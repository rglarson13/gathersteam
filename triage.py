"""Interactive triage: which no-achievement games did you actually play?

    python3 triage.py --input steam_playtime_only.csv
    python3 triage.py --ask family --input steam_family_candidates.csv
    python3 triage.py              # the no-achievement shortlist
    python3 triage.py --all        # include the card-farmed ones too
    python3 triage.py --review     # revisit decisions already made
    python3 triage.py --input other.csv

--ask family flips the question to "did you play this WITH your kids?" and
writes to the family list instead of the played/idled decisions.

Keys:  y = actually played   n = only idled for cards
       s = skip for now      u = undo last          q = quit

Writes triage_manual_played.csv / triage_manual_idled.csv (your hand
decisions only). The full played list comes from build_reclist.py.

Decisions are saved after every keypress to data/triage.json, so quitting at any
point (or Ctrl-C) loses nothing - rerun and you resume where you stopped.
"""

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
IN_CSV = os.path.join(OUTPUT, "steam_no_achievements.csv")
STATE = os.path.join(DATA, "triage.json")
PLAYED_CSV = os.path.join(OUTPUT, "triage_manual_played.csv")
IDLED_CSV = os.path.join(OUTPUT, "triage_manual_idled.csv")

BOLD, DIM, GREEN, RED, YELLOW, CYAN, RESET = (
    "\033[1m", "\033[2m", "\033[32m", "\033[31m", "\033[33m", "\033[36m", "\033[0m")
if not sys.stdout.isatty() or os.environ.get("NO_COLOR"):
    BOLD = DIM = GREEN = RED = YELLOW = CYAN = RESET = ""


def read_key():
    """Read one keypress without requiring Enter; fall back to a line read."""
    if not sys.stdin.isatty():
        line = sys.stdin.readline()
        if not line:
            return "q"
        return line.strip().lower()[:1] or "\n"
    import termios
    import tty
    fd = sys.stdin.fileno()
    saved = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        ch = sys.stdin.read(1)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, saved)
    if ch == "\x03":
        raise KeyboardInterrupt
    return ch.lower()


def _read_state():
    try:
        with open(STATE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def load_state(mode="played"):
    """Each mode keeps its own answered-set so the two passes do not collide."""
    st = _read_state()
    if mode == "family":
        return st.get("family_decided", {}), st.get("family_order", [])
    return st.get("decisions", {}), st.get("order", [])


def save_state(decisions, order, mode="played"):
    """Merge into the existing state - other keys (social, hours, prefs) must
    survive a triage run untouched."""
    st = _read_state()
    if mode == "family":
        st["family_decided"] = decisions
        st["family_order"] = order
        # Merge, never replace: games marked family via mark.py must survive a
        # triage pass that never asked about them.
        fam = set(st.get("family", []))
        for appid, verdict in decisions.items():
            fam.add(appid) if verdict == "y" else fam.discard(appid)
        st["family"] = sorted(fam)
    else:
        st["decisions"] = decisions
        st["order"] = order
    tmp = STATE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(st, f)
    os.replace(tmp, STATE)  # atomic, so a Ctrl-C mid-write cannot corrupt it


def write_output(games, decisions, mode="played"):
    """Write every decision made so far, not just this session's input set.

    Decisions are keyed by AppID and accumulate across runs over different
    inputs, so the outputs are always the full picture.
    """
    if mode == "family":
        return (sum(1 for v in decisions.values() if v == "y"),
                sum(1 for v in decisions.values() if v == "n"))
    lib_path = os.path.join(DATA, "steam_library.csv")
    try:
        with open(lib_path, encoding="utf-8") as f:
            lib = list(csv.DictReader(f))
    except OSError:
        lib = games
    by_id = {r["AppID"]: r for r in lib}
    for g in games:
        by_id.setdefault(g["AppID"], g)

    counts = {}
    for verdict, path in (("y", PLAYED_CSV), ("n", IDLED_CSV)):
        rows = [by_id[a] for a, v in decisions.items() if v == verdict and a in by_id]
        rows.sort(key=lambda r: -float(r["Hours Played"]))
        counts[verdict] = len(rows)
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["Game", "AppID", "Hours Played", "Last Played"])
            for r in rows:
                w.writerow([r["Game"], r["AppID"], r["Hours Played"], r["Last Played"]])
    return counts["y"], counts["n"]


def show(game, pos, total, decisions, mode="played"):
    played = sum(1 for v in decisions.values() if v == "y")
    idled = sum(1 for v in decisions.values() if v == "n")
    if mode == "family":
        kept, dropped = "family", "solo"
    else:
        kept, dropped = "played", "idled"
    bar_w = 32
    filled = int(bar_w * pos / total) if total else 0
    print(f"\n{DIM}{'-' * 60}{RESET}")
    print(f"{CYAN}[{pos} of {total}]{RESET} {DIM}{'#' * filled}{'.' * (bar_w - filled)}"
          f"  {GREEN}{played} {kept}{RESET}{DIM} / {RED}{idled} {dropped}{RESET}")
    print(f"\n  {BOLD}{game['Game']}{RESET}")
    played = game["Last Played"] or "never recorded"
    print(f"  {game['Hours Played']} hours   last played {played}")
    if game.get("Likely") == "idled":
        print(f"  {YELLOW}flagged as card-farming{RESET}")
    print(f"  {DIM}store.steampowered.com/app/{game['AppID']}{RESET}")
    if mode == "family":
        print(f"\n  {BOLD}y{RESET} with kids   {BOLD}n{RESET} solo   {BOLD}s{RESET} skip   "
              f"{BOLD}u{RESET} undo   {BOLD}q{RESET} quit  > ", end="", flush=True)
    else:
        print(f"\n  {BOLD}y{RESET} played   {BOLD}n{RESET} idled   {BOLD}s{RESET} skip   "
              f"{BOLD}u{RESET} undo   {BOLD}q{RESET} quit  > ", end="", flush=True)


def main():
    args = sys.argv[1:]
    mode = args[args.index("--ask") + 1] if "--ask" in args else "played"
    path = IN_CSV
    if "--input" in args:
        path = args[args.index("--input") + 1]
    try:
        with open(path, encoding="utf-8") as f:
            games = list(csv.DictReader(f))
    except OSError:
        sys.exit(f"Cannot read {path}. Run gathersteam.py first.")
    if "--all" not in args:
        games = [g for g in games if g.get("Likely") != "idled"]
    if not games:
        sys.exit("No games matched.")

    decisions, order = load_state(mode)
    if "--review" in args:
        decisions, order = {}, []

    total = len(games)
    print(f"{BOLD}{total} games to triage{RESET}  {DIM}(state: {os.path.basename(STATE)}){RESET}")
    done = sum(1 for g in games if g["AppID"] in decisions)
    if done:
        print(f"{DIM}resuming - {done} already decided{RESET}")

    i = 0
    try:
        while i < total:
            game = games[i]
            if game["AppID"] in decisions:
                i += 1
                continue
            show(game, i + 1, total, decisions, mode)
            key = read_key()
            print(key if key.strip() else "")

            if key == "q":
                break
            if key == "u":
                if order:
                    last = order.pop()
                    decisions.pop(last, None)
                    i = next(j for j, g in enumerate(games) if g["AppID"] == last)
                    save_state(decisions, order, mode)
                    print(f"  {YELLOW}undid{RESET}")
                else:
                    print(f"  {DIM}nothing to undo{RESET}")
                continue
            if key == "s":
                i += 1
                continue
            if key in ("y", "n"):
                decisions[game["AppID"]] = key
                order.append(game["AppID"])
                save_state(decisions, order, mode)
                i += 1
                continue
            print(f"  {DIM}press y, n, s, u or q{RESET}")
    except KeyboardInterrupt:
        print()
    finally:
        save_state(decisions, order, mode)
        played, idled = write_output(games, decisions, mode)
        left = sum(1 for g in games if g["AppID"] not in decisions)
        print(f"\n{DIM}{'-' * 60}{RESET}")
        if mode == "family":
            print(f"{GREEN}{played} family{RESET} / {RED}{idled} solo{RESET} "
                  f"-> data/triage.json  (run weights.py to rebuild the profile)")
        else:
            print(f"{GREEN}{played} played{RESET} -> {os.path.basename(PLAYED_CSV)}")
            print(f"{RED}{idled} idled{RESET} -> {os.path.basename(IDLED_CSV)}")
        if left:
            print(f"{left} still undecided - rerun to pick up where you left off.")
        else:
            print("All done.")


if __name__ == "__main__":
    main()
