"""Build the candidate pool for recommend.py: everything you own but have not
played, from every platform this project knows about.

    python3 build_backlog.py

Two sources of candidates:
  - Steam games you own that are not in the played list (the original,
    Steam-only backlog).
  - Non-Steam games (GOG, Epic, EA, Ubisoft, ...) with no recorded playtime, resolved by
    resolve_steam_ids.py to a Steam AppID purely so enrich.py can fetch tags
    for them. A resolved AppID does not mean you own it on Steam - it means a
    Steam listing exists to borrow tag data from.

Run resolve_steam_ids.py first if you want the second source included; this
runs fine without it; it just falls back to Steam-only, as before.
"""

import csv
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
OUTPUT = os.path.join(HERE, "output")
os.makedirs(DATA, exist_ok=True)
os.makedirs(OUTPUT, exist_ok=True)

LIB = os.path.join(DATA, "steam_library.csv")
PLAYED = os.path.join(OUTPUT, "steam_recommendation_input.csv")
UNIFIED = os.path.join(OUTPUT, "unified_library.csv")
RESOLUTIONS = os.path.join(DATA, "steam_id_resolutions.json")
OUT = os.path.join(OUTPUT, "steam_backlog_candidates.csv")

COLS = ["Game", "AppID", "Hours Played", "Last Played",
        "Achievements Unlocked", "Achievements Total", "Completion %", "Source"]


def main():
    played_ids = set()
    try:
        played_ids = {r["AppID"] for r in csv.DictReader(open(PLAYED, encoding="utf-8"))}
    except OSError:
        pass

    rows = []
    lib = list(csv.DictReader(open(LIB, encoding="utf-8")))
    for r in lib:
        if r["AppID"] in played_ids:
            continue
        rows.append({"Game": r["Game"], "AppID": r["AppID"],
                     "Hours Played": r["Hours Played"], "Last Played": r["Last Played"],
                     "Achievements Unlocked": r["Achievements Unlocked"],
                     "Achievements Total": r["Achievements Total"],
                     "Completion %": r["Completion %"], "Source": "Steam"})
    steam_n = len(rows)

    cross_n = 0
    try:
        res = json.load(open(RESOLUTIONS))["resolved"]
        uni = {u["Title"]: u for u in csv.DictReader(open(UNIFIED, encoding="utf-8"))}
    except (OSError, ValueError, KeyError):
        res, uni = {}, {}

    seen_appids = {r["AppID"] for r in rows}
    for title, info in res.items():
        appid = str(info["appid"])
        if appid in played_ids or appid in seen_appids:
            continue
        u = uni.get(title, {})
        try:
            hours = float(u.get("Hours Played") or 0)
        except ValueError:
            hours = 0
        if hours > 0:
            continue          # has real playtime - belongs in the played list, not here
        rows.append({"Game": title, "AppID": appid, "Hours Played": "0",
                     "Last Played": "", "Achievements Unlocked": "",
                     "Achievements Total": "", "Completion %": "",
                     "Source": u.get("Platforms", "?")})
        seen_appids.add(appid)
        cross_n += 1

    rows.sort(key=lambda r: r["Game"].lower())
    with open(OUT, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, COLS)
        w.writeheader()
        w.writerows(rows)

    print(f"{len(rows)} candidates -> {os.path.relpath(OUT, HERE)}")
    print(f"  Steam (unplayed)              : {steam_n}")
    print(f"  Other platforms (unplayed)    : {cross_n}")
    if not res:
        print("  (run resolve_steam_ids.py to include GOG/Epic candidates)")
    print("\nNext: python3 enrich.py --input output/steam_backlog_candidates.csv "
          "--out output/steam_backlog_enriched.csv")


if __name__ == "__main__":
    main()
