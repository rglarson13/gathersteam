"""Resolve non-Steam games to a Steam AppID, purely so their tags can be
fetched. This does NOT mean you own them on Steam - it means a Steam listing
exists that enrich.py can borrow tag/genre data from.

    python3 resolve_steam_ids.py              # resolve + write derived files
    python3 resolve_steam_ids.py --refresh     # re-fetch Steam's app list
    python3 resolve_steam_ids.py --set "Title" 123456   # fix a bad match
    python3 resolve_steam_ids.py --reject "Title"       # mark unresolvable

Matching is EXACT normalized-title only against Steam's full ~250k-app
catalog - no fuzzy matching here. Fuzzy matching worked for unify.py's ~3,500
titles; against a quarter million, it is both slow and far more likely to
attach the wrong game's tags to your real playtime, which would quietly
corrupt the taste profile rather than just look odd. If several Steam apps
share an exact title, it's flagged ambiguous rather than guessed at.

Writes:
  data/steam_applist_cache.json      Steam's full catalog (id, name), cached
  data/steam_id_resolutions.json     title -> resolved appid + your overrides
  data/cross_platform_played.csv     GOG games with real hours, resolved ->
                                      manual_additions.csv format, picked up
                                      by build_reclist.py automatically
  output/cross_platform_ambiguous.csv   titles with 2+ Steam matches
  output/cross_platform_unresolved.csv  titles with no Steam listing at all
"""

import argparse
import csv
import json
import os
import re
import sys
import time
from collections import defaultdict

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
OUTPUT = os.path.join(HERE, "output")
os.makedirs(DATA, exist_ok=True)
os.makedirs(OUTPUT, exist_ok=True)

UNIFIED = os.path.join(OUTPUT, "unified_library.csv")
APPLIST_CACHE = os.path.join(DATA, "steam_applist_cache.json")
RESOLUTIONS = os.path.join(DATA, "steam_id_resolutions.json")
CROSS_PLAYED = os.path.join(DATA, "cross_platform_played.csv")
AMBIGUOUS = os.path.join(OUTPUT, "cross_platform_ambiguous.csv")
UNRESOLVED = os.path.join(OUTPUT, "cross_platform_unresolved.csv")

EDITION_WORDS = (r"\b(goty|game of the year|definitive|enhanced|complete|"
                 r"remastered|remaster|deluxe|ultimate|gold|special|extended|"
                 r"director'?s cut|anniversary|collection|edition|hd|the)\b")


def norm(title):
    t = title.lower().replace("™", "").replace("®", "")
    t = t.replace("&", "and").replace("’", "'")
    t = re.sub(EDITION_WORDS, " ", t)
    t = re.sub(r"[^a-z0-9 ]", " ", t)
    return " ".join(t.split())


def load_api_key():
    """Same tiny .env reader gathersteam.py uses. Real env vars win."""
    if os.environ.get("STEAM_API_KEY"):
        return os.environ["STEAM_API_KEY"]
    try:
        for line in open(os.path.join(HERE, ".env")):
            line = line.strip().removeprefix("export ").strip()
            key, sep, val = line.partition("=")
            if sep and key.strip() == "STEAM_API_KEY":
                val = val.strip()
                return val[1:-1] if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'" else val
    except OSError:
        pass
    return None


def fetch_applist(force=False, api_key=None):
    """IStoreService/GetAppList replaced the old public ISteamApps/GetAppList
    at some point - the old endpoint now 404s. This one needs the API key and
    paginates ~50k apps per page via last_appid."""
    if not force and os.path.exists(APPLIST_CACHE):
        return json.load(open(APPLIST_CACHE))
    if not api_key:
        sys.exit("Need STEAM_API_KEY (from .env) to fetch the app list.")
    print("Fetching Steam's full app list (one-time, a few MB, several pages)...")
    apps, last_appid = [], 0
    while True:
        r = requests.get("https://api.steampowered.com/IStoreService/GetAppList/v1/",
                          params={"key": api_key, "max_results": 50000,
                                  "last_appid": last_appid}, timeout=60)
        r.raise_for_status()
        resp = r.json()["response"]
        apps.extend(resp.get("apps", []))
        print(f"  ...{len(apps)} apps so far", end="\r", flush=True)
        if not resp.get("have_more_results"):
            break
        last_appid = resp["last_appid"]
        time.sleep(0.3)
    print()
    json.dump(apps, open(APPLIST_CACHE, "w"))
    print(f"  cached {len(apps)} apps -> {os.path.relpath(APPLIST_CACHE, HERE)}")
    return apps


def build_index(apps):
    """normalized title -> list of appids sharing it."""
    idx = defaultdict(list)
    for a in apps:
        name = (a.get("name") or "").strip()
        if not name:
            continue
        idx[norm(name)].append(a["appid"])
    return idx


def load_json(path, default):
    try:
        return json.load(open(path))
    except (OSError, ValueError):
        return default


def save_json(path, obj):
    tmp = path + ".tmp"
    json.dump(obj, open(tmp, "w"), indent=1)
    os.replace(tmp, path)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--refresh", action="store_true", help="re-fetch the Steam app list")
    p.add_argument("--set", nargs=2, metavar=("TITLE", "APPID"))
    p.add_argument("--reject", metavar="TITLE")
    args = p.parse_args()

    res = load_json(RESOLUTIONS, {"resolved": {}, "rejected": {}})

    if args.set:
        title, appid = args.set
        res["resolved"][title] = {"appid": int(appid), "source": "manual"}
        res["rejected"].pop(title, None)
        save_json(RESOLUTIONS, res)
        print(f'"{title}" -> {appid} (manual)')
        return
    if args.reject:
        res["rejected"][args.reject] = True
        res["resolved"].pop(args.reject, None)
        save_json(RESOLUTIONS, res)
        print(f'"{args.reject}" marked unresolvable')
        return

    try:
        with open(UNIFIED, encoding="utf-8") as f:
            unified = list(csv.DictReader(f))
    except OSError:
        sys.exit("No unified library. Run `python3 unify.py` first.")

    needs_resolution = [r for r in unified if not r.get("Steam ID")]
    print(f"{len(unified)} unified games, {len(needs_resolution)} without a Steam ID")

    apps = fetch_applist(force=args.refresh, api_key=load_api_key())
    idx = build_index(apps)

    ambiguous, unresolved, newly = [], [], 0
    for r in needs_resolution:
        title = r["Title"]
        if title in res["resolved"] or title in res["rejected"]:
            continue
        matches = idx.get(norm(title), [])
        if len(matches) == 1:
            res["resolved"][title] = {"appid": matches[0], "source": "auto"}
            newly += 1
        elif len(matches) > 1:
            ambiguous.append((title, matches))
        else:
            unresolved.append(title)
    save_json(RESOLUTIONS, res)

    with open(AMBIGUOUS, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Title", "Candidate AppIDs", "Fix with"])
        for title, matches in ambiguous:
            w.writerow([title, "; ".join(map(str, matches[:8])),
                        f'python3 resolve_steam_ids.py --set "{title}" <appid>'])

    with open(UNRESOLVED, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Title", "Platforms"])
        for title in unresolved:
            plat = next((r["Platforms"] for r in unified if r["Title"] == title), "")
            w.writerow([title, plat])

    # Cross-platform games with REAL hours and a resolved AppID, not already
    # owned on Steam: these are new played-list entries, same shape as
    # manual_additions.csv, so build_reclist.py needs no new logic to use them.
    by_title = {r["Title"]: r for r in unified}
    played_rows = []
    for title, info in res["resolved"].items():
        r = by_title.get(title)
        if not r or r.get("Steam ID"):
            continue
        try:
            hours = float(r.get("Hours Played") or 0)
        except ValueError:
            hours = 0
        if hours > 0:
            played_rows.append([title, info["appid"], hours, r.get("Last Played", ""),
                                "", "", ""])
    played_rows.sort(key=lambda x: -x[2])

    # Same game under a different AppID (e.g. a VR edition) will not collide
    # in build_reclist.py's AppID-keyed merge, so it would silently double-
    # count that playtime instead of being skipped. Catch it here by title
    # closeness against manual_additions.csv and flag it - do not guess.
    manual_titles = []
    for path in (os.path.join(DATA, "manual_additions.csv"),):
        try:
            manual_titles = [(r["Game"], r["AppID"]) for r in csv.DictReader(open(path))]
        except OSError:
            pass
    likely_dupes = []
    for row in played_rows:
        title, appid = row[0], str(row[1])
        for mtitle, mappid in manual_titles:
            if appid == mappid:
                continue          # exact AppID match: build_reclist.py already dedupes this
            a, b = norm(title), norm(mtitle)
            if a == b or (a in b or b in a):
                likely_dupes.append((title, appid, mtitle, mappid))

    with open(CROSS_PLAYED, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Game", "AppID", "Hours Played", "Last Played",
                    "Achievements Unlocked", "Achievements Total", "Completion %"])
        w.writerows(played_rows)

    if likely_dupes:
        print(f"\n  ** {len(likely_dupes)} likely duplicate(s) under a DIFFERENT AppID - "
              f"probably the same game double-counted: **")
        for title, appid, mtitle, mappid in likely_dupes:
            print(f'    "{title}" ({appid})  looks like  "{mtitle}" ({mappid}) in manual_additions.csv')
        print("    Fix with: python3 resolve_steam_ids.py --reject \"<the one to drop>\"")
        print("    then rerun it. (This file is regenerated each run - never hand-edit it.)")

    print(f"\n  newly resolved   : {newly}")
    print(f"  ambiguous (2+)   : {len(ambiguous)} -> {os.path.relpath(AMBIGUOUS, HERE)}")
    print(f"  unresolved       : {len(unresolved)} -> {os.path.relpath(UNRESOLVED, HERE)}")
    print(f"  cross-platform played (real hours, resolved) : {len(played_rows)}"
          f" -> {os.path.relpath(CROSS_PLAYED, HERE)}")
    if played_rows:
        print("\n  top by hours:")
        for row in played_rows[:8]:
            print(f"    {row[2]:>7.1f}h  {row[0]}")
    print("\nNext: python3 build_reclist.py && python3 enrich.py && "
          "python3 weights.py && python3 recommend.py")


if __name__ == "__main__":
    main()
