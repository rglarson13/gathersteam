"""Assemble the 'actually played' list from playtime, achievements and triage.

    python3 build_reclist.py            # writes steam_recommendation_input.csv
    python3 build_reclist.py --pending  # writes steam_playtime_only.csv (to triage)

Your triage decisions outrank every automatic signal: shared machines mean
unlocked achievements do not always prove *you* played it.
"""
import collections
import csv
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
# data/  = sources and state: expensive or impossible to regenerate.
# output/ = everything derived from them; safe to delete and rebuild.
DATA = os.path.join(HERE, "data")
OUTPUT = os.path.join(HERE, "output")
os.makedirs(DATA, exist_ok=True)
os.makedirs(OUTPUT, exist_ok=True)
LIB = os.path.join(DATA, "steam_library.csv")
STATE = os.path.join(DATA, "triage.json")
# Optional: games Steam no longer reports (GOG, console).
MANUAL = os.path.join(DATA, "manual_additions.csv")


def hrs(r):
    """Adjusted hours where set, else what Steam reports."""
    return float(r.get("Adjusted Hours") or r["Hours Played"])


def unlocked(r):
    v = r["Achievements Unlocked"]
    return int(v) if v.isdigit() else 0


def load():
    lib = list(csv.DictReader(open(LIB, encoding="utf-8")))
    try:
        lib += list(csv.DictReader(open(MANUAL, encoding="utf-8")))
    except OSError:
        pass
    try:
        state = json.load(open(STATE))
    except (OSError, ValueError):
        state = {}
    dec = state.get("decisions", {})
    overrides = state.get("hours", {})
    pct_over = state.get("completion", {})
    for r in lib:
        # Steam's number stays visible; Adjusted Hours is what weighting uses.
        r["Adjusted Hours"] = f'{float(overrides[r["AppID"]]):g}' \
            if r["AppID"] in overrides else r["Hours Played"]
        if r["AppID"] in pct_over:
            # Completed elsewhere: restate unlocked count to match the override.
            pct = float(pct_over[r["AppID"]])
            r["Completion %"] = f"{pct:g}"
            if r["Achievements Total"].isdigit():
                r["Achievements Unlocked"] = str(round(
                    int(r["Achievements Total"]) * pct / 100))
    return lib, dec


def auto_idled(launched):
    """Games farmed for trading cards: the 2016 window, nothing unlocked, and a
    playtime shared with several others. Real play does not repeat to 0.1h."""
    win = [r for r in launched
           if "2016-01" <= r["Last Played"][:7] <= "2016-06" and unlocked(r) == 0]
    dup = collections.Counter(round(hrs(r), 1) for r in win)
    ids = {r["AppID"] for r in win}
    return lambda r: r["AppID"] in ids and dup[round(hrs(r), 1)] >= 3


def classify(lib, dec):
    launched = [r for r in lib if hrs(r) > 0]
    is_idle = auto_idled(launched)
    played, idled = [], []
    for r in launched:
        v = dec.get(r["AppID"])
        if v == "y":
            r["Signal"] = "confirmed by you"; played.append(r)
        elif v == "n":
            idled.append(r)
        elif unlocked(r) > 0:
            r["Signal"] = "unlocked achievements"; played.append(r)
        elif is_idle(r):
            idled.append(r)
        else:
            r["Signal"] = "playtime only"; played.append(r)
    played.sort(key=lambda r: -hrs(r))
    return played, idled


COLS = ["Game", "AppID", "Hours Played", "Adjusted Hours", "Last Played",
        "Achievements Unlocked", "Achievements Total", "Completion %", "Signal"]


def write(path, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, COLS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    print(f"{len(rows):>4} -> {os.path.relpath(path, HERE)}")


def main():
    lib, dec = load()
    played, idled = classify(lib, dec)
    if "--pending" in sys.argv:
        pending = [r for r in played if r["Signal"] == "playtime only"]
        write(os.path.join(OUTPUT, "steam_playtime_only.csv"), pending)
        return
    write(os.path.join(OUTPUT, "steam_recommendation_input.csv"), played)
    for r in idled:
        r.setdefault("Signal", "excluded")
    write(os.path.join(OUTPUT, "steam_not_played.csv"), idled)
    print("\nevidence:", dict(collections.Counter(r["Signal"] for r in played)))


if __name__ == "__main__":
    main()
