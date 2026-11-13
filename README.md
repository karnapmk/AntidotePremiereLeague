# AntidotePremiereLeague

Scores, stats and the report card for the Antidote Premiere League.

## Files

- `AntidoteScores.txt`: every round. One date block per table, in either format (`Name: 1, 3, -2` per player, or a line of names over a line of scores). `S` / `X` mean the player ended holding a syringe / toxin, worth −⌈n/2⌉. A 2-player game is dealt like a 3-player one, so it is scored like one: S/X is −2 and normalized stats divide by 3.
- `House Rules.txt`: the league's house rules.
- `Antidote_score_stats.py`: reads the scores and writes `AntidoteStats.xlsx` and `Antidote Report Card.pdf`.
- `league_extras.py`: the stats beyond the report card (see below), used by the script.
- `league_stats.py`: how sure we can be: confidence intervals, chance of being #1, p-values and multiple-comparison correction for every stat.

## Running it

```
python Antidote_score_stats.py            # workbook + report card (regulars only)
python Antidote_score_stats.py --npcs     # report card that also ranks guests
python Antidote_score_stats.py --json league.json   # every stat as JSON, nothing else written
python Antidote_score_stats.py --input other.txt    # read a different score file
```

## The workbook

- `GameLog`, `PlayerStats`, `Group Stats`: the report card's numbers.
- `Form & Clutch`: Elo, the last 10 rounds (survival, score, heat = form minus career), Elo change over those rounds, streaks; survival in the next round the same night after dying vs after surviving; the last round of each night; and each player's best partner, toughest rival and favourite target (among players with 5+ rounds and 3+ shared rounds).
- `Rivals`: head-to-head on clamped scores (dying on −1 or −4 counts the same) with Elo's win chance, and chemistry (survival with and without each partner).
- `Endings & Nights`: S/X end cards by player and by table size, the biggest Elo swings in a night, and the upset of each night (the finish pre-game Elo thought least likely).
- `Elo History`: everyone's rating after every round.
- `Significance`: for every award, each player's 95% interval, chance of really being #1, and p / q against the league average.
- `Significance Tests`: head-to-head sign tests, chemistry / bounce-back / closer Fisher tests, and hot-or-due permutation tests.

## How sure are we?

With a few dozen rounds, most gaps between players could be luck. `league_stats.py` says which aren't:

- **95% intervals** come from a bootstrap by night: the nights are resampled with replacement 2,000 times (seed 1729) and every stat is recomputed. Whole nights are resampled because rounds on one night aren't independent.
- **Chance of #1** is the share of resamples a regular tops an award.
- **p-values:** awards vs the league average (bootstrap), head-to-head (exact sign test), chemistry / bounce-back / closer (Fisher's exact test), hot-or-due (permutation test).
- **q-values** apply the Benjamini-Hochberg correction within each table, because every player and pair is tested at once. q < 0.05 is significant.
- Only players with 5+ rounds over 3+ nights are tested.

## The website

The [Antidote web app](https://rosstewart.github.io/antidote/?league) shows all of this as interactive charts, reading `AntidoteScores.txt` live from this repo. Its stats code is a TypeScript port of these two Python files, and its tests compare the two on every number using the `--json` export. If you change how a stat is defined here, change it there too (`shared/src/league/index.ts` in the webserver repo) and regenerate its test fixture.
