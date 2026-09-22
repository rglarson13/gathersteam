"""Record games you have played on other stores, without editing any CSV.

    python3 add_played.py                    # walk your EA/Ubisoft/Battle.net/... lists
    python3 add_played.py --platform GOG     # or walk a specific store instead
    python3 add_played.py --game "Overwatch" # just one game, by name
    python3 add_played.py --redo             # re-ask games you already answered
    python3 add_played.py --rebuild          # apply saved answers to your recommendations

For each game it asks whether you played it, and if so, roughly how many hours,
when you last played, and how you felt about it. Everything after "played it?"
is optional - press Enter to skip any question. If you do not know the hours, it
offers a rough-size menu, and if you skip that too it counts the game as a light
~5 hour signal, marked "estimated", so it still registers without inventing a
precise number.

Answers are remembered, so rerunning only asks about games you have not
answered. Quit any time with q or Ctrl-C; nothing is lost.

Where it writes (you never need to open these):
  data/manual_additions.csv   the played-elsewhere games and their hours
  data/triage.json            "played" marks, and love/meh opinions
  data/steam_id_resolutions.json  the Steam listing chosen for each title
"""

import argparse
import csv
import datetime as dt
import difflib
import json
import os
import re
import subprocess
import sys

import unify   # reuses its title normaliser and the manual-list discovery

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
OUTPUT = os.path.join(HERE, "output")

UNIFIED = os.path.join(OUTPUT, "unified_library.csv")
PLAYED = os.path.join(OUTPUT, "steam_recommendation_input.csv")
STEAM_LIB = os.path.join(DATA, "steam_library.csv")
MANUAL = os.path.join(DATA, "manual_additions.csv")
STATE = os.path.join(DATA, "triage.json")
RESOLUTIONS = os.path.join(DATA, "steam_id_resolutions.json")
APPLIST = os.path.join(DATA, "steam_applist_cache.json")

COLS = ["Game", "AppID", "Hours Played", "Last Played", "Achievements Unlocked",
        "Achievements Total", "Completion %", "Note"]

# Rough sizes for when you cannot recall hours. Log-scaled downstream, so being
# off by a factor of two barely matters.
BUCKETS = {"1": (1, "barely tried it"), "2": (5, "a few sessions"),
           "3": (20, "a good chunk"), "4": (60, "played it a lot"),
           "5": (200, "hundreds of hours")}
UNKNOWN_HOURS = 5

JUNK = re.compile(r"soundtrack|\bdlc\b|demo\b|\bbeta\b|playtest|dedicated server|"
                  r"test server|artbook|season pass|bundle|\bpack\b|\bost\b|"
                  r"wallpaper|expansion", re.I)


class Quit(Exception):
    pass


def ask(prompt):
    try:
        return input(prompt).strip()
    except EOFError:
        raise Quit()


# ---------------------------------------------------------------- state files

def read_json(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def write_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f, indent=1)
    os.replace(tmp, path)


def read_manual():
    try:
        with open(MANUAL, encoding="utf-8") as f:
            return list(csv.DictReader(f))
    except OSError:
        return []


def write_manual(rows):
    tmp = MANUAL + ".tmp"
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, COLS, extrasaction="ignore", restval="")
        w.writeheader()
        w.writerows(rows)
    os.replace(tmp, MANUAL)


# ---------------------------------------------------------------- steam lookup

_index = None


def steam_index():
    """(token set, appid, name) for every Steam app, built once - a few seconds."""
    global _index
    if _index is None:
        apps = read_json(APPLIST, [])
        _index = [(set(unify.norm(a["name"]).split()), a["appid"], a["name"])
                  for a in apps if a.get("name") and not JUNK.search(a["name"])]
    return _index


def steam_name(appid):
    for _, a, name in steam_index():
        if str(a) == str(appid):
            return name
    return ""


def search_steam(query, limit=5):
    """Steam apps whose name contains every word of the query, closest first."""
    qn = unify.norm(query)
    qtok = set(qn.split())
    if not qtok:
        return []
    hits = []
    for toks, appid, name in steam_index():
        if qtok <= toks:
            ratio = difflib.SequenceMatcher(None, qn, unify.norm(name)).ratio()
            hits.append((-ratio, appid, name))
    hits.sort()
    return [(a, n) for _, a, n in hits[:limit]]


def save_resolution(title, appid):
    """Remember the chosen listing so later runs and build_backlog agree on it."""
    res = read_json(RESOLUTIONS, {"resolved": {}, "rejected": {}})
    res["resolved"][title] = {"appid": int(appid), "source": "manual"}
    res["rejected"].pop(title, None)
    write_json(RESOLUTIONS, res)


# ---------------------------------------------------------------- parsing

def parse_hours(text):
    m = re.search(r"\d[\d,]*\.?\d*", text)
    if not m:
        return None
    try:
        h = float(m.group().replace(",", ""))
    except ValueError:
        return None
    return h if h > 0 else None


def parse_when(text):
    """'2019', '2019-05' or '2019-05-12' -> ISO date; '' -> ''; junk -> None."""
    t = text.strip()
    if not t:
        return ""
    m = re.fullmatch(r"(\d{4})(?:-(\d{1,2}))?(?:-(\d{1,2}))?", t)
    if not m:
        return None
    year = int(m.group(1))
    month = int(m.group(2)) if m.group(2) else 7          # year only -> mid-year
    day = int(m.group(3)) if m.group(3) else (15 if m.group(2) else 1)
    try:
        d = dt.date(year, month, day)
    except ValueError:
        return None
    if year < 1985 or d > dt.date.today():
        return None
    return d.isoformat()


# ---------------------------------------------------------------- questions

def ask_hours():
    while True:
        t = ask("  Roughly how many hours? (a number, or Enter if you don't know) > ")
        if not t:
            break
        h = parse_hours(t)
        if h:
            return h, ""
        print("  Please type a number like 40, or just press Enter.")
    print("  No problem - a rough size is fine:")
    for k, (h, label) in BUCKETS.items():
        print(f"    {k} = {label} (~{h}h)")
    t = ask(f"  Pick 1-5, or Enter to count it as a light ~{UNKNOWN_HOURS}h estimate > ")
    if t in BUCKETS:
        h, label = BUCKETS[t]
        return h, f"estimated: {label}"
    return UNKNOWN_HOURS, "estimated: hours unknown"


def ask_when():
    while True:
        t = ask("  When did you last play it? (a year like 2019, or 2019-05; Enter = not sure) > ")
        d = parse_when(t)
        if d is not None:
            return d
        print("  Try a year like 2019, or year-month like 2019-05, or press Enter.")


def ask_opinion():
    t = ask("  How was it? [l = loved it, m = meh / didn't like it, Enter = no strong feeling] > ").lower()
    return {"l": 2.0, "m": 0.5}.get(t[:1]) if t else None


def pick_appid(title):
    """Help find the Steam listing for a game that had no automatic match."""
    hits = search_steam(title)
    if hits:
        print("  I couldn't match this to Steam automatically. Closest listings:")
        for i, (appid, name) in enumerate(hits, 1):
            print(f"    {i}) {name}  (AppID {appid})")
        prompt = "  Pick one, type an AppID, or Enter if none of these > "
    else:
        print("  I couldn't find a similar name on Steam.")
        prompt = "  Type a Steam AppID if you know one, or Enter to skip > "
    while True:
        t = ask(prompt)
        if not t:
            return None
        if t.isdigit() and hits and 1 <= int(t) <= len(hits):
            return str(hits[int(t) - 1][0])
        if t.isdigit() and int(t) > len(hits):
            return t
        print("  Please pick a number from the list, an AppID, or press Enter.")


# ---------------------------------------------------------------- recording

def record(cand, appid, hours, when, note, weight):
    st = read_json(STATE, {})
    st.setdefault("decisions", {})
    st.setdefault("order", [])
    st["decisions"][appid] = "y"
    if appid not in st["order"]:
        st["order"].append(appid)
    if weight is not None:
        st.setdefault("game_prefs", {})[appid] = weight
    st.setdefault("addplayed", {})[cand["title"]] = "y"

    owned_on_steam = appid in {r["AppID"] for r in csv.DictReader(
        open(STEAM_LIB, encoding="utf-8"))}
    if owned_on_steam:
        # A manual row would be ignored (Steam's own row wins), so use the override.
        st.setdefault("hours", {})[appid] = hours
        where = "as an hours override on your Steam copy"
    else:
        rows = read_manual()
        old = next((r for r in rows if r["AppID"] == appid), {})
        rows = [r for r in rows if r["AppID"] != appid]
        rows.append({"Game": cand["title"], "AppID": appid,
                     "Hours Played": f"{hours:g}",
                     "Last Played": when or old.get("Last Played", ""),
                     "Achievements Unlocked": old.get("Achievements Unlocked", ""),
                     "Achievements Total": old.get("Achievements Total", ""),
                     "Completion %": old.get("Completion %", ""),
                     "Note": note})
        write_manual(rows)
        where = "in data/manual_additions.csv"
    write_json(STATE, st)
    save_resolution(cand["title"], appid)
    feel = {2.0: ", loved it", 0.5: ", meh"}.get(weight, "")
    est = "  (estimate)" if note else ""
    print(f"  Recorded {where}: {hours:g}h, last played {when or 'unknown'}{feel}.{est}")


def set_status(title, status):
    st = read_json(STATE, {})
    st.setdefault("addplayed", {})[title] = status
    write_json(STATE, st)


# ---------------------------------------------------------------- candidates

def make_candidate(row, resolved):
    title = row["Title"]
    appid = ((row.get("Steam ID") or "").split(";")[0].strip()
             or str(resolved.get(title, {}).get("appid", "")))
    return {"title": title, "platforms": row.get("Platforms", ""), "appid": appid}


def build_candidates(args, unified, st, resolved):
    played_ids = set()
    try:
        played_ids = {r["AppID"] for r in csv.DictReader(open(PLAYED, encoding="utf-8"))}
    except OSError:
        pass
    want = {p.lower() for p in (args.platform or unify.manual_list_sources())}
    if not want:
        sys.exit("No platform lists found in input/. Add e.g. input/ea_manual_list.txt, "
                 "or pass --platform GOG.")
    answered = st.get("addplayed", {})
    out = []
    for r in unified:
        plats = [p for p in r["Platforms"].split("; ") if p]
        if "Steam" in plats or not ({p.lower() for p in plats} & want):
            continue
        c = make_candidate(r, resolved)
        if not args.redo and (c["title"] in answered or c["appid"] in played_ids):
            continue
        out.append(c)
    out.sort(key=lambda c: (c["platforms"].lower(), c["title"].lower()))
    return out


def find_game(name, unified, resolved):
    q = unify.norm(name)
    hits = [r for r in unified if q and q in unify.norm(r["Title"])]
    if not hits:
        by = {unify.norm(r["Title"]): r for r in unified}
        hits = [by[k] for k in difflib.get_close_matches(q, list(by), n=5, cutoff=0.7)]
    if not hits:
        print(f'  "{name}" isn\'t in your library lists - adding it as a new game.')
        return {"title": name, "platforms": "(not in your lists)", "appid": ""}
    if len(hits) > 1:
        print(f'  "{name}" matches several games:')
        for i, r in enumerate(hits[:8], 1):
            print(f"    {i}) {r['Title']}  [{r['Platforms']}]")
        t = ask("  Which one? (number, or Enter to add it as a new game) > ")
        if not (t.isdigit() and 1 <= int(t) <= min(8, len(hits))):
            return {"title": name, "platforms": "(not in your lists)", "appid": ""}
        hits = [hits[int(t) - 1]]
    return make_candidate(hits[0], resolved)


# ---------------------------------------------------------------- main flow

def ask_game(c, i, n):
    print(f"\n{'-' * 60}")
    print(f"[{i} of {n}]  {c['title']}     on: {c['platforms']}")
    if c["appid"]:
        print(f"  Steam listing: {steam_name(c['appid']) or '(unknown name)'}  (AppID {c['appid']})")
    a = ask("  Have you played it? [y = yes, n = no, Enter = ask me later, q = quit] > ").lower()
    if a in ("q", "quit"):
        return "quit"
    if a in ("", "s"):
        return "skip"
    if a in ("n", "no"):
        set_status(c["title"], "n")
        return "done"
    if a not in ("y", "yes"):
        print("  (Didn't understand that - skipping. Type y, n, or press Enter.)")
        return "skip"

    appid = c["appid"] or pick_appid(c["title"])
    if not appid:
        set_status(c["title"], "y-no-listing")
        print("  Noted. With no Steam listing there are no tags to score it against, so it\n"
              "  can't influence recommendations - but you won't be asked again.")
        return "done"
    hours, note = ask_hours()
    when = ask_when()
    weight = ask_opinion()
    record(c, appid, hours, when, note, weight)
    return "recorded"


def rebuild():
    py = sys.executable
    steps = [
        ("Rebuilding the played list", [py, "build_reclist.py"], False),
        ("Fetching tags for anything new (a few seconds each)", [py, "enrich.py"], False),
        ("Rebuilding your taste profile", [py, "weights.py"], False),
        ("Refreshing the backlog candidates", [py, "build_backlog.py"], False),
        ("Tagging the candidates", [py, "enrich.py", "--input",
         "output/steam_backlog_candidates.csv", "--out",
         "output/steam_backlog_enriched.csv"], False),
        ("Your updated top recommendations:", [py, "recommend.py", "--top", "15"], True),
    ]
    for label, cmd, show in steps:
        print(f"\n{label}")
        r = subprocess.run(cmd, cwd=HERE, capture_output=True, text=True)
        if r.returncode != 0:
            print(r.stdout[-800:], r.stderr[-800:])
            print(f"  Stopped: {' '.join(cmd[1:])} failed. Fix that, then rerun it by hand.")
            return
        if show:
            print(r.stdout)


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--platform", action="append", help="store to walk (repeatable)")
    p.add_argument("--game", help="add just this game, by name")
    p.add_argument("--redo", action="store_true", help="re-ask games already answered")
    p.add_argument("--no-rebuild", action="store_true", help="do not offer to rebuild")
    p.add_argument("--rebuild", action="store_true",
                   help="just rebuild profile + recommendations from saved answers")
    args = p.parse_args()
    if args.rebuild:
        rebuild()
        return

    try:
        unified = list(csv.DictReader(open(UNIFIED, encoding="utf-8")))
    except OSError:
        sys.exit("No unified library yet. Run `python3 unify.py` first.")
    if not os.path.exists(APPLIST):
        print("(Note: run `python3 resolve_steam_ids.py` once to enable Steam listing search.)")
    st = read_json(STATE, {})
    resolved = read_json(RESOLUTIONS, {"resolved": {}}).get("resolved", {})

    recorded = 0
    try:
        if args.game:
            cands = [find_game(args.game, unified, resolved)]
        else:
            cands = build_candidates(args, unified, st, resolved)
            if not cands:
                print("Nothing left to ask about. (Use --redo to go back over answered games.)")
                return
            print(f"{len(cands)} games to go through. Press Enter on any question to skip it; "
                  f"q quits and remembers your progress.")
        for i, c in enumerate(cands, 1):
            r = ask_game(c, i, len(cands))
            if r == "quit":
                break
            recorded += r == "recorded"
    except (Quit, KeyboardInterrupt):
        print()

    print(f"\n{recorded} game(s) recorded this session.")
    if recorded and not args.no_rebuild:
        try:
            if ask("Rebuild your profile and recommendations now? [Y/n] > ").lower() in ("", "y", "yes"):
                rebuild()
            else:
                print("OK - your answers are saved. To apply them later, run:\n"
                      "    python3 add_played.py --rebuild")
        except (Quit, KeyboardInterrupt):
            print("\nSkipped the rebuild. To apply your answers, run:\n"
                  "    python3 add_played.py --rebuild")


if __name__ == "__main__":
    main()
