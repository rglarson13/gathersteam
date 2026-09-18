# gathersteam

Export your Steam library, work out what you *actually* like, and get
recommendations from the games you already own.

Steam knows how many hours you played. It does not know that 3,000 of them were
one game you play with friends, that your kids used your account, that you 100%ed
Dark Souls on a PS3, or that you love Metroidvanias. This fixes that, then uses
the corrected picture to rank your unplayed backlog.

---

## TL;DR — I just want to run it again

```bash
python3 build_reclist.py && python3 enrich.py && python3 weights.py && python3 recommend.py
```

**Run those four in that order after any change.** Everything is cached, so it
takes seconds. If output looks stale, you skipped a step — most likely `enrich.py`,
which is what feeds tags to `weights.py`.

---

## Setup

Needs Python 3 and `requests`. Nothing else — no virtualenv required.

```bash
cp .env.example .env      # then edit it
```

`.env` needs two values:

```
STEAM_API_KEY=...     # free: https://steamcommunity.com/dev/apikey
STEAM_ID=76561198...  # Steam64 ID, vanity name, or full profile URL
```

The API key form asks for a domain — it is not validated, so `localhost` is fine.
Your account needs Steam Guard enabled and at least $5 of lifetime spend, and
**Game details must be Public** in your privacy settings or the API returns an
empty library.

---

## First run, from scratch

```bash
python3 gathersteam.py        # 1. export the library      (~2 min, one API call per game)
python3 build_reclist.py      # 2. decide who played what
python3 enrich.py             # 3. fetch tags              (SLOW - see below)
python3 weights.py            # 4. score games, build the taste profile
python3 recommend.py          # 5. rank your unplayed backlog
```

**Step 3 is the slow one.** Roughly 3 seconds per game, so a 400-game played list
takes ~20 minutes and a 2,500-game backlog takes over two hours. It caches every
result to `data/enrich_cache.json`, so it is safe to interrupt and rerun — and
`--no-fetch` writes a CSV from whatever is cached so far, without touching the
network, which is safe to run *while* a fetch is in progress.

Then teach it about yourself (below), and rerun the four-command pipeline.

---

## The pipeline

```
gathersteam.py     Steam API          -> data/steam_library.csv
build_reclist.py   library + your calls -> output/steam_recommendation_input.csv
enrich.py          Steam + SteamSpy   -> output/steam_played_enriched.csv
weights.py         scores + profiles  -> output/taste_profile*.csv
recommend.py       profile + backlog  -> output/recommendations*.csv
```

`triage.py`, `mark.py` and `family_candidates.py` are how you feed judgements in.
They only write `data/triage.json`; nothing takes effect until you rerun the
pipeline.

---

## Teaching it about yourself

All of this goes through `mark.py`. Names are fuzzy-matched, so typos are fine —
it prints what it resolved, and `--show` checks without changing anything.

```bash
python3 mark.py --show "nidhogg"
```

### Who played it

| Command | Meaning |
|---|---|
| `--mine "Game"` | You played it, whatever the data says |
| `--not-mine "Game"` | Someone else's playtime (kids, a housemate) |

Your call **overrides every automatic signal**, including unlocked achievements.
Shared machines make achievements unreliable proof.

### Why you played it

| Command | Effect on the tag profile |
|---|---|
| `--social "Game"` | 0.15x — you were there for the friends, not the genre |
| `--social-partial "Game"` | 0.5x — social, but you would play it anyway |
| `--not-social "Game"` | 1.0x — online, but it is your own taste |
| `--family "Game"` | Full weight **and** its own profile: couch co-op you want more of |
| `--not-family "Game"` | Remove from the family list |

Social games keep full weight in the games list — you did play them. The discount
applies only to what they say about your *taste*.

### When the numbers are wrong

| Command | Use for |
|---|---|
| `--hours 100 "Game"` | Playtime Steam cannot see (console, GOG, Game Pass) |
| `--completion 100 "Game"` | Achievements earned on another platform |
| `--love "Game"` | 2x weight — you rate it far above what the hours suggest |
| `--meh "Game"` | 0.5x weight — hours overstate your regard |
| `--neutral "Game"` | Clear a love/meh |
| `--shelved "Game"` | Set aside, mean to return — collected in `shelved_backlog.csv`, changes no weighting |

`--hours` also marks the game as yours, which is what pulls a 0-hour game into the
played list at all.

### Declaring taste directly

These act on **tags**, not games. Validated against your library's vocabulary, so
a typo gets a suggestion rather than a dead entry.

```bash
python3 mark.py --prefer "Metroidvania"          # 1.5x
python3 mark.py --prefer-strong "Beat 'em up"    # 2.0x
python3 mark.py --dislike "LEGO"                 # 0.5x
python3 mark.py --no-preference "LEGO"           # clear
```

Use these where playtime cannot show something — a genre you love but have not got
to, or one your kids have outgrown. Declared tags are marked `<- declared` in the
output, and `taste_profile.csv` keeps raw `Lift` next to `Adj Lift` so you can
always see what the data said before you weighed in.

---

## Games Steam does not know about

For anything played on GOG, console, or another account, add a row to
`data/manual_additions.csv`:

```csv
Game,AppID,Hours Played,Last Played,Achievements Unlocked,Achievements Total,Completion %
The Witcher 3: Wild Hunt,292030,120,2015-05-18,62,78,79
```

Find the AppID from its Steam store URL — the game only has to *exist* on Steam,
you do not need to own it there. `Last Played` matters: leave it blank and the
game sits at a recency floor of 0.35.

---

## Bulk triage

For working through many games at once. Resumable — quit with `q` or Ctrl-C and
rerun to pick up exactly where you stopped.

```bash
# Did you actually play these, or just idle them for trading cards?
python3 triage.py --input output/steam_playtime_only.csv

# Which of these did you play WITH your kids?
python3 family_candidates.py --played-only --top 60
python3 triage.py --ask family --input output/steam_family_candidates.csv
```

Keys: `y` yes · `n` no · `s` skip (ask again next time) · `u` undo · `q` quit.

`family_candidates.py` ranks by weighted couch-play signals (`4 Player Local`
counts 3.0, `Funny` counts 1.0). `--played-only` restricts to games you have
played; horror and punishing-difficulty games are dropped unless you pass
`--allow-intense`; single-player-only games are never asked about.

---

## Getting recommendations

```bash
python3 recommend.py --top 40                 # solo
python3 recommend.py --family --top 20        # couch co-op
python3 recommend.py --min-metacritic 80      # quality floor
python3 recommend.py --min-tags 5             # skip thinly-tagged obscurities
```

Each candidate's tags are looked up in your profile and averaged, weighted by
position — SteamSpy returns tags in vote order, so the first ones describe the
game best. A tag you have no history with counts as **neutral, not negative**.

**Reading the score:** 1.0 is "typical of your library". Roughly, `>=1.2` is top
10%, `>=1.3` top 5%, `>=1.5` top 1%. Below ~1.1 is noise. Every row carries a
`Why` column naming the tags that earned the score — check it. A game that scored
well on tags you do not care about is a bad match, not a discovery.

Family scores run higher (2.5-4.5) because that profile is measured against your
whole library rather than against itself. Compare within a list, not across.

---

## Tuning

```bash
python3 weights.py --half-life 5           # chase recent taste (default 12)
python3 weights.py --family-half-life 2    # kids' current taste (default 4)
python3 weights.py --completion-boost 0    # ignore achievement completion
python3 weights.py --social-scale 0        # drop social games from tags entirely
python3 recommend.py --decay 0.7           # weight a game's first tags harder
```

**Half-life** is the big one: years until a game's weight halves. The default of 12
assumes stable taste; 5 heavily favours what you played recently. The family
profile runs on its own, shorter clock because children age out of things fast.

---

## How it decides things

**Weight** per game = log-scaled hours x completion multiplier x recency decay x
any declared multiplier. Log scaling matters: a 3,000-hour game is a stronger
signal than a 100-hour one, but not 30x stronger, or one outlier owns everything.

**Card-farm detection.** Games idled for Steam trading cards are auto-excluded:
launched in a narrow window, no achievements unlocked, and sharing an exact
playtime with several other games. Real play does not repeat to the tenth of an
hour across dozens of unrelated titles.

**Lift**, not volume. A tag's share of your weight divided by its share of your
games. Above 1.0 means those games pull more engagement than their headcount
predicts. Ranking by raw totals just surfaces `Action` and `Singleplayer`, which
describe nobody. Tags on fewer than 3 games are excluded as noise.

**The family profile is measured against your whole library**, not against itself.
Within 30 couch games `Local Co-Op` is everywhere and looks unremarkable; against
the full library it is 5x over-represented. Comparing a small homogeneous set to
itself neutralises the exact tags that define it.

---

## Files

| Path | What |
|---|---|
| `.env` | Your API key and Steam ID. Never commit. |
| `data/steam_library.csv` | Raw export: every owned game, playtime, achievements |
| `data/triage.json` | **Every judgement you have made.** Back this up. |
| `data/manual_additions.csv` | Hand-added off-Steam games |
| `data/platform_links.json` | Confirmed/rejected cross-platform title matches |
| `output/unified_library.csv` | Every game, deduplicated across platforms |
| `output/platform_match_review.csv` | Uncertain cross-platform matches awaiting your call |
| `data/*_cache.json` | API caches — hours of fetching, do not delete casually |
| `output/steam_recommendation_input.csv` | The played list: what counts as yours |
| `output/steam_not_played.csv` | Everything excluded, with reasons |
| `output/steam_played_weighted.csv` | Played games with weights and components |
| `output/taste_profile.csv` | Tag affinities and lift |
| `output/taste_profile_family.csv` | Same, for couch co-op |
| `output/recommendations.csv` | Ranked unplayed games |
| `output/recommendations_family.csv` | Same, for game night |
| `output/shelved_backlog.csv` | Games you meant to come back to |

`data/` is expensive or impossible to regenerate. `output/` is disposable —
delete it and the four-command pipeline rebuilds it in seconds.

Both are gitignored: they describe one person's library in detail.

---

## Cross-platform: seeing everything you own

Steam is one library among several. `unify.py` merges every platform you have
an exporter for into one canonical registry, matching the same game across
platforms by title even when nothing else lines up (no shared ID exists
between Steam, Epic and GOG).

```bash
python3 export_epic.py     # -> output/epic_games.csv
python3 export_gog.py      # -> output/gog_games.csv
python3 unify.py           # merge everything -> output/unified_library.csv
```

Matching has three tiers: identical normalized titles merge automatically;
very close titles (fuzzy ratio >= 0.93) also auto-merge; anything closer than
that but not certain goes to a review queue instead of guessing.

```bash
python3 unify.py --review
```

y = same game, n = different games, s = skip, q = quit. Answers are remembered
in `data/platform_links.json`, so a title is only ever asked about once, even
across future reruns after adding more platforms.

**Adding a new platform** (Ubisoft, EA, Blizzard, or a hand-typed PS/Switch
export): write an exporter that produces a CSV with a title column and ideally
a stable id column, then add one entry to the `SOURCES` dict at the top of
`unify.py`. Nothing else needs to change.

### Before you buy something

```bash
python3 own.py "elden ring"
python3 own.py "hollow knight" "witcher 3"
```

Searches the unified registry and tells you which platform(s) you already own
a game on, with hours and last-played where available, so you do not
accidentally rebuy something on Steam that you already have on GOG.

### What this does not do yet

The taste profile and recommender (`weights.py`, `recommend.py`) are still
Steam-only: they need tags, and tag lookup currently goes through Steam's own
APIs by AppID. A GOG- or Epic-only game has no AppID to look up. Getting
recommendations to genuinely span platforms means either resolving non-Steam
titles to a Steam AppID where one exists (many indie/AA titles are on both),
or adding a platform-agnostic tag source (IGDB). Not built yet - flag it if
you want to prioritize it.

In the meantime, a high-value real number a game already played on GOG/Epic
*can* still enter the taste profile the same way console hours do: add it to
`data/manual_additions.csv` with a Steam AppID (the game only needs to exist
on Steam, not be owned there) and real hours/dates from the unified registry.

---

## Gotchas

- **Rerun the whole pipeline.** `weights.py` reads what `enrich.py` wrote, which
  reads what `build_reclist.py` wrote. Skipping a step silently uses stale data.
- **`--family` writes to `recommendations_family.csv`.** Passing `--out` to one
  run will overwrite whatever you point it at.
- **Some games can never have achievement data.** Delisted apps return HTTP 500
  forever; they show `unknown` rather than a count, which is not the same as zero.
- **Console and other-account hours are invisible.** If your kids play on their own
  accounts, those games look untouched — the recommender will happily suggest
  their favourites back to you.
- **Steam's tags are crowd-sourced**, so `Retro` mostly marks modern pixel-art
  indies, not actual retro games.

---

## Refreshing later

```bash
python3 gathersteam.py                       # pick up new games and playtime
python3 build_reclist.py && python3 enrich.py && python3 weights.py && python3 recommend.py
```

`data/triage.json` persists, so every judgement you have made survives. Only
genuinely new games need fetching.
