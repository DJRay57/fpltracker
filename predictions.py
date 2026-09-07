"""
Two predictive features, sharing the same per-manager projection:

1. H2H WIN PROBABILITIES for the upcoming (not-yet-played) gameweek --
   projects each manager's starting XI score as the sum of `ep_next`
   (FPL's own fixture/form-adjusted "expected points next round") for
   their most recently known starting XI, then simulates the gameweek
   player by player -- see scoring_model.py -- to get win/draw/loss.

2. SEASON FINISHING POSITION -- Monte Carlo simulation of the rest of
   the season (the full fixture list already exists for all 38 GWs in
   the league's `matches`), replaying already-played gameweeks exactly
   as they happened and simulating the rest from each manager's
   projected mean score.

Methodology caveats (stated on the page, not hidden):
- `ep_next` isn't exposed by the Draft API -- pulled from the classic
  FPL API instead (fantasy.premierleague.com), which shares element ids.
- Projections start from each manager's last known starting XI (from
  target_gw, since next_gw's picks aren't queryable until its deadline
  passes), then apply any accepted trade effective for next_gw as a
  like-for-like swap in that same XI slot -- if the trade dropped a
  bench player, or the manager rearranges their XI beyond what the
  trade implies, that's not captured. A bench-only lineup shuffle with
  no trade behind it also isn't accounted for.
- Scores are no longer assumed to be normal around a projection with a
  hand-picked spread. Each starter is simulated from what comparable
  players actually scored, so the spread comes out of the data instead
  of being asserted, and the lumpy shape of real scoring survives.
- The season simulation does not assume today's table is the truth. Each
  trial draws how good every manager actually is, because with only a few
  gameweeks played the observed spread is mostly sampling noise -- early
  on it is smaller than chance alone would produce. A squad's measurable
  edge decays as squads churn and stops counting at the January re-draft.
  As real gameweeks accumulate the evidence takes over on its own.

Writes seasons/2026-27/h2h_predictions.md and seasons/2026-27/season_projection.md.
"""

import requests
import os
import math
import random
import statistics
import json

import scoring_model

DRAFT_BASE = "https://draft.premierleague.com/api"
CLASSIC_BASE = "https://fantasy.premierleague.com/api"
LEAGUE_ID = 1139
SEASON = "2026-27"
SEASON_DIR = os.path.join("seasons", SEASON)

N_TRIALS = 5000        # Monte Carlo trials for season projection
SCORE_SIMS = 4000      # simulated gameweeks per manager, sampled from later
# Fixed, so the same data gives the same table. Unseeded, 5000 trials left
# enough sampling noise to move a manager two points between back-to-back
# runs on identical data -- which reads as movement, commits on every cron
# run and redeploys the site for nothing. Genuine change still shows: the
# squads and their distributions feed in from upstream.
SEASON_SEED = 11

# ---------------------------------------------------------------- projection
# The old projection fixed each manager's scoring rate at what he had averaged
# so far and replayed it for every remaining gameweek. Over 35 weeks the noise
# averages out, so the table it produced was close to deterministic: a manager
# three points off the pace after three games came out with no realistic path
# to the top three. That is not what three games tells you.
#
# What three games actually tells you: the spread between managers' points per
# game is SMALLER than chance alone would produce (5.8 observed against 6.6
# expected from week-to-week variance / sqrt(3)). There is no detectable
# difference between anyone in this league yet, so projecting today's table
# forward is projecting noise.
#
# Three things now go into a manager's rate for a given gameweek:
#   the league average       everyone starts from the same place
#   persistent skill         drawn per trial, because we do not know it -- and
#                            after three games the data cannot distinguish it
#                            from zero, so its posterior is essentially prior
#   a current squad edge     real and measurable from expected points, but it
#                            erodes as squads churn and it is wiped by the
#                            re-draft
REDRAFT_GW = 24          # end-of-January re-draft; GW23 is 30 Jan, GW24 6 Feb
SQUAD_HALF_LIFE_FALLBACK = 8.0   # only until there are transactions to measure
# After the re-draft nobody keeps their assets, so a squad edge does not
# survive it. What does survive is the manager: he takes his selection habits
# with him, not his players.
#
# That is measurable from this season alone, without reaching for a previous
# one -- previous seasons are irrelevant here anyway, since the league
# re-drafts and last year's table describes squads nobody still owns. The
# measurable part is bench waste: points a manager left sitting on his bench.
# It is a decision, repeated weekly, and it survives any re-draft.
#
# Its spread is shrunk the same way the table is, because three gameweeks of
# bench waste is noisy too. Unlike overall scoring, a real difference does
# come through -- roughly a third of the spread is signal -- which is why this
# is a measurement rather than a guess.
#
# It is a FLOOR on manager skill, not all of it: waiver work, trades and
# start/sit calls within the XI are skill too and are not counted here. Erring
# low keeps the model from asserting differences it cannot see.
SKILL_FALLBACK_SD = 2.0   # only if the bench cache is missing entirely


def fetch(url):
    r = requests.get(url, timeout=10)
    r.raise_for_status()
    return r.json()


def normal_cdf(x, mean, sigma):
    return 0.5 * (1 + math.erf((x - mean) / (sigma * math.sqrt(2))))


def win_draw_loss(mean_a, mean_b, sigma):
    """P(A wins), P(draw), P(B wins), assuming actual scores are
    ~Normal(mean, sigma) each and scores are integers (continuity
    correction of +/-0.5 for the draw band)."""
    diff_mean = mean_a - mean_b
    diff_sigma = sigma * math.sqrt(2)
    p_b_wins = normal_cdf(-0.5, diff_mean, diff_sigma)
    p_not_a_wins = normal_cdf(0.5, diff_mean, diff_sigma)
    p_draw = p_not_a_wins - p_b_wins
    p_a_wins = 1 - p_not_a_wins
    return p_a_wins, p_draw, p_b_wins


def squad_half_life(transactions, n_managers, squad_size=15):
    """How many gameweeks it takes a squad edge to halve, measured.

    Taken from how fast squads actually turn over: accepted transactions
    per manager per gameweek, as a fraction of the squad. If a squad
    changes 11% of itself a week, what made it good three months ago has
    largely gone.

    It is a rate of change, not strictly a rate of decay -- a good manager
    churns to keep an edge, not to lose one -- so read it as a bound on how
    long an advantage can persist rather than an exact decay. It is still a
    measurement, which the flat 8.0 it replaces was not.
    """
    accepted = [t for t in transactions if t.get("result") == "a"]
    weeks = {t["event"] for t in accepted}
    if not accepted or not weeks or not n_managers:
        return None
    per_week = len(accepted) / n_managers / len(weeks)
    frac = per_week / squad_size
    if not 0 < frac < 1:
        return None
    return math.log(0.5) / math.log(1 - frac)


def measured_skill_sd(cache_path, played):
    """Manager skill, in points per gameweek, measured from bench waste.

    Returns the shrunk between-manager spread: how differently managers
    actually select, once the part explainable by three weeks of luck is
    taken back out. None if there is not enough to measure.
    """
    try:
        with open(cache_path) as f:
            weeks = json.load(f)["gameweeks"]
    except (OSError, ValueError, KeyError):
        return None
    gws = sorted(weeks, key=int)
    if len(gws) < 2:
        return None
    lids = sorted(weeks[gws[0]]["bench"])
    series = {l: [weeks[g]["bench"].get(l, 0) for g in gws] for l in lids}

    within = []
    for v in series.values():
        mu = statistics.mean(v)
        within += [x - mu for x in v]
    dof = max(1, len(within) - len(lids))
    within_sd = statistics.pstdev(within) * (len(within) / dof) ** 0.5

    means = [statistics.mean(v) for v in series.values()]
    se = within_sd / len(gws) ** 0.5
    return max(0.0, statistics.pvariance(means) - se ** 2) ** 0.5


def main():
    print("Fetching data...")
    draft_bootstrap = fetch(f"{DRAFT_BASE}/bootstrap-static")
    classic_bootstrap = fetch(f"{CLASSIC_BASE}/bootstrap-static/")

    names = {el["id"]: el["web_name"] for el in draft_bootstrap["elements"]}
    ep_next = {el["id"]: float(el["ep_next"] or 0) for el in classic_bootstrap["elements"]}

    finished = [e["id"] for e in draft_bootstrap["events"]["data"] if e["finished"]]
    if not finished:
        print("No finished gameweeks yet -- can't project from a standing start.")
        return
    target_gw = max(finished)
    next_gw = target_gw + 1

    league_data = fetch(f"{DRAFT_BASE}/league/{LEAGUE_ID}/details")
    entry_lookup = {}
    for e in league_data["league_entries"]:
        entry_lookup[e["id"]] = {
            "manager": f"{e['player_first_name']} {e['player_last_name']}",
            "entry_id": e["entry_id"],
        }
    entry_ids = list(entry_lookup.keys())
    matches = league_data["matches"]

    # ------------------------------------------------------------------
    # Per-manager: season PPG so far + projected next-GW XI score (ep_next)
    # ------------------------------------------------------------------
    season_ppg = {}
    for lid in entry_ids:
        own_scores = [
            m["league_entry_1_points"] if m["league_entry_1"] == lid else m["league_entry_2_points"]
            for m in matches
            if m["event"] <= target_gw and lid in (m["league_entry_1"], m["league_entry_2"])
        ]
        season_ppg[lid] = sum(own_scores) / len(own_scores) if own_scores else 0

    # Accepted trades effective for next_gw, per entry_id: swap the dropped
    # player for the acquired one if the dropped player was a starter --
    # otherwise the last known (target_gw) starting XI is still the best
    # available signal, since next_gw's own picks aren't queryable yet.
    transactions = fetch(f"{DRAFT_BASE}/draft/league/{LEAGUE_ID}/transactions")["transactions"]
    pending_swaps = {}
    for t in transactions:
        if t["result"] == "a" and t["event"] == next_gw:
            pending_swaps.setdefault(t["entry"], []).append((t["element_out"], t["element_in"]))

    # Where a score might land, learned from every gameweek played so far
    positions = {1: "GK", 2: "DEF", 3: "MID", 4: "FWD"}
    player_info = {
        el["id"]: {"type": positions[el["element_type"]], "team": el["team"]}
        for el in draft_bootstrap["elements"]
    }
    pools = scoring_model.Pools(
        scoring_model.gather_samples(fetch, DRAFT_BASE, ep_next, player_info, finished)
    )
    print(f"  comparable-player sample: {len(pools)} starter performances")

    xi_projection = {}
    squads = {}
    for lid, info in entry_lookup.items():
        picks = fetch(f"{DRAFT_BASE}/entry/{info['entry_id']}/event/{target_gw}")["picks"]
        starters = [p["element"] for p in picks if p["position"] <= 11]
        bench = [p["element"] for p in sorted(
            (x for x in picks if x["position"] > 11), key=lambda x: x["position"])]
        for element_out, element_in in pending_swaps.get(info["entry_id"], []):
            if element_out in starters:
                starters[starters.index(element_out)] = element_in
        xi_projection[lid] = sum(ep_next.get(e, 0) for e in starters)

        xi_types = [player_info.get(e, {}).get("type", "MID") for e in starters]
        # Nobody has kicked a ball yet, so a reserve's own expectation stands in
        # for what he'd bring if called upon, and all of them count as available.
        bench_rows = [
            (slot, player_info.get(e, {}).get("type", "MID"),
             round(ep_next.get(e, 0)), True)
            for slot, e in enumerate(bench)
        ]
        squads[lid] = [
            {
                "ep": ep_next.get(e, 0),
                "pos": xi_types[i],
                "share": 1.0,
                "team": player_info.get(e, {}).get("team"),
                "cover": scoring_model.eligible_cover(xi_types, i, bench_rows),
            }
            for i, e in enumerate(starters)
        ]

    projected_mean = {
        lid: 0.5 * season_ppg[lid] + 0.5 * xi_projection[lid]
        for lid in entry_ids
    }

    # How much a manager's weekly score actually moves about, measured as the
    # spread around their own average. Simulating eleven players and adding
    # them up gives a believable shape but too narrow a spread -- it can only
    # see the correlations it was told about, and real gameweeks have more
    # going on than clubs sharing clean sheets. So the simulation supplies the
    # shape, and the league's own results supply the scale.
    deviations = []
    for lid in entry_ids:
        own = [
            m["league_entry_1_points"] if m["league_entry_1"] == lid else m["league_entry_2_points"]
            for m in matches
            if m["event"] <= target_gw and lid in (m["league_entry_1"], m["league_entry_2"])
        ]
        if len(own) >= 2:
            mu = statistics.mean(own)
            deviations += [x - mu for x in own]
    observed = None
    if len(deviations) >= 12:
        # correct for the means having been estimated from the same few games
        dof = max(1, len(deviations) - len(entry_ids))
        observed = statistics.pstdev(deviations) * (len(deviations) / dof) ** 0.5

    print("Simulating gameweek scores...")
    distributions = {}
    raw_spreads = []
    for lid in entry_ids:
        raw = scoring_model.score_distribution(pools, squads[lid], sims=SCORE_SIMS)
        mu = statistics.mean(raw)
        sd = statistics.pstdev(raw) or 1.0
        raw_spreads.append(sd)
        # never narrow the simulation, only widen it towards what's been seen
        scale = max(1.0, observed / sd) if observed else 1.0
        distributions[lid] = [
            max(0, round(projected_mean[lid] + (v - mu) * scale)) for v in raw
        ]
    sim_spread = statistics.mean(raw_spreads)
    final_spread = statistics.mean(statistics.pstdev(d) for d in distributions.values())
    print(f"  simulated spread {sim_spread:.1f} pts; observed in this league "
          f"{observed:.1f} pts" if observed else f"  simulated spread {sim_spread:.1f} pts")
    print(f"  using {final_spread:.1f} pts per manager per gameweek "
          f"(the old code assumed a flat 15.0)")

    # ------------------------------------------------------------------
    # 1. H2H predictions for next_gw
    # ------------------------------------------------------------------
    next_matches = [m for m in matches if m["event"] == next_gw]
    os.makedirs(SEASON_DIR, exist_ok=True)

    if next_matches:
        h2h_lines = [f"# Gameweek {next_gw} Predictions\n"]
        h2h_lines.append(
            f"_Projected starting XI score = 50% season PPG so far + 50% "
            f"current squad's summed `ep_next`. Win/draw/loss comes from "
            f"simulating each side player by player, drawing on what "
            f"comparable players actually scored across GW1-{target_gw} "
            f"({len(pools)} performances), widened to match how much scores "
            f"have actually moved about in this league ({final_spread:.0f} pts "
            f"a week) -- a rough guide, not a forecast, especially this early "
            f"in the season._\n"
        )
        h2h_lines.append("| Home | Proj | Win% | Draw% | Proj | Away | Win% |")
        h2h_lines.append("|---|---|---|---|---|---|---|")
        for m in next_matches:
            a, b = m["league_entry_1"], m["league_entry_2"]
            mean_a, mean_b = projected_mean[a], projected_mean[b]
            p_a, p_draw, p_b = [x / 100 for x in
                                scoring_model.odds_from(distributions[a], distributions[b])]
            h2h_lines.append(
                f"| {entry_lookup[a]['manager']} | {mean_a:.1f} | {p_a*100:.0f}% | "
                f"{p_draw*100:.0f}% | {mean_b:.1f} | {entry_lookup[b]['manager']} | {p_b*100:.0f}% |"
            )
        with open(os.path.join(SEASON_DIR, "h2h_predictions.md"), "w") as f:
            f.write("\n".join(h2h_lines) + "\n")
        print(f"Wrote {SEASON_DIR}/h2h_predictions.md")
    else:
        print(f"No scheduled matches found for GW{next_gw} -- skipping H2H predictions.")

    # ------------------------------------------------------------------
    # 2. Monte Carlo season projection
    # ------------------------------------------------------------------
    remaining = [m for m in matches if m["event"] > target_gw]
    base_league_pts = {lid: 0 for lid in entry_ids}
    base_pts_for = {lid: 0 for lid in entry_ids}
    for m in matches:
        if m["event"] > target_gw:
            continue
        e1, e2 = m["league_entry_1"], m["league_entry_2"]
        p1, p2 = m["league_entry_1_points"], m["league_entry_2_points"]
        base_pts_for[e1] += p1
        base_pts_for[e2] += p2
        if p1 > p2:
            base_league_pts[e1] += 3
        elif p2 > p1:
            base_league_pts[e2] += 3
        else:
            base_league_pts[e1] += 1
            base_league_pts[e2] += 1

    position_counts = {lid: [0] * len(entry_ids) for lid in entry_ids}

    # --- how much of the table so far is signal? -------------------------
    # A manager's mean over n games carries a standard error of sigma/sqrt(n).
    # If the spread between managers is no bigger than that, the table is
    # noise and the right estimate of everyone's rate is the league average.
    played = len(set(m["event"] for m in matches if m["event"] <= target_gw))
    week_sd = observed or final_spread or 12.0
    se = week_sd / max(1, played) ** 0.5
    obs_means = {lid: base_pts_for[lid] / max(1, played) for lid in entry_ids}
    league_mean = statistics.mean(obs_means.values())
    between_var = statistics.pvariance(list(obs_means.values()))
    true_var = max(0.0, between_var - se ** 2)
    shrink = true_var / (true_var + se ** 2) if (true_var + se ** 2) else 0.0

    # The squad edge is a real measurement, not an average of past results:
    # it is what this squad is expected to score next week.
    squad_mean = statistics.mean(xi_projection.values())
    squad_edge = {lid: xi_projection[lid] - squad_mean for lid in entry_ids}
    skill_sd = measured_skill_sd(
        os.path.join(SEASON_DIR, "gw_points_cache.json"), played)
    if skill_sd is None:
        skill_sd = SKILL_FALLBACK_SD
        print(f"  no bench history yet -- manager skill assumed "
              f"{skill_sd:.1f} pts/week")
    else:
        print(f"  manager skill measured from bench decisions: "
              f"{skill_sd:.2f} pts/week spread (a floor -- waivers and "
              f"start/sit are skill too and aren't counted)")

    # Squad edge decays week by week and stops entirely at the re-draft.
    half_life = squad_half_life(transactions, len(entry_ids))
    if half_life is None:
        half_life = SQUAD_HALF_LIFE_FALLBACK
        print(f"  no transactions yet -- squad edge half-life assumed "
              f"{half_life:.1f} gameweeks")
    else:
        print(f"  squad turnover measured: edge half-life "
              f"{half_life:.1f} gameweeks")

    print(f"  {played} gameweeks in: between-manager spread {between_var ** 0.5:.1f} "
          f"vs {se:.1f} from noise alone -> {shrink * 100:.0f}% of the table is signal")
    print(f"  squad edges span {min(squad_edge.values()):+.1f} to "
          f"{max(squad_edge.values()):+.1f} pts/week, halving every "
          f"{half_life:.1f} gameweeks, gone at the GW{REDRAFT_GW} re-draft")

    # Keep each manager's score SHAPE but let its centre move: the empirical
    # distribution carries the skew and the lumpiness, the rate carries the level.
    shapes = {lid: [v - statistics.mean(distributions[lid]) for v in distributions[lid]]
              for lid in entry_ids}

    weight = {}
    for gw in sorted(set(m["event"] for m in remaining)):
        weight[gw] = (0.0 if gw >= REDRAFT_GW
                      else 0.5 ** ((gw - target_gw) / half_life))

    rng = random.Random(SEASON_SEED)
    for _ in range(N_TRIALS):
        # One draw of the season's truth: who is actually any good. Redrawn
        # every trial, because after three games we genuinely do not know.
        rate = {}
        for lid in entry_ids:
            shrunk = league_mean + shrink * (obs_means[lid] - league_mean)
            rate[lid] = shrunk + rng.gauss(0.0, skill_sd)

        league_pts = dict(base_league_pts)
        pts_for = dict(base_pts_for)
        for m in remaining:
            e1, e2 = m["league_entry_1"], m["league_entry_2"]
            w = weight[m["event"]]
            s1 = max(0, round(rate[e1] + squad_edge[e1] * w + rng.choice(shapes[e1])))
            s2 = max(0, round(rate[e2] + squad_edge[e2] * w + rng.choice(shapes[e2])))
            pts_for[e1] += s1
            pts_for[e2] += s2
            if s1 > s2:
                league_pts[e1] += 3
            elif s2 > s1:
                league_pts[e2] += 3
            else:
                league_pts[e1] += 1
                league_pts[e2] += 1

        final_order = sorted(entry_ids, key=lambda lid: (league_pts[lid], pts_for[lid]), reverse=True)
        for rank, lid in enumerate(final_order):
            position_counts[lid][rank] += 1

    proj_lines = [f"# Season Projection (through GW{target_gw}, {N_TRIALS:,} simulations)\n"]
    proj_lines.append(
        f"_Monte Carlo simulation. Gameweeks already played are exact. For the rest, "
        f"each trial first draws how good every manager actually is -- after "
        f"{played} gameweeks the table is {shrink * 100:.0f}% signal, so that is "
        f"mostly guesswork and the simulation treats it that way. A squad's current "
        f"edge decays as squads churn and stops counting at the GW{REDRAFT_GW} "
        f"re-draft, when everyone starts again._\n"
    )
    proj_lines.append("| Manager | Expected Finish | Top 3 | Mid | Bottom 3 |")
    proj_lines.append("|---|---|---|---|---|")

    def expected(lid):
        counts = position_counts[lid]
        return sum((r + 1) * c for r, c in enumerate(counts)) / N_TRIALS

    # Sorted on expected finish, which is stable. The single most likely
    # position is not: once the spread is this wide several managers share a
    # modal rank of 1st and the ordering jumps about between runs.
    for lid in sorted(entry_ids, key=expected):
        counts = position_counts[lid]
        top3 = sum(counts[:3]) / N_TRIALS * 100
        bottom3 = sum(counts[-3:]) / N_TRIALS * 100
        mid = 100.0 - top3 - bottom3
        proj_lines.append(
            f"| {entry_lookup[lid]['manager']} | {expected(lid):.1f} | "
            f"{top3:.0f}% | {mid:.0f}% | {bottom3:.0f}% |"
        )

    with open(os.path.join(SEASON_DIR, "season_projection.md"), "w") as f:
        f.write("\n".join(proj_lines) + "\n")
    print(f"Wrote {SEASON_DIR}/season_projection.md")


if __name__ == "__main__":
    main()
