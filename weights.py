"""Turn the enriched play history into weighted preference signals.

    python3 weights.py [--half-life YEARS] [--today YYYY-MM-DD] [--top N]

Not every played game is an equal endorsement. Each game gets a weight from
three components, then those weights are pooled per tag into a taste profile.

  engagement  log-scaled hours - 3000h is a stronger signal than 100h, but not
              30x stronger, and log keeps one outlier from owning the profile
  completion  achievement completion, a proxy for "finished it" vs "bounced"
  recency     exponential decay on last-played. The 12-year default assumes
              taste is fairly stable; drop it to ~5 to chase recent taste

Tag affinity is weighted hours pooled per tag, then damped by how common the
tag is across your played library - otherwise "Singleplayer" and "Action" win
by sheer ubiquity and tell you nothing.
"""

import argparse
import collections
import csv
import datetime as dt
import json
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
IN_CSV = os.path.join(OUTPUT, "steam_played_enriched.csv")
OUT_GAMES = os.path.join(OUTPUT, "steam_played_weighted.csv")
OUT_TAGS = os.path.join(OUTPUT, "taste_profile.csv")
OUT_FAMILY = os.path.join(OUTPUT, "taste_profile_family.csv")
OUT_SHELVED = os.path.join(OUTPUT, "shelved_backlog.csv")


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--input", default=IN_CSV)
    p.add_argument("--half-life", type=float, default=12.0,
                   help="years until a game's recency weight halves "
                        "(default 12; lower favours recent taste)")
    p.add_argument("--completion-boost", type=float, default=0.35,
                   help="extra weight at 100%% completion (default 0.35)")
    p.add_argument("--today", default=dt.date.today().isoformat())
    p.add_argument("--top", type=int, default=30)
    p.add_argument("--family-half-life", type=float, default=4.0,
                   help="recency half-life for the family profile only "
                        "(default 4 - children's tastes turn over fast)")
    p.add_argument("--social-scale", type=float, default=1.0,
                   help="scale every stored social factor (1.0 = as marked, "
                        "0 = drop social games from the tag profile entirely)")
    return p.parse_args()


def num(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def engagement(hours, peak):
    """Log-scaled hours in 0..1. Diminishing returns above the long tail."""
    if hours <= 0 or peak <= 0:
        return 0.0
    return math.log1p(hours) / math.log1p(peak)


def recency(last_played, today, half_life):
    """Exponential decay. Undated games sit at the floor rather than at zero,
    since 'no date recorded' is missing data, not evidence of disinterest."""
    if not last_played:
        return 0.35
    try:
        d = dt.date.fromisoformat(last_played)
    except ValueError:
        return 0.35
    years = max(0.0, (today - d).days / 365.25)
    return 0.5 ** (years / half_life)


def completion(row, boost):
    """Only meaningful where the game actually has achievements."""
    if not row.get("Achievements Total", "").isdigit():
        return 1.0
    return 1.0 + boost * (num(row.get("Completion %")) / 100.0)


def score_games(rows, args, gprefs=None):
    today = dt.date.fromisoformat(args.today)
    def played(r):
        return num(r.get("Adjusted Hours")) or num(r["Hours Played"])
    peak = max(played(r) for r in rows)
    for r in rows:
        hours = played(r)
        e = engagement(hours, peak)
        c = completion(r, args.completion_boost)
        rec = recency(r.get("Last Played", ""), today, args.half_life)
        r["Engagement"] = f"{e:.4f}"
        r["Completion Mult"] = f"{c:.3f}"
        r["Recency"] = f"{rec:.4f}"
        m = (gprefs or {}).get(r["AppID"], 1.0)
        r["Declared"] = f"{m:g}x" if m != 1.0 else ""
        r["Weight"] = f"{e * c * rec * m:.4f}"
    rows.sort(key=lambda r: -num(r["Weight"]))
    return rows


def tag_profile(rows, field="Tags", min_games=3, baseline=None):
    """Pool weight per tag and measure engagement concentration.

    Two numbers, because they answer different questions:

      Affinity  total weight behind a tag, damped by sqrt(game count). "How
                much of my playing does this tag account for?" Ubiquitous tags
                still rank high here, which is correct but not very telling.

      Lift      the tag's share of total weight divided by its share of games,
                measured against `baseline` when given. A small, homogeneous
                corpus (couch co-op) must be compared to the whole library, or
                its defining tags look ubiquitous and score ~1.0 - exactly the
                tags that characterise it get neutralised.
                Above 1.0 means games with this tag pull more engagement than
                their headcount would predict - that is the taste signal.
                "Action" is on 179 games so it cannot have high lift; a tag
                that is rare but always played hard will.

    Lift needs a floor on game count or single-game tags dominate on noise.
    """
    weight, hours, count = (collections.defaultdict(float),
                            collections.defaultdict(float),
                            collections.Counter())
    for r in rows:
        # Social games are real play but weak taste evidence, so they are
        # discounted here while keeping full weight in the games list.
        factor = num(r.get("Social Factor"), 1.0)
        for t in [t.strip() for t in (r.get(field) or "").split(";") if t.strip()]:
            weight[t] += num(r["Weight"]) * factor
            hours[t] += num(r.get("Adjusted Hours")) or num(r["Hours Played"])
            count[t] += 1

    total_w = sum(num(r["Weight"]) * num(r.get("Social Factor"), 1.0)
                  for r in rows) or 1.0
    total_n = len(rows) or 1
    base_n = None
    if baseline is not None:
        base_n = collections.Counter()
        for r in baseline:
            for t in [x.strip() for x in (r.get(field) or "").split(";") if x.strip()]:
                base_n[t] += 1
        base_total = len(baseline) or 1
    out = []
    for t, w in weight.items():
        share_w = w / total_w
        if base_n is not None:
            # How much more common is this tag here than in the library at large?
            # Weighted, so a tag the kids outgrew decays like anything else.
            share_n = (base_n.get(t, 0) / base_total) or (1 / base_total)
            share_w = w / total_w
        else:
            share_n = count[t] / total_n
        out.append({"Tag": t,
                    "Affinity": w / math.sqrt(count[t]),
                    "Lift": share_w / share_n if share_n else 0.0,
                    "Raw Weight": w, "Games": count[t], "Hours": hours[t]})
    out.sort(key=lambda d: -d["Affinity"])
    return out, min_games


def apply_prefs(tags, prefs):
    """Declared preferences scale the measured numbers; both are kept visible so
    a boosted tag never masquerades as a purely data-driven finding."""
    for t in tags:
        m = prefs.get(t["Tag"], 1.0)
        t["Declared"] = f"{m:g}x" if m != 1.0 else ""
        t["Adj Lift"] = t["Lift"] * m
        t["Adj Affinity"] = t["Affinity"] * m
    return tags


def main():
    args = parse_args()
    try:
        with open(args.input, encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
    except OSError:
        sys.exit(f"Cannot read {args.input}. Run enrich.py first.")
    if not rows:
        sys.exit("No rows.")

    try:
        raw = json.load(open(os.path.join(DATA, "triage.json"))).get("social", {})
    except (OSError, ValueError):
        raw = {}
    social = {a: 0.15 for a in raw} if isinstance(raw, list) else raw
    try:
        family = set(json.load(open(os.path.join(DATA, "triage.json")))
                     .get("family", []))
    except (OSError, ValueError):
        family = set()
    for r in rows:
        r["Family"] = "yes" if r["AppID"] in family else ""
        f = social.get(r["AppID"], 1.0)
        # Scaling toward 0 pulls marked games further out of the tag profile.
        r["Social Factor"] = f"{f * args.social_scale if f < 1 else 1.0:g}"
    st = {}
    try:
        st = json.load(open(os.path.join(DATA, "triage.json")))
    except (OSError, ValueError):
        pass
    gprefs = st.get("game_prefs", {})
    shelf = set(st.get("shelved", []))
    for r in rows:
        r["Shelved"] = "yes" if r["AppID"] in shelf else ""
    rows = score_games(rows, args, gprefs)
    with open(OUT_GAMES, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    try:
        prefs = json.load(open(os.path.join(DATA, "triage.json"))).get("tag_prefs", {})
    except (OSError, ValueError):
        prefs = {}
    tags, min_games = tag_profile(rows)
    tags = apply_prefs(tags, prefs)
    fam_rows = [r for r in rows if r.get("Family") == "yes"]
    with open(OUT_TAGS, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, ["Tag", "Adj Lift", "Lift", "Declared", "Adj Affinity",
                               "Affinity", "Raw Weight", "Games", "Hours"],
                           extrasaction="ignore")
        w.writeheader()
        for t in tags:
            w.writerow({**t, "Affinity": f"{t['Affinity']:.4f}",
                        "Lift": f"{t['Lift']:.3f}",
                        "Adj Lift": f"{t['Adj Lift']:.3f}",
                        "Adj Affinity": f"{t['Adj Affinity']:.4f}",
                        "Raw Weight": f"{t['Raw Weight']:.4f}",
                        "Hours": f"{t['Hours']:.1f}"})

    if fam_rows:
        # Rescore the family games on their own, faster clock: children's tastes
        # turn over far quicker than an adult's, so a game they have outgrown
        # should decay out of the profile.
        fam_args = argparse.Namespace(**vars(args))
        fam_args.half_life = args.family_half_life
        fam_scored = score_games([dict(r) for r in fam_rows], fam_args, gprefs)
        fam_tags, _ = tag_profile(fam_scored, min_games=2, baseline=rows)
        fam_tags = apply_prefs(fam_tags, prefs)
        with open(OUT_FAMILY, "w", newline="", encoding="utf-8") as f:
            wr = csv.DictWriter(f, ["Tag", "Adj Lift", "Lift", "Declared",
                                    "Affinity", "Raw Weight", "Games", "Hours"],
                                extrasaction="ignore")
            wr.writeheader()
            for t in fam_tags:
                wr.writerow({**t, "Affinity": f"{t['Affinity']:.4f}",
                             "Lift": f"{t['Lift']:.3f}",
                             "Adj Lift": f"{t['Adj Lift']:.3f}",
                             "Raw Weight": f"{t['Raw Weight']:.4f}",
                             "Hours": f"{t['Hours']:.1f}"})
        print(f"{len(fam_rows)} family games -> {os.path.basename(OUT_FAMILY)}"
              f"  (half-life {args.family_half_life:g}y)")
        print("  top family tags: " + ", ".join(
            f'{t["Tag"]} ({t["Games"]}g)' for t in
            sorted([t for t in fam_tags if t["Games"] >= 2],
                   key=lambda d: -d["Raw Weight"])[:12]) + "\n")
    print(f"{len(rows)} games -> {os.path.basename(OUT_GAMES)}"
          f"  ({len(social)} social-marked)")
    print(f"{len(tags)} tags  -> {os.path.basename(OUT_TAGS)}\n")
    print(f"top {args.top} games by weight:")
    for r in rows[:args.top]:
        print(f'  {r["Weight"]:>6}  {num(r["Hours Played"]):>7.1f}h  '
              f'{(r.get("Last Played") or "-")[:7]:8}  {r["Game"][:42]}')
    print(f"\ntop {args.top} tags by affinity (volume):")
    for t in tags[:args.top]:
        print(f'  {t["Affinity"]:>7.3f}  {t["Games"]:>3}g  {t["Hours"]:>7.0f}h  {t["Tag"]}')

    strong = [t for t in tags if t["Games"] >= min_games]
    strong.sort(key=lambda d: -d["Adj Lift"])
    print(f"\ntop {args.top} tags by LIFT (>={min_games} games) - the taste signal:")
    for t in strong[:args.top]:
        mark = f'  <- declared {t["Declared"]}' if t["Declared"] else ""
        print(f'  {t["Adj Lift"]:>6.2f}x  {t["Games"]:>3}g  {t["Hours"]:>7.0f}h  '
              f'{t["Tag"]}{mark}')


if __name__ == "__main__":
    main()
