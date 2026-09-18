"""Build one canonical game registry across every platform you export.

    python3 unify.py                # match + write the unified library
    python3 unify.py --review       # resolve uncertain matches, y/n style

Each platform's exporter writes its own CSV in its own shape. This reads all
of them, normalizes titles, and groups rows that are the same game. Adding a
new platform later (Ubisoft, EA, Blizzard, a PS/Switch export you type by
hand) means adding one entry to SOURCES below - nothing else changes.

Platforms with no exporter can skip even that: drop a plain-text list, one
title per line, at input/<platform>_manual_list.txt (e.g. ea_manual_list.txt,
battle-net_manual_list.txt) and it is picked up automatically. Blank lines
and lines starting with # are ignored. Such lists carry no hours or IDs, so
those games count as owned-but-unplayed unless you say otherwise.

Matching has three tiers:
  exact    normalized titles are identical               -> auto-merged
  strong   very close (fuzzy ratio >= STRONG)             -> auto-merged
  uncertain  close but not strong (>= REVIEW)             -> queued for you

Confirmed and rejected matches are remembered in data/platform_links.json, so
--review only ever asks about a pair once, and reruns after new platforms are
added only ask about genuinely new ambiguity.
"""

import argparse
import csv
import difflib
import json
import os
import re
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
OUTPUT = os.path.join(HERE, "output")
os.makedirs(DATA, exist_ok=True)
os.makedirs(OUTPUT, exist_ok=True)

LINKS = os.path.join(DATA, "platform_links.json")
OUT_UNIFIED = os.path.join(OUTPUT, "unified_library.csv")
OUT_REVIEW = os.path.join(OUTPUT, "platform_match_review.csv")

STRONG = 0.93   # auto-merge above this
REVIEW = 0.82   # queue between REVIEW and STRONG; below is "different games"

# One entry per platform. `hours`/`last_played` are optional - leave None if
# the exporter cannot provide them (Epic's catalog cache has no playtime).
# To add a platform: write an exporter that produces a CSV with a title
# column and (ideally) a stable id column, then add a line here.
SOURCES = {
    "Steam": dict(path=os.path.join(DATA, "steam_library.csv"),
                  title="Game", id="AppID",
                  hours="Hours Played", last_played="Last Played"),
    "Epic":  dict(path=os.path.join(OUTPUT, "epic_games.csv"),
                  title="title", id="epic_namespace",
                  hours=None, last_played=None),
    "GOG":   dict(path=os.path.join(OUTPUT, "gog_games.csv"),
                  title="title", id="gog_id",
                  hours="playtime_hours", last_played="last_played"),
}

EDITION_WORDS = (r"\b(goty|game of the year|definitive|enhanced|complete|"
                 r"remastered|remaster|deluxe|ultimate|gold|special|extended|"
                 r"director'?s cut|anniversary|collection|edition|hd|the)\b")


def norm(title):
    t = title.lower().replace("™", "").replace("®", "")
    t = t.replace("&", "and").replace("’", "'")
    t = re.sub(EDITION_WORDS, " ", t)
    t = re.sub(r"[^a-z0-9 ]", " ", t)
    return " ".join(t.split())


INPUT = os.path.join(HERE, "input")
MANUAL_SUFFIX = "_manual_list.txt"
# Filename stem -> display name, where title-casing the stem would be wrong.
PLATFORM_NAMES = {"ea": "EA", "battle-net": "Battle.net", "battlenet": "Battle.net",
                  "gog": "GOG", "ps4": "PS4", "ps5": "PS5", "psn": "PSN",
                  "switch": "Switch", "nintendo-switch": "Switch", "xbox": "Xbox"}


def manual_list_sources():
    """platform display name -> path, for every input/*_manual_list.txt."""
    found = {}
    try:
        files = sorted(os.listdir(INPUT))
    except OSError:
        return found
    for fn in files:
        if not fn.endswith(MANUAL_SUFFIX):
            continue
        stem = fn[:-len(MANUAL_SUFFIX)].lower()
        name = PLATFORM_NAMES.get(stem, stem.replace("-", " ").replace("_", " ").title())
        found[name] = os.path.join(INPUT, fn)
    return found


def load_manual_list(name, path):
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            title = line.strip()
            if not title or title.startswith("#"):
                continue
            out.append({"platform": name, "title": title, "key": norm(title),
                        "id": "", "hours": "", "last_played": ""})
    return out


def load_source(name, cfg):
    try:
        with open(cfg["path"], encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
    except OSError:
        return []
    out = []
    for r in rows:
        title = (r.get(cfg["title"]) or "").strip()
        if not title:
            continue
        out.append({
            "platform": name,
            "title": title,
            "key": norm(title),
            "id": r.get(cfg["id"], "") if cfg["id"] else "",
            "hours": r.get(cfg["hours"]) if cfg["hours"] else "",
            "last_played": r.get(cfg["last_played"]) if cfg["last_played"] else "",
        })
    return out


def load_links():
    try:
        return json.load(open(LINKS))
    except (OSError, ValueError):
        return {"confirmed": {}, "rejected": {}}


def save_links(links):
    tmp = LINKS + ".tmp"
    json.dump(links, open(tmp, "w"), indent=1)
    os.replace(tmp, LINKS)


def pair_key(a, b):
    """Order-independent id for a (platform, title) pair."""
    return " :: ".join(sorted([f"{a['platform']}|{a['title']}", f"{b['platform']}|{b['title']}"]))


def build_groups(entries, links):
    """Union-find over entries, merging on exact key, a confirmed link, or a
    strong fuzzy ratio. Returns (groups, pending) where pending is the
    uncertain pairs still needing a decision."""
    parent = list(range(len(entries)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i, j):
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[ri] = rj

    by_key = defaultdict(list)
    for i, e in enumerate(entries):
        by_key[e["key"]].append(i)
    for idxs in by_key.values():
        for i in idxs[1:]:
            union(idxs[0], i)          # exact normalized match

    pending = []
    keys = list(by_key.keys())
    for gi, ka in enumerate(keys):
        i = by_key[ka][0]
        # difflib over all keys is O(n^2) on ~4000 titles - fine for a
        # library-sized dataset, and this only runs when you add platforms.
        close = difflib.get_close_matches(ka, keys[gi + 1:], n=8, cutoff=REVIEW)
        for kb in close:
            j = by_key[kb][0]
            if find(i) == find(j):
                continue
            ratio = difflib.SequenceMatcher(None, ka, kb).ratio()
            # If both titles appear on a common platform, you own them there as
            # two distinct items (sequels, editions), so they cannot be one
            # game and are never worth asking about. Exact-key merges above
            # still collapse true same-platform duplicates.
            plats_a = {entries[x]["platform"] for x in by_key[ka]}
            plats_b = {entries[x]["platform"] for x in by_key[kb]}
            if plats_a & plats_b:
                continue
            pk = pair_key(entries[i], entries[j])
            if pk in links["rejected"]:
                continue
            if pk in links["confirmed"] or ratio >= STRONG:
                union(i, j)
            else:
                pending.append((entries[i], entries[j], ratio, pk))

    groups = defaultdict(list)
    for i in range(len(entries)):
        groups[find(i)].append(entries[i])
    return list(groups.values()), pending


def best_hours(vals):
    nums = [float(v) for v in vals if v not in (None, "")]
    return round(sum(nums), 1) if nums else ""


def best_date(vals):
    dates = sorted(v for v in vals if v)
    return dates[-1] if dates else ""


def write_unified(groups):
    platform_names = list(SOURCES)
    cols = ["Title", "Platforms"] + [f"{p} ID" for p in platform_names] + \
           ["Hours Played", "Last Played"]
    rows = []
    for g in groups:
        title = max(g, key=lambda e: len(e["title"]))["title"]  # fullest title wins
        plats = sorted({e["platform"] for e in g})
        row = {"Title": title, "Platforms": "; ".join(plats),
               "Hours Played": best_hours(e["hours"] for e in g),
               "Last Played": best_date(e["last_played"] for e in g)}
        for p in platform_names:
            ids = [e["id"] for e in g if e["platform"] == p and e["id"]]
            row[f"{p} ID"] = "; ".join(ids)
        rows.append(row)
    rows.sort(key=lambda r: r["Title"].lower())
    with open(OUT_UNIFIED, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, cols)
        w.writeheader()
        w.writerows(rows)
    return rows


def write_review_queue(pending):
    with open(OUT_REVIEW, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Ratio", "A Platform", "A Title", "B Platform", "B Title", "Key"])
        for a, b, ratio, pk in sorted(pending, key=lambda p: -p[2]):
            w.writerow([f"{ratio:.2f}", a["platform"], a["title"],
                        b["platform"], b["title"], pk])


def review(links):
    try:
        with open(OUT_REVIEW, encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
    except OSError:
        print("No review queue. Run `python3 unify.py` first.")
        return
    todo = [r for r in rows if r["Key"] not in links["confirmed"]
            and r["Key"] not in links["rejected"]]
    if not todo:
        print("Nothing to review.")
        return
    print(f"{len(todo)} uncertain matches. y = same game, n = different, "
          f"s = skip, q = quit.\n")
    for i, r in enumerate(todo, 1):
        print(f"[{i}/{len(todo)}] ratio {r['Ratio']}")
        print(f"  {r['A Platform']:8} {r['A Title']}")
        print(f"  {r['B Platform']:8} {r['B Title']}")
        ans = input("  same game? [y/n/s/q] > ").strip().lower()
        if ans == "q":
            break
        if ans == "y":
            links["confirmed"][r["Key"]] = True
        elif ans == "n":
            links["rejected"][r["Key"]] = True
        save_links(links)
        print()
    print("Run `python3 unify.py` again to fold your answers in.")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--review", action="store_true")
    args = p.parse_args()

    links = load_links()
    if args.review:
        review(links)
        return

    entries = []
    counts = {}
    for name, cfg in SOURCES.items():
        rows = load_source(name, cfg)
        counts[name] = len(rows)
        entries.extend(rows)
    for name, path in manual_list_sources().items():
        rows = load_manual_list(name, path)
        counts[name] = len(rows)
        entries.extend(rows)

    groups, pending = build_groups(entries, links)
    rows = write_unified(groups)
    write_review_queue(pending)

    multi = sum(1 for r in rows if ";" in r["Platforms"])
    print("Loaded: " + ", ".join(f"{n} {c}" for n, c in counts.items() if c))
    print(f"{len(rows)} unique games -> {os.path.relpath(OUT_UNIFIED, HERE)}")
    print(f"  {multi} owned on more than one platform")
    if pending:
        print(f"  {len(pending)} uncertain matches -> {os.path.relpath(OUT_REVIEW, HERE)}")
        print("  run `python3 unify.py --review` to resolve them")


if __name__ == "__main__":
    main()
