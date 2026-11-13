"""How sure can we be? Confidence intervals, p-values and multiple-comparison control.

Shared with the Antidote web app (shared/src/league/stats.ts in the webserver repo), whose
tests check it number for number against `--json`: both use the same seeded generator, so
the bootstrap resamples are identical. Change a method here, change it there.

Methods:
- Bootstrap by table: a table (one date block in the score file: one group playing consecutive
  rounds) shares players and dynamics, so whole tables are resampled with replacement (kept in
  date order so Elo replays sensibly). Every stat is recomputed on each resample; 95% intervals
  are the 2.5th and 97.5th percentiles (linear interpolation, numpy's default). "Chance of #1"
  is the share of resamples a player tops.
- Versus the league: the bootstrap distribution of (player - league average) gives a
  two-sided p-value, 2 x the smaller tail at zero (+1 smoothing).
- Robustness: the same p-values are also worked out with a bootstrap of single rounds. A
  difference is "firm" when both agree (q < ALPHA under each) and "borderline" when only one
  does: which clustering is right is a judgement call, so we show where it matters.
- Exact tests: head-to-head is a two-sided sign test on rounds one finished ahead (level
  rounds dropped); chemistry, bounce-back and the closer use Fisher's exact test on survived /
  died counts; hot-or-due is a permutation test of the last FORM_ROUNDS rounds vs the rest.
- Many players and pairs are tested at once, so each family of p-values also gets
  Benjamini-Hochberg q-values (false discovery rate).
Only players with MIN_GAMES rounds over at least MIN_TABLES tables are tested: resampling one
or two tables says nothing. "Chance of #1" is among the regulars, like the report card.
"""

import math

from Antidote_score_stats import CANONICAL_PLAYERS, ELO_K, ELO_START, MIN_GAMES, SCORE_SCALE, scoring_size
from league_extras import FORM_ROUNDS, player_order

BOOTSTRAP_SAMPLES = 2000
PERMUTATIONS = 2000
STATS_SEED = 1729
ALPHA = 0.05
MIN_TABLES = 3

AWARD_KEYS = ["winRate", "survivalRate", "scaledScore", "elo", "teamPlayer", "snake",
              "soloWins", "soloLosses", "scaledHand", "ends"]
ASCENDING = {"snake"}
NO_BASELINE = {"soloWins", "soloLosses", "ends"}
MASK = 0xFFFFFFFF


def mulberry32(seed):
    """A small seeded generator, bit-for-bit the same as the website's. Returns [0, 1)."""
    state = [seed & MASK]

    def imul(x, y):
        return (x * y) & MASK

    def rand():
        state[0] = (state[0] + 0x6D2B79F5) & MASK
        a = state[0]
        t = imul(a ^ (a >> 15), 1 | a)
        t = ((t + imul(t ^ (t >> 7), 61 | t)) & MASK) ^ t
        return ((t ^ (t >> 14)) & MASK) / 4294967296

    return rand


def quantile(sorted_values, q):
    if not sorted_values:
        return None
    pos = (len(sorted_values) - 1) * q
    f = math.floor(pos)
    c = min(f + 1, len(sorted_values) - 1)
    return sorted_values[f] + (sorted_values[c] - sorted_values[f]) * (pos - f)


def bh_adjust(ps):
    """Benjamini-Hochberg q-values in input order; None stays None and isn't counted."""
    idx = sorted(((p, i) for i, p in enumerate(ps) if p is not None))
    m = len(idx)
    out = [None] * len(ps)
    running = 1
    for k in range(m - 1, -1, -1):
        running = min(running, idx[k][0] * m / (k + 1))
        out[idx[k][1]] = min(1, running)
    return out


_log_fact = [0.0]


def _lf(n):
    while len(_log_fact) <= n:
        i = len(_log_fact)
        _log_fact.append(_log_fact[i - 1] + math.log(i))
    return _log_fact[n]


def _log_choose(n, k):
    return _lf(n) - _lf(k) - _lf(n - k)


def sign_test(k, n):
    """Two-sided exact sign test: k successes of n at p = 1/2."""
    if n == 0:
        return 1
    tail = 0
    for i in range(min(k, n - k) + 1):
        tail += math.exp(_log_choose(n, i) - n * math.log(2))
    return min(1, 2 * tail)


def fisher_exact(a, b, c, d):
    """Two-sided Fisher's exact test for [[a, b], [c, d]]."""
    r1, r2, c1 = a + b, c + d, a + c
    n = r1 + r2

    def prob(x):
        return math.exp(_log_choose(c1, x) + _log_choose(n - c1, r1 - x) - _log_choose(n, r1))

    seen = prob(a)
    p = 0
    for x in range(max(0, r1 + c1 - n), min(r1, c1) + 1):
        px = prob(x)
        if px <= seen * (1 + 1e-7):
            p += px
    return min(1, p)


def boot_p(diffs):
    if not diffs:
        return None
    le = sum(d <= 0 for d in diffs)
    ge = sum(d >= 0 for d in diffs)
    # +1 so the smallest reportable p is about 1/B, never an impossible 0.
    return min(1, 2 * (min(le, ge) + 1) / (len(diffs) + 1))


def mean(xs):
    total = 0
    for x in xs:
        total += x
    return total / len(xs)


def elo_expected(a, b):
    return 1 / (1 + 10 ** ((b - a) / 400))


def sample_stats(rounds):
    """Every award's value for each player (and the league), recomputed on a list of rounds."""
    acc = {}
    lg = dict(n=0, won=0, surv=0, norm=0, hand=0, peers=0, peersN=0, snake=0, snakeN=0)
    sizes = {}
    ratings = {}
    for r in rounds:
        entries = list(r["entries"].items())
        count = len(entries)
        scale = scoring_size(count)
        survivors = sum(e[0] > 0 for _, e in entries)
        best = max(e[0] for _, e in entries)
        size = sizes.setdefault(count, [0, 0])
        size[0] += survivors
        size[1] += count
        for name, e in entries:
            a = acc.setdefault(name, dict(games=0, won=0, surv=0, norm=0, hand=0, peers=0, peersN=0,
                                          snake=0, snakeN=0, solo=0, soloLoss=0, ends=0))
            live = e[0] > 0
            won = live and e[0] == best
            a["games"] += 1
            a["won"] += won
            a["surv"] += live
            a["norm"] += e[0] / scale
            a["hand"] += e[1] / scale
            if live and survivors == 1 and count > 2:
                a["solo"] += 1
            if not live and survivors == count - 1 and count > 2:
                a["soloLoss"] += 1
            if getattr(e, "end", None):
                a["ends"] += 1
            lg["n"] += 1
            lg["won"] += won
            lg["surv"] += live
            lg["norm"] += e[0] / scale
            lg["hand"] += e[1] / scale
            if count > 1:
                peers = (survivors - live) / (count - 1)
                a["peers"] += peers
                a["peersN"] += 1
                lg["peers"] += peers
                lg["peersN"] += 1
                if live:
                    a["snake"] += peers
                    a["snakeN"] += 1
                    lg["snake"] += peers
                    lg["snakeN"] += 1
        if count >= 2:
            names = [n for n, _ in entries]
            for n in names:
                ratings.setdefault(n, ELO_START)
            deltas = dict.fromkeys(names, 0.0)
            k = ELO_K / (count - 1)
            for i in range(count):
                for j in range(i + 1, count):
                    a_, b_ = names[i], names[j]
                    sa, sb = max(r["entries"][a_][0], 0), max(r["entries"][b_][0], 0)
                    expected = 1 / (1 + 10 ** ((ratings[b_] - ratings[a_]) / 400))
                    actual = 0.5 if sa == sb else (1 if sa > sb else 0)
                    deltas[a_] += k * (actual - expected)
                    deltas[b_] -= k * (actual - expected)
            for n in names:
                ratings[n] += deltas[n]

    stats = {}
    for name, a in acc.items():
        stats[name] = {
            "games": a["games"],
            "winRate": a["won"] / a["games"],
            "survivalRate": a["surv"] / a["games"],
            "scaledScore": a["norm"] / a["games"] * SCORE_SCALE,
            "elo": ratings.get(name),
            "teamPlayer": a["peers"] / a["peersN"] if a["peersN"] else None,
            "snake": a["snake"] / a["snakeN"] if a["snakeN"] else None,
            "soloWins": a["solo"],
            "soloLosses": a["soloLoss"],
            "scaledHand": a["hand"] / a["games"] * SCORE_SCALE,
            "ends": a["ends"],
        }
    league = {
        "winRate": lg["won"] / lg["n"],
        "survivalRate": lg["surv"] / lg["n"],
        "scaledScore": lg["norm"] / lg["n"] * SCORE_SCALE,
        "elo": ELO_START,
        "teamPlayer": lg["peers"] / lg["peersN"] if lg["peersN"] else None,
        "snake": lg["snake"] / lg["snakeN"] if lg["snakeN"] else None,
        "soloWins": None, "soloLosses": None, "ends": None,
        "scaledHand": lg["hand"] / lg["n"] * SCORE_SCALE,
    }
    return {"stats": stats, "league": league,
            "sizes": {str(c): s / t for c, (s, t) in sizes.items()}}


def interval(xs):
    s = sorted(xs)
    return {"lo": quantile(s, ALPHA / 2), "hi": quantile(s, 1 - ALPHA / 2)}


def build_uncertainty(rounds, samples=BOOTSTRAP_SAMPLES, permutations=PERMUTATIONS, seed=STATS_SEED):
    # Tables: consecutive rounds from one date block (rounds arrive sorted by date, block, round).
    tables = []
    for r in rounds:
        if tables and tables[-1][0]["date"] == r["date"] and tables[-1][0]["block"] == r["block"]:
            tables[-1].append(r)
        else:
            tables.append([r])
    order = player_order(rounds)
    base = sample_stats(rounds)
    tables_of = lambda n: sum(any(n in r["entries"] for r in rs) for rs in tables)
    qualified = [n for n in order
                 if base["stats"][n]["games"] >= MIN_GAMES and tables_of(n) >= MIN_TABLES]
    contenders = {n for n in qualified if n in CANONICAL_PLAYERS}
    size_keys = list(base["sizes"])

    def boot(clusters, rand, full):
        """Resample clusters `samples` times; `full` also keeps intervals, #1 and win chances."""
        values = {k: {n: [] for n in qualified} for k in AWARD_KEYS}
        diffs = {k: {n: [] for n in qualified} for k in AWARD_KEYS}
        top = {k: {n: 0 for n in qualified} for k in AWARD_KEYS}
        size_vals = {k: [] for k in size_keys}
        win_vals = {a: {b: [] for b in qualified if b != a} for a in qualified}
        N = len(clusters)
        for _ in range(samples):
            idx = sorted(math.floor(rand() * N) for _ in range(N))
            rs = [r for k in idx for r in clusters[k]]
            sm = sample_stats(rs)
            for key in AWARD_KEYS:
                best = None
                winners = []
                for n in qualified:
                    v = sm["stats"].get(n, {}).get(key)
                    if v is None:
                        continue
                    ref = sm["league"][key]
                    if key not in NO_BASELINE and ref is not None:
                        diffs[key][n].append(v - ref)
                    if not full:
                        continue
                    values[key][n].append(v)
                    if n not in contenders:
                        continue
                    if best is None or (v < best if key in ASCENDING else v > best):
                        best, winners = v, [n]
                    elif v == best:
                        winners.append(n)
                for w in winners:
                    top[key][w] += 1 / len(winners)
            if not full:
                continue
            for k in size_keys:
                if k in sm["sizes"]:
                    size_vals[k].append(sm["sizes"][k])
            for a in qualified:
                ra = sm["stats"].get(a, {}).get("elo")
                if ra is None:
                    continue
                for b in qualified:
                    rb = sm["stats"].get(b, {}).get("elo")
                    if a != b and rb is not None:
                        win_vals[a][b].append(elo_expected(ra, rb))
        return values, diffs, top, size_vals, win_vals

    values, diffs, top, size_vals, win_vals = boot(tables, mulberry32(seed), True)
    _, round_diffs, _, _, _ = boot([[r] for r in rounds], mulberry32(seed + 1), False)

    awards = {}
    for key in AWARD_KEYS:
        ps = [None if key in NO_BASELINE else boot_p(diffs[key][n]) for n in qualified]
        pr = [None if key in NO_BASELINE else boot_p(round_diffs[key][n]) for n in qualified]
        qs, qr = bh_adjust(ps), bh_adjust(pr)
        awards[key] = {
            n: {**interval(values[key][n]), "pTop": top[key][n] / samples, "p": ps[i], "q": qs[i],
                "pRound": pr[i], "qRound": qr[i]}
            for i, n in enumerate(qualified)
        }

    h2h = []
    for i, a in enumerate(qualified):
        for b in qualified[i + 1:]:
            ahead = behind = 0
            for r in rounds:
                if a not in r["entries"] or b not in r["entries"]:
                    continue
                sa, sb = max(r["entries"][a][0], 0), max(r["entries"][b][0], 0)
                ahead += sa > sb
                behind += sa < sb
            if ahead + behind:
                h2h.append({"a": a, "b": b, "ahead": ahead, "behind": behind,
                            "p": sign_test(ahead, ahead + behind), "q": 1})
    for row, q in zip(h2h, bh_adjust([x["p"] for x in h2h])):
        row["q"] = q

    chemistry = []
    for a in qualified:
        for b in qualified:
            if a == b:
                continue
            ws = wd = os_ = od = 0
            for r in rounds:
                if a not in r["entries"]:
                    continue
                live = r["entries"][a][0] > 0
                if b in r["entries"]:
                    ws, wd = ws + live, wd + (not live)
                else:
                    os_, od = os_ + live, od + (not live)
            p = fisher_exact(ws, wd, os_, od) if ws + wd > 0 and os_ + od > 0 else None
            chemistry.append({"player": a, "partner": b, "p": p, "q": None})
    for row, q in zip(chemistry, bh_adjust([x["p"] for x in chemistry])):
        row["q"] = q

    def mine(n):
        return [r for r in rounds if n in r["entries"]]

    def family(ps):
        qs = bh_adjust(ps)
        return {n: {"p": ps[i], "q": qs[i]} for i, n in enumerate(qualified)}

    def bounce(n):
        rs = mine(n)
        ds = dd = ls = ld = 0
        for prev, cur in zip(rs, rs[1:]):
            if prev["date"] != cur["date"]:
                continue
            live = cur["entries"][n][0] > 0
            if prev["entries"][n][0] > 0:
                ls, ld = ls + live, ld + (not live)
            else:
                ds, dd = ds + live, dd + (not live)
        return fisher_exact(ds, dd, ls, ld) if ds + dd > 0 and ls + ld > 0 else None

    def closer(n):
        rs = mine(n)
        last_of = {}
        for r in rs:
            last_of[r["date"]] = r["i"]
        cs = cd = os_ = od = 0
        for r in rs:
            live = r["entries"][n][0] > 0
            if last_of[r["date"]] == r["i"]:
                cs, cd = cs + live, cd + (not live)
            else:
                os_, od = os_ + live, od + (not live)
        return fisher_exact(cs, cd, os_, od) if cs + cd > 0 and os_ + od > 0 else None

    def heat(n):
        xs = [r["entries"][n][0] / scoring_size(len(r["entries"])) * SCORE_SCALE for r in mine(n)]
        if len(xs) < FORM_ROUNDS + 3:
            return None

        def stat(v):
            return mean(v[-FORM_ROUNDS:]) - mean(v[:-FORM_ROUNDS])

        obs = abs(stat(xs))
        r = mulberry32((seed + 7919 * (order.index(n) + 1)) & MASK)
        hits = 0
        v = list(xs)
        for _ in range(permutations):
            for i in range(len(v) - 1, 0, -1):
                j = math.floor(r() * (i + 1))
                v[i], v[j] = v[j], v[i]
            if abs(stat(v)) >= obs - 1e-12:
                hits += 1
        return (hits + 1) / (permutations + 1)

    return {
        "samples": samples,
        "tables": len(tables),
        "qualified": qualified,
        "league": base["league"],
        "awards": awards,
        "tableSizes": {k: interval(size_vals[k]) for k in size_keys},
        "winProbability": {a: {b: interval(v) for b, v in row.items()} for a, row in win_vals.items()},
        "headToHead": h2h,
        "chemistry": chemistry,
        "bounceBack": family([bounce(n) for n in qualified]),
        "closer": family([closer(n) for n in qualified]),
        "heat": family([heat(n) for n in qualified]),
    }


AWARD_TITLES = {
    "winRate": "Born to win (win rate)",
    "survivalRate": "Best survival instincts (survival rate)",
    "scaledScore": f"Scoring champion ({SCORE_SCALE}-player scale)",
    "elo": "Elo power rankings",
    "teamPlayer": "Best team player (peer survival, all rounds)",
    "snake": "Biggest snake (peer survival when they survived)",
    "soloWins": "Free thinker (solo wins)",
    "soloLosses": "Overfitter (solo losses)",
    "scaledHand": f"Boldest player (hand, {SCORE_SCALE}-player scale)",
    "ends": "Syringe & toxin endings",
}


def verdict(stat):
    """firm: significant resampling tables AND single rounds; borderline: only one of them."""
    hits = sum(q is not None and q < ALPHA for q in (stat.get("q"), stat.get("qRound")))
    return {2: "firm", 1: "borderline"}.get(hits, "")


def significance_sheets(unc, pd):
    """The uncertainty as titled tables for AntidoteStats.xlsx."""
    r3 = lambda x: None if x is None else round(x, 3)
    award_blocks = []
    for key in AWARD_KEYS:
        rows = [
            {"Player": n, "95% CI Low": r3(s["lo"]), "95% CI High": r3(s["hi"]),
             "Chance of #1": r3(s["pTop"]),
             "p vs League": r3(s["p"]), "q (BH)": r3(s["q"]),
             "p (rounds)": r3(s["pRound"]), "q (rounds)": r3(s["qRound"]),
             "Verdict": verdict(s)}
            for n, s in unc["awards"][key].items()
        ]
        award_blocks.append((AWARD_TITLES[key], pd.DataFrame(rows)))
    tests = [
        ("Head-to-head sign test (level rounds dropped)", pd.DataFrame([
            {"Player": x["a"], "Opponent": x["b"], "Ahead": x["ahead"], "Behind": x["behind"],
             "p": r3(x["p"]), "q (BH)": r3(x["q"])} for x in unc["headToHead"]])),
        ("Chemistry: survival with vs without (Fisher)", pd.DataFrame([
            {"Player": x["player"], "Partner": x["partner"], "p": r3(x["p"]), "q (BH)": r3(x["q"])}
            for x in unc["chemistry"]])),
        ("Bounce-back, closer and hot-or-due", pd.DataFrame([
            {"Player": n,
             "Bounce-back p": r3(unc["bounceBack"][n]["p"]), "Bounce-back q": r3(unc["bounceBack"][n]["q"]),
             "Closer p": r3(unc["closer"][n]["p"]), "Closer q": r3(unc["closer"][n]["q"]),
             "Hot/due p": r3(unc["heat"][n]["p"]), "Hot/due q": r3(unc["heat"][n]["q"])}
            for n in unc["qualified"]])),
    ]
    note = pd.DataFrame([{
        "Method": f"Bootstrap by table ({unc['samples']} resamples of {unc['tables']} tables, seed "
                  f"{STATS_SEED}); 95% percentile intervals; p vs League = two-sided bootstrap; "
                  "q = Benjamini-Hochberg within each block. Firm = q < 0.05 resampling tables and "
                  "single rounds; borderline = only one."}])
    return {
        "Significance": [("How to read this", note)] + award_blocks,
        "Significance Tests": tests,
    }
