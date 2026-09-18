"""Check whether you already own a game, on any platform, before buying it.

    python3 own.py "witcher 3"
    python3 own.py "elden ring" "hollow knight"

Searches output/unified_library.csv (built by unify.py). Run that first, and
rerun it after adding a new platform or a fresh export, or this will miss
anything from since your last unify.
"""

import csv
import difflib
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
UNIFIED = os.path.join(HERE, "output", "unified_library.csv")


def norm(s):
    return " ".join(re.sub(r"[^a-z0-9 ]", " ", s.lower()).split())


def load():
    try:
        with open(UNIFIED, encoding="utf-8") as f:
            return list(csv.DictReader(f))
    except OSError:
        sys.exit("No unified library yet. Run `python3 unify.py` first.")


def search(query, rows):
    q = norm(query)
    exact = [r for r in rows if q in norm(r["Title"])]
    if exact:
        return exact
    keys = {norm(r["Title"]): r for r in rows}
    close = difflib.get_close_matches(q, keys, n=5, cutoff=0.6)
    return [keys[k] for k in close]


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    rows = load()
    for query in sys.argv[1:]:
        hits = search(query, rows)
        print(f'\n"{query}"')
        if not hits:
            print("  Not found - you probably don't own this anywhere. Safe to buy.")
            continue
        for r in hits[:5]:
            plats = r["Platforms"]
            hrs = f', {r["Hours Played"]}h' if r["Hours Played"] else ""
            last = f', last played {r["Last Played"]}' if r["Last Played"] else ""
            print(f'  OWNED on {plats}  ->  {r["Title"]}{hrs}{last}')


if __name__ == "__main__":
    main()
