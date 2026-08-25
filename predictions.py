"""
Two predictive features, sharing the same per-manager projection:

1. H2H WIN PROBABILITIES for the upcoming (not-yet-played) gameweek --
   projects each manager's starting XI score as the sum of `ep_next`
   (FPL's own fixture/form-adjusted "expected points next round") for
   their most recently known starting XI, then converts the projected
   score gap into a win/draw/loss probability assuming each team's
   actual score is ~Normal(projected, SIGMA).

2. SEASON FINISHING POSITION -- Monte Carlo simulation of the rest of
   the season (the full fixture list already exists for all 38 GWs in
   the league's `matches`), replaying already-played gameweeks exactly
   as they happened and simulating the rest from each manager's
   projected mean score.

Methodology caveats (stated on the page, not hidden):
- `ep_next` isn't exposed by the Draft API -- pulled from the classic
  FPL API instead (fantasy.premierleague.com), which shares element ids.
- Projections assume each manager's starting XI stays as last set; a
  trade or lineup change before the next deadline isn't accounted for.
- SIGMA (assumed per-manager, per-gameweek scoring std-dev) is a fixed
  estimate, not fit to this league's own data -- there's only 1 GW of
  real variance to fit to right now, which isn't enough to trust.
- The season simulation uses one static projected mean per manager for
  all remaining gameweeks (it does not re-project fixture-by-fixture
  for all 37 remaining weeks) -- treat it as a rough guide, not a
  forecast, especially this early in the season.

Writes seasons/2026-27/h2h_predictions.md and seasons/2026-27/season_projection.md.
"""

import requests
import os
import math
import random

DRAFT_BASE = "https://draft.premierleague.com/api"
CLASSIC_BASE = "https://fantasy.premierleague.com/api"
LEAGUE_ID = 1139
SEASON = "2026-27"
SEASON_DIR = os.path.join("seasons", SEASON)

SIGMA = 15.0          # assumed per-GW scoring std-dev, see caveats above
N_TRIALS = 5000        # Monte Carlo trials for season projection


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

    xi_projection = {}
    for lid, info in entry_lookup.items():
        picks = fetch(f"{DRAFT_BASE}/entry/{info['entry_id']}/event/{target_gw}")["picks"]
        xi_projection[lid] = sum(
            ep_next.get(p["element"], 0) for p in picks if p["position"] <= 11
        )

    projected_mean = {
        lid: 0.5 * season_ppg[lid] + 0.5 * xi_projection[lid]
        for lid in entry_ids
    }

    # ------------------------------------------------------------------
    # 1. H2H predictions for next_gw
    # ------------------------------------------------------------------
    next_matches = [m for m in matches if m["event"] == next_gw]
    os.makedirs(SEASON_DIR, exist_ok=True)

    if next_matches:
        h2h_lines = [f"# Gameweek {next_gw} Predictions\n"]
        h2h_lines.append(
            f"_Projected starting XI score = 50% season PPG so far + 50% "
            f"current squad's summed `ep_next`. Win/draw/loss assumes actual "
            f"scores land on a Normal curve around that projection with an "
            f"assumed std-dev of {SIGMA:.0f} pts -- a rough guide, not a forecast, "
            f"especially this early in the season._\n"
        )
        h2h_lines.append("| Home | Proj | Win% | Draw% | Proj | Away | Win% |")
        h2h_lines.append("|---|---|---|---|---|---|---|")
        for m in next_matches:
            a, b = m["league_entry_1"], m["league_entry_2"]
            mean_a, mean_b = projected_mean[a], projected_mean[b]
            p_a, p_draw, p_b = win_draw_loss(mean_a, mean_b, SIGMA)
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

    for _ in range(N_TRIALS):
        league_pts = dict(base_league_pts)
        pts_for = dict(base_pts_for)
        for m in remaining:
            e1, e2 = m["league_entry_1"], m["league_entry_2"]
            s1 = max(0, round(random.gauss(projected_mean[e1], SIGMA)))
            s2 = max(0, round(random.gauss(projected_mean[e2], SIGMA)))
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
        "_Monte Carlo simulation: already-played gameweeks are exact, the rest of the "
        "season is simulated from each manager's projected mean score (see methodology "
        "note in the script). One static mean per manager for all remaining gameweeks -- "
        "a rough guide, not a forecast._\n"
    )
    proj_lines.append("| Manager | Most Likely Finish | Chance | Top 3 | Bottom 3 |")
    proj_lines.append("|---|---|---|---|---|")

    ranked_by_likely = sorted(
        entry_ids,
        key=lambda lid: max(range(len(entry_ids)), key=lambda r: position_counts[lid][r]),
    )
    for lid in ranked_by_likely:
        counts = position_counts[lid]
        best_rank = max(range(len(counts)), key=lambda r: counts[r])
        chance = counts[best_rank] / N_TRIALS * 100
        top3 = sum(counts[:3]) / N_TRIALS * 100
        bottom3 = sum(counts[-3:]) / N_TRIALS * 100
        proj_lines.append(
            f"| {entry_lookup[lid]['manager']} | {best_rank+1} | {chance:.0f}% | "
            f"{top3:.0f}% | {bottom3:.0f}% |"
        )

    with open(os.path.join(SEASON_DIR, "season_projection.md"), "w") as f:
        f.write("\n".join(proj_lines) + "\n")
    print(f"Wrote {SEASON_DIR}/season_projection.md")


if __name__ == "__main__":
    main()
