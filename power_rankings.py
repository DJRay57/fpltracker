"""
Generates power rankings -- a "how good are you really" ranking that's
usually different from the actual standings, because H2H results reward
scheduling luck as much as squad strength.

Composite score per manager (lower rank number = more powerful), blending:
  - 40% season points-for rank   (average starting XI score all season -- squad quality)
  - 35% recent form rank         (average starting XI score, last 3 finished GWs -- who's hot now)
  - 25% actual standings rank    (real league position from W/D/L record)

Also reports a "luck" delta: standings rank vs points-for rank. Positive means
overperforming your scoring output (winning close ones); negative means
underperforming it (scoring well but losing H2H matchups).

Writes power_rankings/GW{n}_power_rankings.md.
"""

import requests
import os

BASE = "https://draft.premierleague.com/api"
LEAGUE_ID = 1139
SEASON = "2026-27"
OUTPUT_DIR = os.path.join("seasons", SEASON, "power_rankings")
FORM_WINDOW = 3


def fetch(url):
    r = requests.get(url, timeout=10)
    r.raise_for_status()
    return r.json()


def entry_gw_scores(matches, lid, through_gw):
    """Ordered list of (gw, score) for this entry's own matches, gw ascending."""
    scores = []
    for m in matches:
        if m["event"] > through_gw:
            continue
        if m["league_entry_1"] == lid:
            scores.append((m["event"], m["league_entry_1_points"]))
        elif m["league_entry_2"] == lid:
            scores.append((m["event"], m["league_entry_2_points"]))
    scores.sort(key=lambda x: x[0])
    return scores


def compute_power_rankings(matches, entry_ids, through_gw):
    """Returns list of dicts, one per entry, sorted by power rank (best first)."""
    stats = {}
    league_pts = {lid: 0 for lid in entry_ids}

    for m in matches:
        if m["event"] > through_gw:
            continue
        e1, e2 = m["league_entry_1"], m["league_entry_2"]
        p1, p2 = m["league_entry_1_points"], m["league_entry_2_points"]
        if p1 > p2:
            league_pts[e1] += 3
        elif p2 > p1:
            league_pts[e2] += 3
        else:
            league_pts[e1] += 1
            league_pts[e2] += 1

    for lid in entry_ids:
        scores = entry_gw_scores(matches, lid, through_gw)
        games_played = len(scores)
        pts_for = sum(s for _, s in scores)
        season_ppg = pts_for / games_played if games_played else 0
        recent = scores[-FORM_WINDOW:]
        recent_ppg = sum(s for _, s in recent) / len(recent) if recent else 0
        stats[lid] = {
            "league_entry_id": lid,
            "games_played": games_played,
            "pts_for": pts_for,
            "season_ppg": round(season_ppg, 1),
            "recent_ppg": round(recent_ppg, 1),
            "league_pts": league_pts[lid],
        }

    # Rank on each component (1 = best)
    by_season = sorted(entry_ids, key=lambda x: stats[x]["season_ppg"], reverse=True)
    by_recent = sorted(entry_ids, key=lambda x: stats[x]["recent_ppg"], reverse=True)
    by_standings = sorted(
        entry_ids,
        key=lambda x: (stats[x]["league_pts"], stats[x]["pts_for"]),
        reverse=True,
    )
    season_rank = {lid: i + 1 for i, lid in enumerate(by_season)}
    recent_rank = {lid: i + 1 for i, lid in enumerate(by_recent)}
    standings_rank = {lid: i + 1 for i, lid in enumerate(by_standings)}

    for lid in entry_ids:
        stats[lid]["season_rank"] = season_rank[lid]
        stats[lid]["recent_rank"] = recent_rank[lid]
        stats[lid]["standings_rank"] = standings_rank[lid]
        stats[lid]["luck"] = season_rank[lid] - standings_rank[lid]
        stats[lid]["power_score"] = (
            0.40 * season_rank[lid] + 0.35 * recent_rank[lid] + 0.25 * standings_rank[lid]
        )

    ranked = sorted(entry_ids, key=lambda x: stats[x]["power_score"])
    return {lid: i + 1 for i, lid in enumerate(ranked)}, stats


def main():
    print("Fetching data...")
    bootstrap = fetch(f"{BASE}/bootstrap-static")
    finished = [e["id"] for e in bootstrap["events"]["data"] if e["finished"]]
    if not finished:
        print("No finished gameweeks yet -- nothing to rank.")
        return
    target_gw = max(finished)
    prev_gw = target_gw - 1
    print(f"Power rankings through GW{target_gw}")

    league_data = fetch(f"{BASE}/league/{LEAGUE_ID}/details")
    entry_lookup = {}
    for e in league_data["league_entries"]:
        entry_lookup[e["id"]] = {
            "manager": f"{e['player_first_name']} {e['player_last_name']}",
            "team_name": e["entry_name"],
        }
    entry_ids = list(entry_lookup.keys())
    matches = league_data["matches"]

    power_now, stats_now = compute_power_rankings(matches, entry_ids, target_gw)
    power_prev = (
        compute_power_rankings(matches, entry_ids, prev_gw)[0] if prev_gw >= 1 else {}
    )

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    out_path = os.path.join(OUTPUT_DIR, f"GW{target_gw}_power_rankings.md")

    lines = [f"# Power Rankings — through GW{target_gw}\n"]
    lines.append(
        "_Blend of season scoring average (40%), last-3-GW form (35%), and actual "
        "standings (25%) — a \"how good are you really\" view, separate from the "
        "literal table._\n"
    )
    lines.append(
        "| Power Rank | Move | Manager | Team | Standings | Season PPG | Last 3 PPG | Luck |"
    )
    lines.append("|---|---|---|---|---|---|---|---|")

    for lid in sorted(entry_ids, key=lambda x: power_now[x]):
        info = entry_lookup[lid]
        s = stats_now[lid]
        rank_now = power_now[lid]
        rank_prev = power_prev.get(lid)
        if rank_prev is None:
            move = "-"
        elif rank_now < rank_prev:
            move = f"UP {rank_prev - rank_now}"
        elif rank_now > rank_prev:
            move = f"DOWN {rank_now - rank_prev}"
        else:
            move = "="
        luck = s["luck"]
        luck_str = f"+{luck} (lucky)" if luck > 0 else (f"{luck} (unlucky)" if luck < 0 else "even")
        lines.append(
            f"| {rank_now} | {move} | {info['manager']} | {info['team_name']} | "
            f"#{s['standings_rank']} | {s['season_ppg']} | {s['recent_ppg']} | {luck_str} |"
        )

    with open(out_path, "w") as f:
        f.write("\n".join(lines) + "\n")

    print(f"\nWrote {out_path}")


if __name__ == "__main__":
    main()
