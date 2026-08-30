"""Mark games as played-by-you or not, by name. Fuzzy-matches typos.

    python3 mark.py --not-mine "Slime Rancher" "Golf with your Friends"
    python3 mark.py --mine "Seej"
    python3 mark.py --social "Dead by Daylight"      # there for the friends
    python3 mark.py --social-partial "Valheim"       # social, but you love it too
    python3 mark.py --not-social "V Rising"          # multiplayer but solo taste
    python3 mark.py --family "Castle Crashers"       # couch co-op with the kids
    python3 mark.py --hours 100 "Fallout 3" "Oblivion"   # console/other-platform
    python3 mark.py --completion 100 "DARK SOULS"        # 100%ed it on console
    python3 mark.py --prefer "Metroidvania" "Deckbuilding"   # taste you know you have
    python3 mark.py --dislike "Horror"                       # ... and taste you do not
    python3 mark.py --love "Alien: Isolation"     # great, hours just do not show it
    python3 mark.py --shelved "Alien: Isolation"  # set aside, want to come back
    python3 mark.py --show "goose"          # look up without changing anything

Social games stay in your played list at full weight but count less toward the
tag profile. The factor is graded: --social gives 0.15 (the genre is incidental),
--social-partial gives 0.5 (you would play it anyway). Games played online but
alone are not social at all - leave them unmarked.

--completion records achievement progress Steam cannot see, for a game you
completed on another platform. Give it a percentage.

--hours records real playtime Steam cannot see (console, Game Pass, a replay on
another account). It overrides the Steam figure for weighting and implies the
game is yours. Steam's own number is kept alongside it for reference.

--love / --meh act on a GAME's weight, for when playtime misleads: a game you
set aside for reasons unrelated to quality, or one you sank hours into without
much affection. --shelved is separate again - it does not change any weighting,
it just collects games you mean to return to.

--prefer / --dislike act on TAGS, not games. They multiply a tag's score in the
taste profile: --prefer 1.5x, --prefer-strong 2x, --dislike 0.5x. Use them where
you know something the playtime cannot show - a genre you love but have not got
to yet, or one your hours overstate. --no-preference clears a tag.

--family is different from --social: these are games you play WITH someone and
genuinely want more of. They keep full weight and also build their own profile.

Writes to data/triage.json, the same state triage.py uses, so decisions made here
and there accumulate together. Rerun build_reclist.py afterwards.
"""

import csv
import difflib
import json
import os
import re
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


def norm(s):
    s = s.lower().replace("™", "").replace("®", "").replace("&", "and")
    return " ".join(re.sub(r"[^a-z0-9 ]", " ", s).split())


def load_lib():
    with open(LIB, encoding="utf-8") as f:
        lib = list(csv.DictReader(f))
    # Games played off-Steam (GOG, console) live here so they can be marked too.
    try:
        with open(os.path.join(DATA, "manual_additions.csv"), encoding="utf-8") as f:
            lib += list(csv.DictReader(f))
    except OSError:
        pass
    return lib


def match(name, lib):
    """Exact normalised match, else closest fuzzy match above a threshold."""
    index = {norm(r["Game"]): r for r in lib}
    key = norm(name)
    if key in index:
        return index[key], "exact"
    close = difflib.get_close_matches(key, list(index), n=1, cutoff=0.72)
    if close:
        return index[close[0]], "fuzzy"
    # Last resort: unique substring hit, which catches heavy subtitle drift.
    subs = [r for k, r in index.items() if key in k or k in key]
    if len(subs) == 1:
        return subs[0], "substring"
    return None, None


ENRICHED = os.path.join(OUTPUT, "steam_played_enriched.csv")


def vocabulary():
    """Every tag/genre seen in your library, for validating declared names."""
    seen = set()
    try:
        with open(ENRICHED, encoding="utf-8") as f:
            for row in csv.DictReader(f):
                for col in ("Tags", "Genres", "Categories"):
                    seen.update(t.strip() for t in (row.get(col) or "").split(";")
                                if t.strip())
    except OSError:
        pass
    return seen


def tag_prefs(mode, names):
    """Declared tag preferences, stored separately from per-game decisions."""
    try:
        state = json.load(open(STATE))
    except (OSError, ValueError):
        state = {"decisions": {}, "order": []}
    prefs = dict(state.get("tag_prefs", {}))
    mult = {"--prefer": 1.5, "--prefer-strong": 2.0, "--dislike": 0.5}.get(mode)
    vocab = vocabulary()
    lower = {v.lower(): v for v in vocab}

    for raw in names:
        tag = lower.get(raw.lower())
        if not tag:
            close = difflib.get_close_matches(raw.lower(), list(lower), n=3, cutoff=0.7)
            if len(close) == 1:
                tag = lower[close[0]]
                print(f'  "{raw}" -> "{tag}" [fuzzy]')
            else:
                hint = f'  did you mean: {", ".join(lower[c] for c in close)}' if close else ""
                print(f'  UNKNOWN TAG  "{raw}"{hint}')
                continue
        if mult is None:
            prefs.pop(tag, None)
            print(f"  {tag:28} -> cleared")
        else:
            prefs[tag] = mult
            print(f"  {tag:28} -> {mult:g}x")

    state["tag_prefs"] = prefs
    json.dump(state, open(STATE, "w"))
    print(f"\n{len(prefs)} tag preference(s) set. Run: python3 weights.py")


def main():
    args = sys.argv[1:]
    if not args:
        sys.exit(__doc__)
    mode = args[0]
    names = args[1:]
    override_hours = override_pct = None
    if mode in ("--hours", "--completion"):
        try:
            value = float(names[0])
        except (IndexError, ValueError):
            sys.exit(f"{mode} needs a number first: {mode} 100 \"Game\"")
        if mode == "--hours":
            override_hours = value
        else:
            override_pct = max(0.0, min(100.0, value))
        names = names[1:]
    tag_modes = ("--prefer", "--prefer-strong", "--dislike", "--no-preference")
    game_mult = {"--love": 2.0, "--meh": 0.5, "--neutral": None}
    if mode not in ("--mine", "--not-mine", "--social", "--social-partial",
                    "--not-social", "--family", "--not-family", "--hours",
                    "--shelved", "--not-shelved", "--completion",
                    "--show") + tag_modes \
            + tuple(game_mult) or not names:
        sys.exit(__doc__)

    if mode in tag_modes:
        return tag_prefs(mode, names)

    lib = load_lib()
    try:
        state = json.load(open(STATE))
    except (OSError, ValueError):
        state = {"decisions": {}, "order": []}

    verdict = {"--mine": "y", "--not-mine": "n"}.get(mode)
    raw = state.get("social", {})
    # Older state stored a bare list; treat those as fully social.
    social = {a: 0.15 for a in raw} if isinstance(raw, list) else dict(raw)
    factor = {"--social": 0.15, "--social-partial": 0.5}.get(mode)
    family = set(state.get("family", []))
    hours = dict(state.get("hours", {}))
    gprefs = dict(state.get("game_prefs", {}))
    completion = dict(state.get("completion", {}))
    shelved = set(state.get("shelved", []))
    changed, missing = 0, []
    for name in names:
        row, how = match(name, lib)
        if not row:
            missing.append(name)
            print(f"  NOT FOUND  {name}")
            continue
        note = "" if how == "exact" else f"  [{how} match]"
        if mode == "--show":
            cur = state["decisions"].get(row["AppID"], "-")
            soc = (f" social={social[row['AppID']]:g}"
                   if row["AppID"] in social else "")
            soc += " family" if row["AppID"] in family else ""
            soc += f" hours={hours[row['AppID']]:g}" if row["AppID"] in hours else ""
            soc += f" x{gprefs[row['AppID']]:g}" if row["AppID"] in gprefs else ""
            soc += " shelved" if row["AppID"] in shelved else ""
            soc += (f" completion={completion[row['AppID']]:g}%"
                    if row["AppID"] in completion else "")
            print(f'  {row["Hours Played"]:>7}h  {row["Game"]:44} '
                  f'current={cur}{soc}{note}')
            continue
        if mode in game_mult:
            m = game_mult[mode]
            if m is None:
                gprefs.pop(row["AppID"], None)
            else:
                gprefs[row["AppID"]] = m
                state["decisions"][row["AppID"]] = "y"   # you rated it, so it is yours
                if row["AppID"] not in state["order"]:
                    state["order"].append(row["AppID"])
            changed += 1
            print(f'  {row["Hours Played"]:>7}h  {row["Game"]:44} '
                  f'-> {"neutral" if m is None else f"{m:g}x weight"}{note}')
            continue
        if mode in ("--shelved", "--not-shelved"):
            shelved.add(row["AppID"]) if mode == "--shelved" \
                else shelved.discard(row["AppID"])
            changed += 1
            print(f'  {row["Hours Played"]:>7}h  {row["Game"]:44} '
                  f'-> {"shelved" if mode == "--shelved" else "not shelved"}{note}')
            continue
        if mode == "--completion":
            completion[row["AppID"]] = override_pct
            state["decisions"][row["AppID"]] = "y"
            if row["AppID"] not in state["order"]:
                state["order"].append(row["AppID"])
            changed += 1
            print(f'  {row["Hours Played"]:>7}h  {row["Game"]:44} '
                  f'-> {override_pct:g}% complete '
                  f'(Steam says {row["Completion %"] or "0"}%){note}')
            continue
        if mode == "--completion":
            completion[row["AppID"]] = override_pct
            state["decisions"][row["AppID"]] = "y"
            if row["AppID"] not in state["order"]:
                state["order"].append(row["AppID"])
            changed += 1
            print(f'  {row["Hours Played"]:>7}h  {row["Game"]:44} '
                  f'-> {override_pct:g}% complete '
                  f'(Steam says {row["Completion %"] or "0"}%){note}')
            continue
        if mode == "--hours":
            hours[row["AppID"]] = override_hours
            state["decisions"][row["AppID"]] = "y"   # real playtime means yours
            if row["AppID"] not in state["order"]:
                state["order"].append(row["AppID"])
            changed += 1
            print(f'  {row["Hours Played"]:>7}h  {row["Game"]:44} '
                  f'-> {override_hours:g}h (was {row["Hours Played"]}h on Steam){note}')
            continue
        if mode in ("--family", "--not-family"):
            # Family games are wanted taste, so any social discount is dropped.
            if mode == "--family":
                family.add(row["AppID"])
                social.pop(row["AppID"], None)
            else:
                family.discard(row["AppID"])
            changed += 1
            print(f'  {row["Hours Played"]:>7}h  {row["Game"]:44} '
                  f'-> {"family co-op" if mode == "--family" else "not family"}{note}')
            continue
        if mode in ("--social", "--social-partial", "--not-social"):
            if factor is None:
                social.pop(row["AppID"], None)
                label = "not social"
            else:
                social[row["AppID"]] = factor
                label = f"social {factor:g}x"
            changed += 1
            print(f'  {row["Hours Played"]:>7}h  {row["Game"]:44} -> {label}{note}')
            continue
        prev = state["decisions"].get(row["AppID"])
        state["decisions"][row["AppID"]] = verdict
        if row["AppID"] not in state["order"]:
            state["order"].append(row["AppID"])
        if prev != verdict:
            changed += 1
        flag = "" if prev is None else f"  (was {prev})"
        print(f'  {row["Hours Played"]:>7}h  {row["Game"]:44} -> {verdict}{flag}{note}')

    if mode != "--show":
        state["social"] = social
        state["family"] = sorted(family)
        state["hours"] = hours
        state["game_prefs"] = gprefs
        state["completion"] = completion
        state["shelved"] = sorted(shelved)
        json.dump(state, open(STATE, "w"))
        print(f"\n{changed} changed, {len(state['decisions'])} decisions total.")
        if missing:
            print(f"not found: {missing}")
        print("Run: python3 build_reclist.py")


if __name__ == "__main__":
    main()
