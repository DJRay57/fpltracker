"""How many points a player might score, and what that means for a tie.

Both the live tracker and the pre-gameweek predictions need the same thing:
turn a set of expected-points figures into a believable distribution of final
scores. This is that shared machinery, so the two can't drift apart.

The approach throughout is empirical rather than parametric. A fantasy score
is not a normal curve around an average -- it is mostly small numbers with an
occasional lump when someone returns, and the size of that lump depends on who
you are. So instead of assuming a shape, a player is simulated in two stages:

  does he feature   the expected-points figure is an unconditional average
                    that already discounts the chance of being left out, so
                    a man on 0.3 is largely a prediction that he won't play
  how does he do    if he features, his score is drawn from what players in
                    the same position on a similar expectation actually
                    scored, across every gameweek played so far

If he doesn't feature the manager isn't left with a hole -- the bench covers
it, as the real rules do.

Nothing here is fitted or tuned by hand; every number comes out of the season's
own results.
"""

import math
import random
import statistics

# Teammates' returns arrive together -- a clean sheet lifts a whole back line,
# a goal flatters everyone involved. Measured across a completed gameweek by
# grouping player residuals by club, this sits around 0.10.
TEAM_RHO = 0.10
MIN_POOL = 20
# Roughly how often an outfield substitute gets on at all. Applied to whatever
# is left of a match, it gives the chance a man who didn't start still appears.
SUB_APPEARANCE = 0.5

MINIMUMS = {"GK": 1, "DEF": 3, "MID": 2, "FWD": 1}


def legal_xi(types):
    counts = {k: 0 for k in MINIMUMS}
    for t in types:
        counts[t] += 1
    return counts["GK"] == 1 and all(counts[k] >= v for k, v in MINIMUMS.items())


def eligible_cover(xi_types, index, bench):
    """Bench players who could legally replace the man at `index`.

    `bench` is [(slot, position, points, played)] in bench order. A keeper only
    covers a keeper, the replacement has to leave a legal XI, and anyone who
    never came on isn't available at all.
    """
    out = []
    for slot, pos, points, played in bench:
        if not played:
            continue
        if (xi_types[index] == "GK") != (pos == "GK"):
            continue
        trial = list(xi_types)
        trial[index] = pos
        if legal_xi(trial):
            out.append((slot, points))
    return out


class Pools:
    """Actual scores of comparable players, keyed on position and expectation."""

    def __init__(self, samples):
        # samples: [(expected_points, points_scored, position)]
        self.samples = [s for s in samples if s[2]]
        self._cache = {}

    def __len__(self):
        return len(self.samples)

    def comparable(self, expected, position, minimum=MIN_POOL):
        """Scores of players in the same position on a similar expectation.

        Position matters as much as expectation: four points for a clean sheet
        is a lump that no forward's distribution contains, and a forward's
        double-return tail is one no defender has.
        """
        key = (round(expected, 1), position)
        if key in self._cache:
            return self._cache[key]
        for pool in ([s for s in self.samples if s[2] == position], self.samples):
            width = 1.0
            while width < 8:
                got = sorted(p for e, p, _ in pool if abs(e - expected) <= width)
                if len(got) >= minimum:
                    self._cache[key] = got
                    return got
                width += 0.5
        got = sorted(p for _, p, _ in self.samples) or [0]
        self._cache[key] = got
        return got

    def play_prob(self, expected, pool):
        """P(features), set so the average still matches the expected figure."""
        avg = statistics.mean(pool) if pool else 0.0
        return min(1.0, expected / avg) if avg > 0 else 0.0

    def player(self, expected, position):
        """(play_probability, score_pool) for one player."""
        pool = self.comparable(expected, position)
        return self.play_prob(expected, pool), pool


def sample_total(rng, pools, base, squad, team_u):
    """One simulated final score for a side.

    `squad` holds everyone still to come, each a dict of:
        ep      expected points
        pos     position
        share   how much of a match is left for him
        team    his club, so teammates can move together
        p_play  optional override for his chance of featuring -- used when
                he has already failed to start a match that's under way
        cover   [(bench_slot, points)] that could legally replace him, in
                bench order, already filtered for position and formation

    A man who doesn't feature is replaced by his cover, exactly as the real
    substitution rules do. Which bench player is eligible depends on who is
    coming out: a keeper only covers a keeper, and a side can't drop below
    three defenders, so it isn't simply the next name on the list.
    """
    total = float(base)
    used = set()
    for man in squad:
        play = man.get("p_play")
        pool = pools.comparable(man["ep"], man["pos"])
        if play is None:
            play = pools.play_prob(man["ep"], pool)
        if rng.random() >= play:
            for slot, points in man.get("cover", ()):
                if slot not in used:
                    used.add(slot)
                    total += points
                    break
            continue
        if man["team"] not in team_u:
            team_u[man["team"]] = rng.random()
        u = TEAM_RHO * team_u[man["team"]] + (1.0 - TEAM_RHO) * rng.random()
        draw = pool[min(len(pool) - 1, int(u * len(pool)))]

        # Points arrive in lumps at moments, not smoothly across ninety
        # minutes, so how much is *expected* falls off with the clock but how
        # much it might *swing* does not fall off nearly as fast. Scaling both
        # by the time left made late-game ranges far too tight -- a man with
        # ten minutes to play can still score. Mean scales with the time
        # remaining, spread with its square root, as an arrival process does.
        share = man["share"]
        if share >= 0.999:
            total += draw
        else:
            middle = statistics.mean(pool) if pool else 0.0
            total += middle * share + (draw - middle) * math.sqrt(share)
    return total


def score_distribution(pools, squad, base=0, sims=4000, seed=11):
    """A manager's plausible final scores, as a list to sample from later."""
    rng = random.Random(seed)
    return [round(sample_total(rng, pools, base, squad, {})) for _ in range(sims)]


def odds_from(dist_a, dist_b, rng=None, pairs=20000):
    """Win / draw / loss for A, pairing draws from two score distributions."""
    rng = rng or random.Random(5)
    wins = draws = 0
    for _ in range(pairs):
        a, b = rng.choice(dist_a), rng.choice(dist_b)
        if a > b:
            wins += 1
        elif a == b:
            draws += 1
    return _tidy(wins / pairs * 100, draws / pairs * 100,
                 (pairs - wins - draws) / pairs * 100)


def match_odds(pools, side_a, side_b, sims=10000, seed=7):
    """Win / draw / loss for A, simulating both sides together.

    Each side is (already_banked, squad_still_to_come). Both are drawn in the
    same trial so that players sharing a club move together.
    """
    base_a, squad_a = side_a
    base_b, squad_b = side_b
    if not squad_a and not squad_b:            # nothing left: it's decided
        if base_a == base_b:
            return 0.0, 100.0, 0.0
        return (100.0, 0.0, 0.0) if base_a > base_b else (0.0, 0.0, 100.0)

    rng = random.Random(seed)                  # fixed: same data, same odds
    wins = draws = 0
    for _ in range(sims):
        team_u = {}
        a = round(sample_total(rng, pools, base_a, squad_a, team_u))
        b = round(sample_total(rng, pools, base_b, squad_b, team_u))
        if a > b:
            wins += 1
        elif a == b:
            draws += 1

    win, draw = wins / sims * 100, draws / sims * 100
    loss = 100.0 - win - draw
    # While anyone is still to play, don't claim certainty either way
    win, loss = min(99.0, max(1.0, win)), min(99.0, max(1.0, loss))
    return _tidy(win, max(0.0, 100.0 - win - loss), loss)


def _tidy(win, draw, loss):
    """Whole percents that still add up to 100."""
    vals = [round(win), round(draw), round(loss)]
    vals[vals.index(max(vals))] += 100 - sum(vals)
    return float(vals[0]), float(vals[1]), float(vals[2])


def gather_samples(fetch, draft_base, expectations, players, gameweeks):
    """Every starter's score across the gameweeks given.

    Keyed on the player's *current* expectation, which is a stable measure of
    quality but not what was expected of him at the time -- a player whose form
    has turned is bucketed by where he is now, not where he was.
    """
    out = []
    for gw in gameweeks:
        try:
            elements = fetch(f"{draft_base}/event/{gw}/live")["elements"]
        except Exception:  # noqa: BLE001 - a missing gameweek just means less data
            continue
        for key, data in elements.items():
            if not data["stats"].get("starts"):
                continue
            pid = int(key)
            out.append((expectations.get(pid, 0.0), data["stats"]["total_points"],
                        players.get(pid, {}).get("type")))
    return out
