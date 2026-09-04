"""
Computes the "fun" stats the recap tables don't cover, for the dashboard:
  - head-to-head results for the latest finished gameweek (the actual scorelines)
  - points each manager left on their bench
  - a W/D/L form guide per manager across every finished gameweek
  - an all-play-all table (everyone vs everyone each week) that strips out
    fixture luck, alongside each manager's real table position
  - the best-scoring player nobody in the league owns
  - season-long trophy counters (weeks topped, weeks bottomed, weeks robbed)

Writes both dashboard_stats.json (for renderers) and dashboard_stats.md
(for humans / the publishing routine).
"""

import requests
import csv
import json
import os
from collections import defaultdict

BASE = "https://draft.premierleague.com/api"
LEAGUE_ID = 1139
SEASON = "2026-27"
SEASON_DIR = os.path.join("seasons", SEASON)
POSITION_LABELS = {1: "GKP", 2: "DEF", 3: "MID", 4: "FWD"}
FREE_AGENTS_FILE = os.path.join(SEASON_DIR, "free_agents_log.csv")
JSON_OUT = os.path.join(SEASON_DIR, "dashboard_stats.json")
MD_OUT = os.path.join(SEASON_DIR, "dashboard_stats.md")


def fetch(url):
    r = requests.get(url, timeout=15)
    r.raise_for_status()
    return r.json()


def main():
    print("Fetching static data...")
    bootstrap = fetch(f"{BASE}/bootstrap-static")
    player_lookup = {
        el["id"]: {
            "name": el["web_name"],
            "position": POSITION_LABELS[el["element_type"]],
            "team": el["team"],
        }
        for el in bootstrap["elements"]
    }
    team_lookup = {t["id"]: t["short_name"] for t in bootstrap["teams"]}

    finished = sorted(e["id"] for e in bootstrap["events"]["data"] if e["finished"])
    if not finished:
        print("No finished gameweeks yet -- nothing to compute.")
        return
    target_gw = finished[-1]
    print(f"Latest finished gameweek: GW{target_gw}")

    league_data = fetch(f"{BASE}/league/{LEAGUE_ID}/details")
    entry_lookup = {}
    for e in league_data["league_entries"]:
        entry_lookup[e["id"]] = {
            "manager": f"{e['player_first_name']} {e['player_last_name']}",
            "team_name": e["entry_name"],
            "entry_id": e["entry_id"],
        }
    entry_ids = list(entry_lookup.keys())

    # ------------------------------------------------------------------
    # Per-gameweek starting XI + bench totals for every manager.
    # One live fetch per gameweek, one picks fetch per manager per gameweek.
    # ------------------------------------------------------------------
    live_points = {}
    for gw in finished:
        live = fetch(f"{BASE}/event/{gw}/live")
        live_points[gw] = {
            int(eid): d["stats"]["total_points"] for eid, d in live["elements"].items()
        }

    xi_score = defaultdict(dict)      # gw -> lid -> starting XI points
    bench_score = defaultdict(dict)   # gw -> lid -> bench points
    bench_detail = {}                 # lid -> [(player, pts)] for target_gw

    for gw in finished:
        for lid, info in entry_lookup.items():
            picks = fetch(f"{BASE}/entry/{info['entry_id']}/event/{gw}")["picks"]
            starters = bench = 0
            detail = []
            for p in picks:
                pts = live_points[gw].get(p["element"], 0)
                if p["position"] <= 11:
                    starters += pts * p.get("multiplier", 1)
                else:
                    bench += pts
                    detail.append((player_lookup.get(p["element"], {}).get("name", "?"), pts))
            xi_score[gw][lid] = starters
            bench_score[gw][lid] = bench
            if gw == target_gw:
                bench_detail[lid] = sorted(detail, key=lambda x: x[1], reverse=True)

    # ------------------------------------------------------------------
    # Head-to-head results, form guide, trophy counters
    # ------------------------------------------------------------------
    results = defaultdict(list)   # gw -> [fixture dicts]
    form = defaultdict(list)      # lid -> ["W", "L", ...] in gameweek order
    trophies = {lid: {"topped": 0, "bottomed": 0, "robbed": 0, "won": 0, "lost": 0, "drew": 0}
                for lid in entry_ids}

    for gw in finished:
        gw_matches = [m for m in league_data["matches"] if m["event"] == gw]
        gw_results = {}
        for m in gw_matches:
            e1, e2 = m["league_entry_1"], m["league_entry_2"]
            p1, p2 = m["league_entry_1_points"], m["league_entry_2_points"]
            if p1 > p2:
                gw_results[e1], gw_results[e2] = "W", "L"
            elif p2 > p1:
                gw_results[e2], gw_results[e1] = "W", "L"
            else:
                gw_results[e1] = gw_results[e2] = "D"
            results[gw].append({
                "home": entry_lookup[e1]["manager"],
                "home_team": entry_lookup[e1]["team_name"],
                "home_pts": p1,
                "away": entry_lookup[e2]["manager"],
                "away_team": entry_lookup[e2]["team_name"],
                "away_pts": p2,
                "winner": "home" if p1 > p2 else ("away" if p2 > p1 else "draw"),
                "margin": abs(p1 - p2),
            })

        for lid, res in gw_results.items():
            form[lid].append(res)
            trophies[lid]["won" if res == "W" else "lost" if res == "L" else "drew"] += 1

        ranked = sorted(entry_ids, key=lambda lid: xi_score[gw][lid], reverse=True)
        trophies[ranked[0]]["topped"] += 1
        trophies[ranked[-1]]["bottomed"] += 1

        losers = [lid for lid in entry_ids if gw_results.get(lid) == "L"]
        if losers:
            robbed = max(losers, key=lambda lid: xi_score[gw][lid])
            trophies[robbed]["robbed"] += 1

    # ------------------------------------------------------------------
    # All-play-all: score every manager against all 9 others, every week.
    # Strips fixture luck out entirely -- the "true" pecking order.
    # ------------------------------------------------------------------
    apa = {lid: {"w": 0, "d": 0, "l": 0, "pts": 0} for lid in entry_ids}
    for gw in finished:
        for lid in entry_ids:
            mine = xi_score[gw][lid]
            for other in entry_ids:
                if other == lid:
                    continue
                theirs = xi_score[gw][other]
                if mine > theirs:
                    apa[lid]["w"] += 1
                    apa[lid]["pts"] += 3
                elif mine == theirs:
                    apa[lid]["d"] += 1
                    apa[lid]["pts"] += 1
                else:
                    apa[lid]["l"] += 1

    real_table = {lid: {"pts": 0, "for": 0} for lid in entry_ids}
    for m in league_data["matches"]:
        if m["event"] > target_gw:
            continue
        e1, e2 = m["league_entry_1"], m["league_entry_2"]
        p1, p2 = m["league_entry_1_points"], m["league_entry_2_points"]
        real_table[e1]["for"] += p1
        real_table[e2]["for"] += p2
        if p1 > p2:
            real_table[e1]["pts"] += 3
        elif p2 > p1:
            real_table[e2]["pts"] += 3
        else:
            real_table[e1]["pts"] += 1
            real_table[e2]["pts"] += 1

    real_rank = {
        lid: i + 1
        for i, lid in enumerate(
            sorted(entry_ids, key=lambda l: (real_table[l]["pts"], real_table[l]["for"]), reverse=True)
        )
    }
    apa_rank = {
        lid: i + 1
        for i, lid in enumerate(
            sorted(entry_ids, key=lambda l: (apa[l]["pts"], real_table[l]["for"]), reverse=True)
        )
    }

    # ------------------------------------------------------------------
    # Best player nobody owns (latest ownership snapshot)
    # ------------------------------------------------------------------
    free_agents = []
    if os.path.exists(FREE_AGENTS_FILE):
        with open(FREE_AGENTS_FILE, newline="") as f:
            rows = list(csv.DictReader(f))
        if rows:
            latest_date = max(r["snapshot_date"] for r in rows)
            for r in rows:
                if r["snapshot_date"] != latest_date or r["owner_entry_id"]:
                    continue
                pid = int(r["player_id"])
                pts = live_points[target_gw].get(pid, 0)
                info = player_lookup.get(pid, {})
                free_agents.append({
                    "name": r["player_name"],
                    "position": r["position"],
                    "team": team_lookup.get(info.get("team"), ""),
                    "gw_points": pts,
                })
    free_agents.sort(key=lambda x: x["gw_points"], reverse=True)

    # ------------------------------------------------------------------
    # Assemble
    # ------------------------------------------------------------------
    managers = []
    for lid, info in entry_lookup.items():
        managers.append({
            "manager": info["manager"],
            "team_name": info["team_name"],
            "xi": xi_score[target_gw][lid],
            "bench": bench_score[target_gw][lid],
            "bench_detail": bench_detail.get(lid, []),
            "form": form[lid],
            "real_rank": real_rank[lid],
            "real_pts": real_table[lid]["pts"],
            "pts_for": real_table[lid]["for"],
            "apa_rank": apa_rank[lid],
            "apa_pts": apa[lid]["pts"],
            "apa_record": [apa[lid]["w"], apa[lid]["d"], apa[lid]["l"]],
            "luck": real_rank[lid] - apa_rank[lid],  # + = table flatters them
            "trophies": trophies[lid],
        })
    managers.sort(key=lambda m: m["real_rank"])

    payload = {
        "gameweek": target_gw,
        "finished_gameweeks": finished,
        "results": results[target_gw],
        "managers": managers,
        "free_agents": free_agents[:5],
    }

    os.makedirs(SEASON_DIR, exist_ok=True)
    with open(JSON_OUT, "w") as f:
        json.dump(payload, f, indent=2)
    print(f"Wrote {JSON_OUT}")

    # --------------------------- markdown ---------------------------
    lines = [f"# Dashboard Stats — GW{target_gw}\n"]

    lines.append(f"## GW{target_gw} Results\n")
    lines.append("| Home | | Score | | Away |")
    lines.append("|---|---|---|---|---|")
    for r in payload["results"]:
        lines.append(
            f"| {r['home']} | {r['home_pts']} | vs | {r['away_pts']} | {r['away']} |"
        )
    lines.append("")

    lines.append("## Left On The Bench\n")
    lines.append("_Points scored by players the manager didn't start._\n")
    lines.append("| Bench pts | Manager | Worst call |")
    lines.append("|---|---|---|")
    for m in sorted(managers, key=lambda x: x["bench"], reverse=True):
        worst = m["bench_detail"][0] if m["bench_detail"] else ("--", 0)
        lines.append(f"| {m['bench']} | {m['manager']} | {worst[0]} ({worst[1]} pts) |")
    lines.append("")

    lines.append("## Form Guide\n")
    lines.append("| Manager | Form (oldest → newest) |")
    lines.append("|---|---|")
    for m in managers:
        lines.append(f"| {m['manager']} | {' '.join(m['form'])} |")
    lines.append("")

    lines.append("## All-Play-All Table\n")
    lines.append(
        "_If everyone played everyone every week. Strips fixture luck out entirely; "
        "the Swing column is real table position minus all-play-all position "
        "(positive = the fixtures have been kind)._\n"
    )
    lines.append("| APA Rank | Manager | APA Pts | W-D-L | Real Rank | Swing |")
    lines.append("|---|---|---|---|---|---|")
    for m in sorted(managers, key=lambda x: x["apa_rank"]):
        w, d, l = m["apa_record"]
        swing = m["luck"]
        swing_str = f"+{swing}" if swing > 0 else str(swing)
        lines.append(
            f"| {m['apa_rank']} | {m['manager']} | {m['apa_pts']} | {w}-{d}-{l} "
            f"| {m['real_rank']} | {swing_str} |"
        )
    lines.append("")

    if free_agents:
        lines.append("## Best Players Nobody Owns\n")
        lines.append(f"| GW{target_gw} pts | Player | Pos | Team |")
        lines.append("|---|---|---|---|")
        for fa in payload["free_agents"]:
            lines.append(f"| {fa['gw_points']} | {fa['name']} | {fa['position']} | {fa['team']} |")
        lines.append("")

    lines.append("## Trophy Cabinet\n")
    lines.append("_Running totals across every finished gameweek._\n")
    lines.append("| Manager | Weeks topped | Weeks bottomed | Robbed blind | W-D-L |")
    lines.append("|---|---|---|---|---|")
    for m in managers:
        t = m["trophies"]
        lines.append(
            f"| {m['manager']} | {t['topped']} | {t['bottomed']} | {t['robbed']} "
            f"| {t['won']}-{t['drew']}-{t['lost']} |"
        )
    lines.append("")

    with open(MD_OUT, "w") as f:
        f.write("\n".join(lines))
    print(f"Wrote {MD_OUT}")


if __name__ == "__main__":
    main()
