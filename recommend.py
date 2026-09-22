"""Score unplayed games against your taste profile.

    python3 recommend.py [--top N] [--family] [--min-tags N] [--min-score X]
    python3 recommend.py --input other_enriched.csv --out picks.csv

Scoring: each candidate's tags are looked up in taste_profile.csv and averaged.
SteamSpy returns tags in vote order, so earlier tags describe the game better
and are weighted more heavily. A tag you have no history with counts as neutral
rather than as a negative - absence of evidence is not evidence of dislike.

A score of 1.0 is "typical of your library". Above ~1.3 means the tags skew
toward things you reliably play.
"""

import argparse
import csv
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
# data/  = sources and state: expensive or impossible to regenerate.
# output/ = everything derived from them; safe to delete and rebuild.
DATA = os.path.join(HERE, "data")
OUTPUT = os.path.join(HERE, "output")
os.makedirs(DATA, exist_ok=True)
os.makedirs(OUTPUT, exist_ok=True)
PROFILE = os.path.join(OUTPUT, "taste_profile.csv")
FAMILY = os.path.join(OUTPUT, "taste_profile_family.csv")
CANDIDATES = os.path.join(OUTPUT, "steam_backlog_enriched.csv")
OUT = os.path.join(OUTPUT, "recommendations.csv")


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--input", default=CANDIDATES)
    p.add_argument("--out", default=None)
    p.add_argument("--profile", default=None,
                   help="taste profile to score against (default: solo)")
    p.add_argument("--family", action="store_true",
                   help="score against the couch co-op profile instead")
    p.add_argument("--top", type=int, default=40)
    p.add_argument("--min-tags", type=int, default=3,
                   help="skip candidates with fewer tags than this (default 3)")
    p.add_argument("--min-score", type=float, default=0.0)
    p.add_argument("--min-metacritic", type=int, default=0)
    p.add_argument("--decay", type=float, default=0.85,
                   help="per-position tag weight decay (default 0.85)")
    return p.parse_args()


def num(v, d=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


def load_profile(path, min_games=2):
    """tag -> adjusted lift. Thin tags are dropped: a 1.9x lift off two games is
    noise, and letting it drive recommendations amplifies that noise."""
    prof = {}
    with open(path, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if int(r["Games"]) < min_games:
                continue
            prof[r["Tag"]] = num(r.get("Adj Lift") or r.get("Lift"), 1.0)
    return prof


def score(tags, prof, decay):
    """Position-weighted mean lift across a candidate's tags."""
    if not tags:
        return 0.0, 0, []
    total = wsum = 0.0
    hits = []
    for i, t in enumerate(tags):
        w = decay ** i
        lift = prof.get(t)
        if lift is not None:
            hits.append((t, lift))
        total += w * (lift if lift is not None else 1.0)
        wsum += w
    return (total / wsum if wsum else 0.0), len(hits), hits


def main():
    args = parse_args()
    path = args.profile or (FAMILY if args.family else PROFILE)
    # Keep the two recommenders in separate files; --family used to clobber the
    # solo list when --out was omitted.
    out = args.out or (os.path.join(OUTPUT, "recommendations_family.csv")
                       if args.family else OUT)
    try:
        prof = load_profile(path)
    except OSError:
        sys.exit(f"Cannot read {path}. Run weights.py first.")
    try:
        with open(args.input, encoding="utf-8") as f:
            cand = list(csv.DictReader(f))
    except OSError:
        sys.exit(f"Cannot read {args.input}. Run enrich.py on the candidates first.")

    scored = []
    for r in cand:
        tags = [t.strip() for t in (r.get("Tags") or "").split(";") if t.strip()]
        if len(tags) < args.min_tags:
            continue
        mc = num(r.get("Metacritic"))
        if args.min_metacritic and mc < args.min_metacritic:
            continue
        s, n_hit, hits = score(tags, prof, args.decay)
        if s < args.min_score:
            continue
        hits.sort(key=lambda kv: -kv[1])
        r["Score"] = f"{s:.4f}"
        r["Matched Tags"] = n_hit
        r["Why"] = "; ".join(f"{t} {l:.1f}x" for t, l in hits[:4])
        scored.append(r)

    scored.sort(key=lambda r: -num(r["Score"]))
    cols = ["Game", "Source", "AppID", "Score", "Matched Tags", "Why", "Metacritic",
            "Release Date", "Tags", "Genres", "Developer"]
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(scored)

    label = "family co-op" if args.family else "solo"
    print(f"{len(scored)} scored against the {label} profile "
          f"({len(prof)} tags) -> {os.path.basename(out)}\n")
    for r in scored[:args.top]:
        mc = f' MC{r["Metacritic"]}' if r.get("Metacritic") else ""
        own = f' [{r["Source"]}]' if r.get("Source") else ""
        print(f'  {r["Score"]}  {r["Game"][:38]:40}{mc}{own}')
        print(f'          {r["Why"]}')


if __name__ == "__main__":
    main()
