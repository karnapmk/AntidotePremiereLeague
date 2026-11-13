"""League stats beyond the report card, shared with the website.

The Antidote web app (rosstewart.github.io/antidote, ?league) draws its charts from a
TypeScript port of these functions. Its test suite checks that port against
`python Antidote_score_stats.py --json league.json`, so a definition changed here should
change there too (shared/src/league/index.ts in the webserver repo).

Everything works on `rounds`: the chronological list of rounds from collect_rounds, each
a dict with i, date (ISO), label, block, round_in_block and entries {name: Score}.
"""

from Antidote_score_stats import ELO_K, ELO_START, MIN_GAMES, SCORE_SCALE

# A "best partner" or "toughest rival" needs at least this many shared rounds, and a
# partner's effect needs this many rounds both with and without them.
MIN_SHARED = 3
FORM_ROUNDS = 10


def as_rounds(played):
    """collect_rounds output -> plain round dicts in chronological order."""
    return [
        {
            "i": i,
            "date": key[0].isoformat(),
            "label": label,
            "block": key[1],
            "round_in_block": key[2],
            "entries": entries,
        }
        for i, (key, label, entries) in enumerate(played)
    ]


def mean(values):
    """Average, or 0 for nothing (the website's convention, so both sides agree)."""
    values = list(values)
    return sum(values) / len(values) if values else 0


def rate(values):
    """Average, or None when there is nothing to average."""
    values = list(values)
    return sum(values) / len(values) if values else None


def survived(entry):
    return entry[0] > 0


def player_order(rounds):
    """First appearance in file order (date block, then round), like the GameLog."""
    order = []
    for r in sorted(rounds, key=lambda r: (r["block"], r["round_in_block"])):
        for name in r["entries"]:
            if name not in order:
                order.append(name)
    return order


def elo_timeline(rounds):
    """Elo after every round each player sat in, plus each round's pre-game ratings.

    The same pairwise update as compute_elo (clamped scores, K/(n-1), applied after the
    pair loop); kept separate so compute_elo stays exactly as the report card uses it.
    """
    ratings = {}
    history = {}
    before = []
    for r in rounds:
        names = list(r["entries"])
        if len(names) < 2:
            before.append({})
            continue
        clamped = {n: max(r["entries"][n][0], 0) for n in names}
        for n in names:
            ratings.setdefault(n, ELO_START)
        before.append({n: ratings[n] for n in names})
        deltas = dict.fromkeys(names, 0.0)
        k = ELO_K / (len(names) - 1)
        for index, a in enumerate(names):
            for b in names[index + 1:]:
                expected = 1 / (1 + 10 ** ((ratings[b] - ratings[a]) / 400))
                actual = 0.5 if clamped[a] == clamped[b] else float(clamped[a] > clamped[b])
                deltas[a] += k * (actual - expected)
                deltas[b] -= k * (actual - expected)
        for n in names:
            ratings[n] += deltas[n]
            history.setdefault(n, []).append([r["i"], ratings[n]])
    return ratings, history, before


def per_player(rounds):
    """Each player's rounds in order: (round, entry, table size)."""
    mine = {}
    for r in rounds:
        count = len(r["entries"])
        for name, entry in r["entries"].items():
            mine.setdefault(name, []).append((r, entry, count))
    return mine


def player_extras(rounds, elo_history):
    """End cards, streaks, recent form and the clutch numbers, per player."""
    out = {}
    for name, games in per_player(rounds).items():
        best_live = best_die = run = 0
        for _, entry, _ in games:
            live = survived(entry)
            run = (run + 1 if run > 0 else 1) if live else (run - 1 if run < 0 else -1)
            best_live = max(best_live, run)
            best_die = max(best_die, -run)

        last = games[-FORM_ROUNDS:]
        elo = elo_history.get(name, [])
        # Elo change over the same last FORM_ROUNDS rounds: from the rating before the
        # first of them (the start rating if that was their first game ever).
        start = elo[-len(last) - 1][1] if len(elo) > len(last) else ELO_START
        form_scaled = mean(e[0] / n * SCORE_SCALE for _, e, n in last)
        career_scaled = mean(e[0] / n * SCORE_SCALE for _, e, n in games)

        # Bounce-back: the next round the same night, after a death or after surviving.
        after_death, after_live = [], []
        for (prev_r, prev, _), (cur_r, cur, _) in zip(games, games[1:]):
            if prev_r["date"] != cur_r["date"]:
                continue
            (after_live if survived(prev) else after_death).append(survived(cur))

        # The closer: each night's last round they played.
        last_of_night = {}
        for r, entry, _ in games:
            last_of_night[r["date"]] = (r, entry)
        closers = list(last_of_night.values())
        best = {r["i"]: max(e[0] for e in r["entries"].values()) for r, _ in closers}

        ends = {"S": 0, "X": 0}
        for _, entry, _ in games:
            if getattr(entry, "end", None):
                ends[entry.end] += 1

        out[name] = {
            "ends": ends,
            "endRate": (ends["S"] + ends["X"]) / len(games),
            "streaks": {"bestLive": best_live, "bestDie": best_die, "current": run},
            "form": {
                "n": len(last),
                "survival": mean(survived(e) for _, e, _ in last),
                "scaled": form_scaled,
                # Positive: playing above their career average lately. Negative: due.
                "heat": form_scaled - career_scaled,
                "eloChange": (elo[-1][1] if elo else ELO_START) - start,
            },
            "clutch": {
                "afterDeathN": len(after_death),
                "afterDeathSurvival": rate(after_death),
                "afterLiveN": len(after_live),
                "afterLiveSurvival": rate(after_live),
                "closerN": len(closers),
                "closerSurvival": rate(survived(e) for _, e in closers),
                "closerWins": sum(survived(e) and e[0] == best[r["i"]] for r, e in closers),
            },
        }
    return out


def head_to_head(rounds, order):
    """For every ordered pair: rounds together, and who finished ahead on clamped scores."""
    rows = []
    for a in order:
        for b in order:
            if a == b:
                continue
            both = [r for r in rounds if a in r["entries"] and b in r["entries"]]
            if not both:
                continue
            ahead = behind = 0
            for r in both:
                sa, sb = max(r["entries"][a][0], 0), max(r["entries"][b][0], 0)
                ahead += sa > sb
                behind += sa < sb
            rows.append({"a": a, "b": b, "rounds": len(both), "ahead": ahead,
                         "behind": behind, "level": len(both) - ahead - behind})
    return rows


def chemistry(rounds, order):
    """A player's survival with a partner at the table, and without them."""
    rows = []
    for a in order:
        mine = [r for r in rounds if a in r["entries"]]
        for b in order:
            if a == b:
                continue
            with_b = [r for r in mine if b in r["entries"]]
            if not with_b:
                continue
            without = [r for r in mine if b not in r["entries"]]
            rows.append({
                "player": a, "partner": b,
                "withN": len(with_b), "withSurvival": mean(survived(r["entries"][a]) for r in with_b),
                "withoutN": len(without), "withoutSurvival": mean(survived(r["entries"][a]) for r in without),
            })
    return rows


def _pick(rows, score, size):
    """Highest score; ties go to the bigger sample, then the name."""
    if not rows:
        return None
    return min(rows, key=lambda r: (-score(r), -size(r), r.get("partner") or r.get("b")))


def profiles(order, pairings, chem, games):
    """Best partner, toughest rival and favourite target for each player.

    Only players with MIN_GAMES rounds count as partners or rivals, so one lucky night
    with a guest doesn't make them anyone's nemesis.
    """
    out = {}
    for name in order:
        partners = [c for c in chem if c["player"] == name and games[c["partner"]] >= MIN_GAMES
                    and c["withN"] >= MIN_SHARED and c["withoutN"] >= MIN_SHARED]
        rivals = [p for p in pairings if p["a"] == name and games[p["b"]] >= MIN_GAMES
                  and p["rounds"] >= MIN_SHARED]
        partner = _pick(partners, lambda c: c["withSurvival"] - c["withoutSurvival"], lambda c: c["withN"])
        rival = _pick(rivals, lambda p: (p["behind"] - p["ahead"]) / p["rounds"], lambda p: p["rounds"])
        target = _pick(rivals, lambda p: (p["ahead"] - p["behind"]) / p["rounds"], lambda p: p["rounds"])
        out[name] = {
            "bestPartner": partner and {"name": partner["partner"],
                                        "lift": partner["withSurvival"] - partner["withoutSurvival"],
                                        "n": partner["withN"]},
            "toughestRival": rival and {"name": rival["b"], "ahead": rival["ahead"],
                                        "behind": rival["behind"], "n": rival["rounds"]},
            "favouriteTarget": target and {"name": target["b"], "ahead": target["ahead"],
                                           "behind": target["behind"], "n": target["rounds"]},
        }
    return out


def table_sizes(rounds):
    """Per table size: rounds, end cards, and each player's survival and scaled score."""
    sizes = {}
    for r in rounds:
        sizes.setdefault(len(r["entries"]), []).append(r)
    out = []
    for count, rs in sorted(sizes.items()):
        entries = [e for r in rs for e in r["entries"].values()]
        by_player = {}
        for r in rs:
            for name, e in r["entries"].items():
                by_player.setdefault(name, []).append(e)
        out.append({
            "count": count,
            "rounds": len(rs),
            "ends": {k: sum(getattr(e, "end", None) == k for e in entries) for k in ("S", "X")},
            "endRate": sum(bool(getattr(e, "end", None)) for e in entries) / len(entries),
            "byPlayer": {
                name: {"n": len(es), "survival": mean(survived(e) for e in es),
                       "scaled": mean(e[0] / count * SCORE_SCALE for e in es)}
                for name, es in by_player.items()
            },
        })
    return out


def night_swings(rounds, elo_history):
    """Each player's Elo change over each night they played, biggest first."""
    rows = []
    date_of = {r["i"]: r for r in rounds}
    for name, points in elo_history.items():
        previous = ELO_START
        nights = {}
        for i, rating in points:
            r = date_of[i]
            night = nights.setdefault(r["date"], {"label": r["label"], "start": previous, "rounds": 0})
            night["end"] = rating
            night["rounds"] += 1
            previous = rating
        for date, night in nights.items():
            rows.append({"player": name, "date": date, "label": night["label"],
                         "rounds": night["rounds"], "delta": night["end"] - night["start"]})
    rows.sort(key=lambda row: (-abs(row["delta"]), row["date"], row["player"]))
    return rows


def upsets(rounds, before):
    """The night's biggest upset: the finish the pre-game Elo thought least likely.

    Within a round every pair where one finished ahead (clamped scores) is a result; the
    upset is the one whose winner had the lowest expected score. Ties keep the first.
    """
    best = {}
    for r, ratings in zip(rounds, before):
        names = list(r["entries"])
        for index, a in enumerate(names):
            for b in names[index + 1:]:
                sa, sb = max(r["entries"][a][0], 0), max(r["entries"][b][0], 0)
                if sa == sb or not ratings:
                    continue
                winner, loser = (a, b) if sa > sb else (b, a)
                expected = 1 / (1 + 10 ** ((ratings[loser] - ratings[winner]) / 400))
                current = best.get(r["date"])
                if current is None or expected < current["expected"]:
                    best[r["date"]] = {"date": r["date"], "label": r["label"], "round": r["i"],
                                       "winner": winner, "loser": loser, "expected": expected}
    return [best[date] for date in sorted(best)]


def win_probability(ratings):
    """Elo's chance that a finishes ahead of b, for every pair."""
    return {
        a: {b: 1 / (1 + 10 ** ((rb - ra) / 400)) for b, rb in ratings.items() if b != a}
        for a, ra in ratings.items()
    }


def build_extras(played):
    rounds = as_rounds(played)
    order = player_order(rounds)
    ratings, elo_history, before = elo_timeline(rounds)
    pairings = head_to_head(rounds, order)
    chem = chemistry(rounds, order)
    return {
        "order": order,
        "rounds": rounds,
        "ratings": ratings,
        "eloHistory": elo_history,
        "playerExtras": player_extras(rounds, elo_history),
        "pairings": pairings,
        "chemistry": chem,
        "profiles": profiles(order, pairings, chem, {n: len(g) for n, g in per_player(rounds).items()}),
        "tableSizes": table_sizes(rounds),
        "nightSwings": night_swings(rounds, elo_history),
        "upsets": upsets(rounds, before),
        "winProbability": win_probability(ratings),
    }


def extras_sheets(extras, pd):
    """The extras as titled tables for AntidoteStats.xlsx (one write_blocks sheet each)."""
    players = extras["playerExtras"]
    order = extras["order"]
    r2 = lambda x: None if x is None else round(x, 3)

    form = pd.DataFrame([
        {
            "Player": name,
            "Elo": round(extras["ratings"].get(name, ELO_START)),
            f"Last {FORM_ROUNDS}: Survival": r2(players[name]["form"]["survival"]),
            f"Last {FORM_ROUNDS}: Score ({SCORE_SCALE}p)": r2(players[name]["form"]["scaled"]),
            "Heat (form - career)": r2(players[name]["form"]["heat"]),
            f"Elo change, last {FORM_ROUNDS}": round(players[name]["form"]["eloChange"], 1),
            "Best Survival Streak": players[name]["streaks"]["bestLive"],
            "Worst Death Streak": players[name]["streaks"]["bestDie"],
            "Current Streak": players[name]["streaks"]["current"],
        }
        for name in order
    ])
    clutch = pd.DataFrame([
        {
            "Player": name,
            "After a Death: Rounds": c["afterDeathN"],
            "After a Death: Survival": r2(c["afterDeathSurvival"]),
            "After Surviving: Rounds": c["afterLiveN"],
            "After Surviving: Survival": r2(c["afterLiveSurvival"]),
            "Last Round of Night: Rounds": c["closerN"],
            "Last Round of Night: Survival": r2(c["closerSurvival"]),
            "Last Round of Night: Wins": c["closerWins"],
        }
        for name in order
        for c in [players[name]["clutch"]]
    ])
    ends = pd.DataFrame([
        {"Player": name, "Syringe (S)": players[name]["ends"]["S"], "Toxin (X)": players[name]["ends"]["X"],
         "End-card Rate": r2(players[name]["endRate"])}
        for name in order
    ])
    ends_by_size = pd.DataFrame([
        {"Player Count": t["count"], "Rounds": t["rounds"], "Syringe (S)": t["ends"]["S"],
         "Toxin (X)": t["ends"]["X"], "End-card Rate": r2(t["endRate"])}
        for t in extras["tableSizes"]
    ])
    h2h = pd.DataFrame([
        {"Player": p["a"], "Opponent": p["b"], "Rounds": p["rounds"], "Ahead": p["ahead"],
         "Behind": p["behind"], "Level": p["level"],
         "Elo Win Chance": r2(extras["winProbability"].get(p["a"], {}).get(p["b"]))}
        for p in extras["pairings"]
    ])
    chem = pd.DataFrame([
        {"Player": c["player"], "Partner": c["partner"], "Rounds With": c["withN"],
         "Survival With": r2(c["withSurvival"]), "Rounds Without": c["withoutN"],
         "Survival Without": r2(c["withoutSurvival"])}
        for c in extras["chemistry"]
    ])
    prof = pd.DataFrame([
        {
            "Player": name,
            "Best Partner": p["bestPartner"] and p["bestPartner"]["name"],
            "Partner Lift": p["bestPartner"] and r2(p["bestPartner"]["lift"]),
            "Toughest Rival": p["toughestRival"] and p["toughestRival"]["name"],
            "Favourite Target": p["favouriteTarget"] and p["favouriteTarget"]["name"],
        }
        for name in order
        for p in [extras["profiles"][name]]
    ])
    swings = pd.DataFrame([
        {"Date": s["label"], "Player": s["player"], "Rounds": s["rounds"], "Elo Change": round(s["delta"], 1)}
        for s in extras["nightSwings"]
    ])
    upsets = pd.DataFrame([
        {"Date": u["label"], "Winner": u["winner"], "Over": u["loser"], "Elo Gave Them": r2(u["expected"])}
        for u in extras["upsets"]
    ])
    names = sorted(extras["eloHistory"], key=order.index)
    timeline = []
    current = {}
    by_round = {}
    for name, points in extras["eloHistory"].items():
        for i, rating in points:
            by_round.setdefault(i, {})[name] = rating
    for r in extras["rounds"]:
        current.update(by_round.get(r["i"], {}))
        timeline.append({"Date": r["label"], "Round": r["round_in_block"] + 1,
                         **{n: round(current[n], 1) if n in current else None for n in names}})
    return {
        "Form & Clutch": [("Form and streaks", form), ("Clutch", clutch), ("Profiles", prof)],
        "Rivals": [("Head-to-head (clamped scores) and Elo win chance", h2h), ("Chemistry", chem)],
        "Endings & Nights": [("End cards by player", ends), ("End cards by table size", ends_by_size),
                             ("Biggest Elo swing in a night", swings), ("Upset of the night", upsets)],
        "Elo History": [("Elo after every round", pd.DataFrame(timeline))],
    }
