"""
Generates a markdown recap of the most recently finished gameweek:
  - league standings + movement vs the previous gameweek
  - each manager's starting XI score, sorted
  - top individual point scorers league-wide
  - waiver wire activity (free agent pickups/drops) over the last 7 days,
    read from free_agents_log.csv
  - injury/status changes over the last 7 days, read from player_status_log.csv

Depends on weekly_snapshot.py having been run at least twice (ideally daily)
so there's a "last week" baseline to diff against for waiver/status trends.
Writes weekly_summaries/GW{n}_summary.md.
"""

import requests
import csv
import os
from collections import defaultdict
from datetime import date, timedelta

BASE = "https://draft.premierleague.com/api"
LEAGUE_ID = 1139
SEASON = "2026-27"
SEASON_DIR = os.path.join("seasons", SEASON)
POSITION_LABELS = {1: "GKP", 2: "DEF", 3: "MID", 4: "FWD"}
OUTPUT_DIR = os.path.join(SEASON_DIR, "weekly_summaries")
FREE_AGENTS_FILE = os.path.join(SEASON_DIR, "free_agents_log.csv")
PLAYER_STATUS_FILE = os.path.join(SEASON_DIR, "player_status_log.csv")


def fetch(url):
    r = requests.get(url, timeout=10)
    r.raise_for_status()
    return r.json()


def build_standings(matches, entry_ids, through_gw):
    """League table (rank, pts, pts_for) after all matches up to through_gw."""
    table = {lid: {"league_pts": 0, "pts_for": 0} for lid in entry_ids}
    for m in matches:
        if m["event"] > through_gw:
            continue
        e1, e2 = m["league_entry_1"], m["league_entry_2"]
        p1, p2 = m["league_entry_1_points"], m["league_entry_2_points"]
        table[e1]["pts_for"] += p1
        table[e2]["pts_for"] += p2
        if p1 > p2:
            table[e1]["league_pts"] += 3
        elif p2 > p1:
            table[e2]["league_pts"] += 3
        else:
            table[e1]["league_pts"] += 1
            table[e2]["league_pts"] += 1

    ranked = sorted(
        entry_ids,
        key=lambda lid: (table[lid]["league_pts"], table[lid]["pts_for"]),
        reverse=True,
    )
    return {lid: rank + 1 for rank, lid in enumerate(ranked)}, table


def read_csv_rows(filename):
    if not os.path.exists(filename):
        return []
    with open(filename, newline="") as f:
        return list(csv.DictReader(f))


def latest_and_baseline_dates(rows, days_back=7):
    """Most recent snapshot_date, and the closest available date >= (latest - days_back)."""
    dates = sorted(set(r["snapshot_date"] for r in rows))
    if not dates:
        return None, None
    latest = dates[-1]
    cutoff = (date.fromisoformat(latest) - timedelta(days=days_back)).isoformat()
    earlier = [d for d in dates if d <= cutoff]
    baseline = earlier[-1] if earlier else (dates[0] if len(dates) > 1 else None)
    return latest, baseline


def main():
    print("Fetching static data...")
    bootstrap = fetch(f"{BASE}/bootstrap-static")
    player_lookup = {
        el["id"]: {"name": el["web_name"], "position": POSITION_LABELS[el["element_type"]]}
        for el in bootstrap["elements"]
    }

    finished = [e["id"] for e in bootstrap["events"]["data"] if e["finished"]]
    if not finished:
        print("No finished gameweeks yet -- nothing to summarize.")
        return
    target_gw = max(finished)
    prev_gw = target_gw - 1
    print(f"Summarizing GW{target_gw} (previous: GW{prev_gw})")

    league_data = fetch(f"{BASE}/league/{LEAGUE_ID}/details")
    entry_lookup = {}
    league_entry_lookup = {}
    for e in league_data["league_entries"]:
        info = {
            "manager": f"{e['player_first_name']} {e['player_last_name']}",
            "team_name": e["entry_name"],
            "entry_id": e["entry_id"],
        }
        entry_lookup[e["id"]] = info
        league_entry_lookup[e["entry_id"]] = info
    entry_ids = list(entry_lookup.keys())

    standings_now, table_now = build_standings(league_data["matches"], entry_ids, target_gw)
    standings_prev, _ = (
        build_standings(league_data["matches"], entry_ids, prev_gw)
        if prev_gw >= 1
        else ({lid: None for lid in entry_ids}, None)
    )

    # ------------------------------------------------------------------
    # Gameweek scores per manager (starting XI only) + top performers
    # ------------------------------------------------------------------
    live = fetch(f"{BASE}/event/{target_gw}/live")
    points_lookup = {int(eid): d["stats"]["total_points"] for eid, d in live["elements"].items()}

    manager_scores = []
    top_performers = []  # (points, player_name, manager)
    any_captains = False

    for lid, info in entry_lookup.items():
        picks = fetch(f"{BASE}/entry/{info['entry_id']}/event/{target_gw}")["picks"]
        starter_total = 0
        for p in picks:
            pts = points_lookup.get(p["element"], 0)
            if p["is_captain"]:
                any_captains = True
            if p["position"] <= 11:
                starter_total += pts * p.get("multiplier", 1)
                player = player_lookup.get(p["element"], {"name": "Unknown"})
                top_performers.append((pts, player["name"], info["manager"], p["is_captain"]))
        manager_scores.append((info["manager"], info["team_name"], starter_total, lid))

    manager_scores.sort(key=lambda x: x[2], reverse=True)
    top_performers.sort(key=lambda x: x[0], reverse=True)

    # ------------------------------------------------------------------
    # Waiver wire activity (free_agents_log.csv diff)
    # ------------------------------------------------------------------
    fa_rows = read_csv_rows(FREE_AGENTS_FILE)
    fa_latest_date, fa_baseline_date = latest_and_baseline_dates(fa_rows)
    pickups, drops = [], []
    if fa_latest_date and fa_baseline_date and fa_latest_date != fa_baseline_date:
        latest_status = {r["player_id"]: r for r in fa_rows if r["snapshot_date"] == fa_latest_date}
        baseline_status = {r["player_id"]: r for r in fa_rows if r["snapshot_date"] == fa_baseline_date}
        for pid, now_row in latest_status.items():
            then_row = baseline_status.get(pid)
            if not then_row:
                continue
            if then_row["ownership_status"] == "a" and now_row["ownership_status"] == "o":
                pickups.append((now_row["player_name"], now_row["owner_manager"]))
            elif then_row["ownership_status"] == "o" and now_row["ownership_status"] == "a":
                drops.append((now_row["player_name"], then_row["owner_manager"]))

    # ------------------------------------------------------------------
    # Injury/status watch (player_status_log.csv diff)
    # ------------------------------------------------------------------
    status_rows = read_csv_rows(PLAYER_STATUS_FILE)
    st_latest_date, st_baseline_date = latest_and_baseline_dates(status_rows)
    status_changes = []
    if st_latest_date and st_baseline_date and st_latest_date != st_baseline_date:
        latest_status = {r["player_id"]: r for r in status_rows if r["snapshot_date"] == st_latest_date}
        baseline_status = {r["player_id"]: r for r in status_rows if r["snapshot_date"] == st_baseline_date}
        for pid, now_row in latest_status.items():
            then_row = baseline_status.get(pid)
            if not then_row:
                continue
            if now_row["status"] != then_row["status"] and now_row["status"] != "a":
                status_changes.append((now_row["player_name"], then_row["status"], now_row["status"], now_row["news"]))

    # ------------------------------------------------------------------
    # Write markdown
    # ------------------------------------------------------------------
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    out_path = os.path.join(OUTPUT_DIR, f"GW{target_gw}_summary.md")

    lines = [f"# Gameweek {target_gw} Summary\n"]

    lines.append("## Standings\n")
    lines.append("| Rank | Manager | Team | Pts | For | Move |")
    lines.append("|---|---|---|---|---|---|")
    for lid in sorted(entry_ids, key=lambda x: standings_now[x]):
        info = entry_lookup[lid]
        rank_now = standings_now[lid]
        rank_prev = standings_prev.get(lid)
        if rank_prev is None:
            move = "-"
        elif rank_now < rank_prev:
            move = f"UP {rank_prev - rank_now}"
        elif rank_now > rank_prev:
            move = f"DOWN {rank_now - rank_prev}"
        else:
            move = "="
        t = table_now[lid]
        lines.append(f"| {rank_now} | {info['manager']} | {info['team_name']} | {t['league_pts']} | {t['pts_for']} | {move} |")

    lines.append(f"\n## GW{target_gw} Scores (starting XI)\n")
    lines.append("| Manager | Team | Points |")
    lines.append("|---|---|---|")
    for manager, team_name, score, lid in manager_scores:
        lines.append(f"| {manager} | {team_name} | {score} |")

    lines.append(f"\n## Top Performers\n")
    for pts, name, manager, is_cap in top_performers[:5]:
        cap_tag = " (C)" if is_cap else ""
        lines.append(f"- **{name}{cap_tag}** — {pts} pts ({manager})")
    if not any_captains:
        lines.append("\n_(This league doesn't use captaincy multipliers.)_")

    lines.append(f"\n## Waiver Wire Activity (last 7 days)\n")
    if not fa_baseline_date:
        lines.append("_Not enough snapshot history yet — check back after a week of daily snapshots._")
    elif not pickups and not drops:
        lines.append("_No pickups or drops detected._")
    else:
        for name, manager in pickups:
            lines.append(f"- **ADDED**: {name} → {manager}")
        for name, manager in drops:
            lines.append(f"- **DROPPED**: {name} (was {manager})")

    lines.append(f"\n## Injury/Status Watch (last 7 days)\n")
    if not st_baseline_date:
        lines.append("_Not enough snapshot history yet — check back after a week of daily snapshots._")
    elif not status_changes:
        lines.append("_No status changes detected._")
    else:
        for name, old, new, news in status_changes:
            news_str = f" — {news}" if news else ""
            lines.append(f"- **{name}**: {old} → {new}{news_str}")

    with open(out_path, "w") as f:
        f.write("\n".join(lines) + "\n")

    print(f"\nWrote {out_path}")


if __name__ == "__main__":
    main()
