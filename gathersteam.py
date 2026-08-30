"""Export a Steam library to CSV: playtime, last played, achievement progress.

Usage:
    Put these in a .env file beside this script (or export them as real
    environment variables, which take precedence):

        STEAM_API_KEY=...      # free: https://steamcommunity.com/dev/apikey
        STEAM_ID=76561198...   # Steam64 ID, or a vanity name / profile URL

    Then: python3 gathersteam.py
"""

import concurrent.futures
import csv
import json
import os
import re
import sys
import threading
import time
from datetime import datetime, timezone

import requests


def load_env(filename=".env"):
    """Read KEY=value lines from .env. Real environment variables win."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), filename)
    try:
        with open(path) as f:
            lines = f.readlines()
    except OSError:
        return
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        line = line.removeprefix("export ").strip()
        key, sep, val = line.partition("=")
        if not sep:
            continue
        key, val = key.strip(), val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
            val = val[1:-1]
        os.environ.setdefault(key, val)


load_env()

API_KEY = os.environ.get("STEAM_API_KEY", "")
# Lookup sites export these under varying names; take whichever is set.
# The legacy STEAM_0:... and [U:1:...] forms are skipped - the API wants a
# Steam64 ID or a vanity name.
STEAM_ID = next((os.environ[k] for k in
                 ("STEAM_ID", "STEAMID64", "STEAM_ID64", "CUSTOMURL", "STEAM_VANITY")
                 if os.environ.get(k)), "")
HERE = os.path.dirname(os.path.abspath(__file__))
# data/  = sources and state: expensive or impossible to regenerate.
# output/ = everything derived from them; safe to delete and rebuild.
DATA = os.path.join(HERE, "data")
OUTPUT = os.path.join(HERE, "output")
os.makedirs(DATA, exist_ok=True)
os.makedirs(OUTPUT, exist_ok=True)
OUT_CSV = os.path.join(DATA, "steam_library.csv")
CACHE = os.path.join(DATA, "achievements_cache.json")
WORKERS = 6

_local = threading.local()


def session():
    """One requests.Session per worker thread."""
    s = getattr(_local, "s", None)
    if s is None:
        s = _local.s = requests.Session()
    return s


def get(url, params, tries=4):
    """GET with retries on rate-limits and transient server errors."""
    for attempt in range(tries):
        try:
            r = session().get(url, params=params, timeout=30)
        except requests.RequestException:
            if attempt == tries - 1:
                raise
            time.sleep(2 ** attempt)
            continue
        if r.status_code in (429, 500, 502, 503, 504) and attempt < tries - 1:
            time.sleep(2 ** attempt)
            continue
        return r
    return r


def resolve_steam_id(value):
    """Accept a Steam64 ID, a vanity name, or a full profile URL."""
    value = value.strip().rstrip("/")
    m = re.search(r"/(?:profiles|id)/([^/]+)$", value)
    if m:
        value = m.group(1)
    if re.fullmatch(r"7656\d{13}", value):
        return value
    r = get("https://api.steampowered.com/ISteamUser/ResolveVanityURL/v1/",
            {"key": API_KEY, "vanityurl": value})
    data = r.json().get("response", {})
    if data.get("success") != 1:
        sys.exit(f"Could not resolve '{value}' to a Steam64 ID. Use your numeric ID.")
    return data["steamid"]


def fetch_games(steam_id):
    r = get("https://api.steampowered.com/IPlayerService/GetOwnedGames/v1/",
            {"key": API_KEY, "steamid": steam_id, "include_appinfo": "true",
             "include_played_free_games": "true", "format": "json"})
    if r.status_code == 401:
        sys.exit("401 from Steam - check STEAM_API_KEY.")
    r.raise_for_status()
    games = r.json().get("response", {}).get("games")
    if games is None:
        sys.exit("Steam returned no games. Is 'Game details' set to Public in your "
                 "privacy settings, and is STEAM_ID correct?")
    return games


class Unresolved(Exception):
    """Steam did not give a usable answer; the game's status is still unknown."""


def fetch_achievements(appid, steam_id):
    """Return (unlocked, total), or None if the game confirmably has none.

    GetPlayerAchievements is one call per game but is unambiguous: every
    achievement comes back with an `achieved` flag, so both numbers are exact.
    Raises Unresolved on transient failures so they are not cached as "none".
    """
    r = get("https://api.steampowered.com/ISteamUserStats/GetPlayerAchievements/v1/",
            {"key": API_KEY, "steamid": steam_id, "appid": appid})
    # 400 "Requested app has no stats" and 403 are real answers: no achievements.
    if r.status_code in (400, 403):
        return None
    if r.status_code != 200:
        raise Unresolved(f"HTTP {r.status_code}")
    try:
        body = r.json().get("playerstats", {})
    except ValueError:
        raise Unresolved("malformed JSON")
    if not body.get("success"):
        return None
    items = body.get("achievements") or []
    if not items:
        return None
    return sum(1 for a in items if a.get("achieved")), len(items)


def load_cache():
    try:
        with open(CACHE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def main():
    if not API_KEY or not STEAM_ID:
        missing = [n for n, v in (("STEAM_API_KEY", API_KEY), ("STEAM_ID", STEAM_ID)) if not v]
        sys.exit(f"Missing {' and '.join(missing)} - add to .env or export.")

    steam_id = resolve_steam_id(STEAM_ID)
    games = fetch_games(steam_id)
    print(f"Fetched {len(games)} games for {steam_id}")

    # Cached results survive reruns, so a second pass costs almost nothing.
    cache = load_cache()
    todo = [g["appid"] for g in games if str(g["appid"]) not in cache]
    print(f"Achievements: {len(games) - len(todo)} cached, {len(todo)} to fetch")

    done = 0
    unresolved = []
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=WORKERS) as pool:
            futures = {pool.submit(fetch_achievements, a, steam_id): a for a in todo}
            for fut in concurrent.futures.as_completed(futures):
                appid = futures[fut]
                try:
                    cache[str(appid)] = fut.result()
                except Unresolved:
                    unresolved.append(appid)  # left uncached, so a rerun retries it
                except Exception as e:
                    unresolved.append(appid)
                    print(f"  appid {appid}: {e}", file=sys.stderr)
                done += 1
                if done % 25 == 0 or done == len(todo):
                    print(f"  {done}/{len(todo)}", end="\r", flush=True)
    finally:
        print()
        with open(CACHE, "w") as f:
            json.dump(cache, f)

    with open(OUT_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Game", "AppID", "Hours Played", "Last Played",
                    "Achievements Unlocked", "Achievements Total", "Completion %"])
        for g in sorted(games, key=lambda x: x.get("playtime_forever", 0), reverse=True):
            last = g.get("rtime_last_played") or 0
            last = (datetime.fromtimestamp(last, timezone.utc).strftime("%Y-%m-%d")
                    if last else "")
            # Absent from the cache means Steam never gave an answer (dead or
            # delisted apps 500 forever); a cached None means confirmably none.
            key = str(g["appid"])
            if key not in cache:
                unlocked = total = pct = "unknown"
            elif cache[key] is None:
                unlocked = total = pct = ""
            else:
                unlocked, total = cache[key]
                pct = f"{100 * unlocked / total:.0f}" if total else ""
            w.writerow([g.get("name", f"App {g['appid']}"), g["appid"],
                        f"{g.get('playtime_forever', 0) / 60:.1f}", last,
                        unlocked, total, pct])

    if unresolved:
        print(f"{len(unresolved)} game(s) unresolved after retries "
              f"(left uncached - rerun to retry): {unresolved[:10]}")
    print(f"Done -> {OUT_CSV}")


if __name__ == "__main__":
    main()
