"""Add genre/tag/developer/release features to the played-games CSV.

    python3 enrich.py [--input FILE] [--out FILE] [--limit N] [--no-fetch]

Two sources per game:
  store.steampowered.com/api/appdetails  -> genres, categories, devs, release
  steamspy.com/api.php                   -> user tags (the better features)

Both are rate-limited, so this is slow and deliberately polite. Every result is
cached to data/enrich_cache.json, so interrupting and rerunning costs nothing.

--no-fetch skips the network entirely and writes the CSV from whatever is
already cached. Safe to run while a fetch is in progress: it never writes the
cache, so it cannot race the running job.
"""

import csv
import json
import os
import sys
import time

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
# data/  = sources and state: expensive or impossible to regenerate.
# output/ = everything derived from them; safe to delete and rebuild.
DATA = os.path.join(HERE, "data")
OUTPUT = os.path.join(HERE, "output")
os.makedirs(DATA, exist_ok=True)
os.makedirs(OUTPUT, exist_ok=True)
CACHE = os.path.join(DATA, "enrich_cache.json")
IN_CSV = os.path.join(OUTPUT, "steam_recommendation_input.csv")
OUT_CSV = os.path.join(OUTPUT, "steam_played_enriched.csv")

STORE = "https://store.steampowered.com/api/appdetails"
SPY = "https://steamspy.com/api.php"
TOP_TAGS = 8          # user tags kept per game, most-voted first
PAUSE = 1.6           # seconds between calls to either API

session = requests.Session()
session.headers["User-Agent"] = "gathersteam/1.0 (personal library export)"


def fetch(url, params, tries=4):
    """GET with backoff. Returns None rather than raising, so one bad app
    cannot abort a 400-game run."""
    delay = PAUSE
    for attempt in range(tries):
        try:
            r = session.get(url, params=params, timeout=30)
        except requests.RequestException:
            time.sleep(delay); delay *= 2
            continue
        if r.status_code == 429 or r.status_code >= 500:
            time.sleep(max(delay, 10)); delay *= 2
            continue
        if r.status_code != 200:
            return None
        try:
            return r.json()
        except ValueError:
            return None
    return None


def from_store(appid):
    data = fetch(STORE, {"appids": appid, "l": "english"})
    entry = (data or {}).get(str(appid)) or {}
    if not entry.get("success"):
        return {}
    d = entry.get("data") or {}
    return {
        "genres": [g["description"] for g in d.get("genres", [])],
        "categories": [c["description"] for c in d.get("categories", [])],
        "developers": d.get("developers") or [],
        "publishers": d.get("publishers") or [],
        "release": (d.get("release_date") or {}).get("date", ""),
        "metacritic": (d.get("metacritic") or {}).get("score", ""),
    }


def from_spy(appid):
    d = fetch(SPY, {"request": "appdetails", "appid": appid})
    if not isinstance(d, dict):
        return {}
    tags = d.get("tags") or {}
    if isinstance(tags, dict):
        ranked = sorted(tags.items(), key=lambda kv: -kv[1])[:TOP_TAGS]
        tags = [k for k, _ in ranked]
    elif not isinstance(tags, list):
        tags = []
    return {"tags": tags[:TOP_TAGS], "spy_owners": d.get("owners", "")}


def main():
    args = sys.argv[1:]
    src = args[args.index("--input") + 1] if "--input" in args else IN_CSV
    dst = args[args.index("--out") + 1] if "--out" in args else OUT_CSV
    limit = int(args[args.index("--limit") + 1]) if "--limit" in args else None
    no_fetch = "--no-fetch" in args

    with open(src, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    if limit:
        rows = rows[:limit]

    try:
        cache = json.load(open(CACHE))
    except (OSError, ValueError):
        cache = {}

    todo = [] if no_fetch else [r for r in rows if r["AppID"] not in cache]
    if no_fetch:
        have = sum(1 for r in rows if r["AppID"] in cache)
        print(f"--no-fetch: writing from cache ({have}/{len(rows)} have data)")
    print(f"{len(rows)} games | {len(rows)-len(todo)} cached | {len(todo)} to fetch")
    if todo:
        print(f"~{len(todo)*PAUSE*2/60:.0f} min at {PAUSE}s per call", flush=True)

    for i, r in enumerate(todo, 1):
        appid = r["AppID"]
        info = from_store(appid)
        time.sleep(PAUSE)
        info.update(from_spy(appid))
        time.sleep(PAUSE)
        cache[appid] = info
        if i % 10 == 0 or i == len(todo):
            json.dump(cache, open(CACHE, "w"))
            print(f"  {i}/{len(todo)}  {r['Game'][:40]}", flush=True)
    if not no_fetch:
        json.dump(cache, open(CACHE, "w"))

    extra = ["Tags", "Genres", "Categories", "Developer", "Publisher",
             "Release Date", "Metacritic"]
    with open(dst, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(list(rows[0].keys()) + extra)
        miss = 0
        for r in rows:
            e = cache.get(r["AppID"]) or {}
            if not e:
                miss += 1
            w.writerow(list(r.values()) + [
                "; ".join(e.get("tags", [])),
                "; ".join(e.get("genres", [])),
                "; ".join(e.get("categories", [])),
                "; ".join(e.get("developers", [])),
                "; ".join(e.get("publishers", [])),
                e.get("release", ""),
                e.get("metacritic", ""),
            ])
    print(f"\nDone -> {dst}" + (f"  ({miss} with no data)" if miss else ""))


if __name__ == "__main__":
    main()
