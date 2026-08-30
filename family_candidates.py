"""Rank owned games by how likely they are to be couch co-op with the kids.

    python3 family_candidates.py [--top N] [--min-hours H] [--played-only]
                                [--allow-intense]

Draws from every game with playtime - including ones marked "not yours", since
a game your kids played is exactly where "did we play it together?" is unasked.
Ranks on family-shaped tags so the triage list is dense with real candidates
instead of alphabetical noise.

--played-only restricts to games in your played list. Horror, gore and
punishing-difficulty games are dropped by default (--allow-intense keeps them),
and anything with no co-op or multiplayer support at all is skipped: a
single-player game cannot be a couch game.
"""

import argparse
import csv
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
# data/  = sources and state: expensive or impossible to regenerate.
# output/ = everything derived from them; safe to delete and rebuild.
DATA = os.path.join(HERE, "data")
OUTPUT = os.path.join(HERE, "output")
os.makedirs(DATA, exist_ok=True)
os.makedirs(OUTPUT, exist_ok=True)
OUT = os.path.join(OUTPUT, "steam_family_candidates.csv")

# Weighted because they are not equal evidence: "4 Player Local" is nearly
# proof of couch play, "Funny" is only a hint.
SIGNALS = {
    "Local Co-Op": 3.0, "4 Player Local": 3.0, "Local Multiplayer": 3.0,
    "Split Screen": 3.0, "Couch Co-op": 3.0, "Party Game": 2.5,
    "Family Friendly": 2.5, "Co-op Campaign": 2.0, "Co-op": 1.5,
    "Funny": 1.0, "Cute": 1.0, "Colorful": 1.0, "Casual": 0.75,
    "Platformer": 0.75, "Beat 'em up": 1.5, "Arcade": 0.75,
    "Physics": 0.75, "Racing": 0.75, "Cartoony": 1.0, "Comedy": 1.0,
}
CATEGORIES = ("Shared/Split Screen", "Local Co-Op", "Local Multi-Player",
              "Remote Play Together", "Shared/Split Screen Co-op")

# Not kid material, whatever the co-op tags say.
INTENSE = {"Horror", "Survival Horror", "Psychological Horror", "Gore", "Violent",
           "Blood", "Nudity", "Sexual Content", "Mature", "NSFW", "Souls-like",
           "Difficult", "Dark", "Grimdark", "Zombies", "Lovecraftian",
           "Disturbing", "Atmospheric Horror", "War"}
# Some form of playing together must be on offer.
MULTI = ("Multi-player", "Co-op", "PvP", "Online Co-op", "Shared/Split Screen",
         "Local Co-Op", "Local Multi-Player", "Remote Play Together",
         "Cross-Platform Multiplayer")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--top", type=int, default=120)
    p.add_argument("--min-hours", type=float, default=1.0)
    p.add_argument("--played-only", action="store_true")
    p.add_argument("--allow-intense", action="store_true")
    a = p.parse_args()

    st = json.load(open(os.path.join(DATA, "triage.json")))
    already = set(st.get("family", []))
    answered = set(st.get("family_decided", {}))

    keep = None
    if a.played_only:
        keep = {r["AppID"] for r in csv.DictReader(
            open(os.path.join(OUTPUT, "steam_recommendation_input.csv"), encoding="utf-8"))}

    lib = {r["AppID"]: r for r in csv.DictReader(
        open(os.path.join(DATA, "steam_library.csv"), encoding="utf-8"))}
    enr = {}
    for f in ("steam_played_enriched.csv", "steam_backlog_enriched.csv"):
        try:
            for r in csv.DictReader(open(os.path.join(OUTPUT, f), encoding="utf-8")):
                enr[r["AppID"]] = r
        except OSError:
            pass

    rows = []
    for appid, r in lib.items():
        if appid in already or appid in answered:
            continue
        if keep is not None and appid not in keep:
            continue
        if float(r["Hours Played"]) < a.min_hours:
            continue
        e = enr.get(appid, {})
        tags = [t.strip() for t in (e.get("Tags") or "").split(";") if t.strip()]
        cats = e.get("Categories") or ""
        if not a.allow_intense and any(t in INTENSE for t in tags):
            continue
        if not any(m in cats for m in MULTI):
            continue          # single-player only: cannot be a couch game
        score = sum(SIGNALS.get(t, 0.0) for t in tags)
        if any(c in cats for c in CATEGORIES):
            score += 2.0          # Steam itself says local play is supported
        if score <= 0:
            continue
        hit = [t for t in tags if t in SIGNALS]
        rows.append({"Game": r["Game"], "AppID": appid,
                     "Hours Played": r["Hours Played"],
                     "Last Played": r["Last Played"],
                     "Family Score": f"{score:.1f}",
                     "Signals": "; ".join(hit[:6]),
                     "Tags": e.get("Tags", "")})

    rows.sort(key=lambda r: (-float(r["Family Score"]), -float(r["Hours Played"])))
    rows = rows[:a.top]
    with open(OUT, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, ["Game", "AppID", "Hours Played", "Last Played",
                               "Family Score", "Signals", "Tags"])
        w.writeheader()
        w.writerows(rows)
    print(f"{len(rows)} candidates -> {os.path.basename(OUT)}"
          f"  ({len(already)} already marked family)\n")
    for r in rows[:20]:
        print(f'  {r["Family Score"]:>5}  {float(r["Hours Played"]):>6.1f}h  '
              f'{r["Game"][:34]:36} {r["Signals"][:44]}')


if __name__ == "__main__":
    main()
