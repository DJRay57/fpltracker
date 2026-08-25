"""
Compares each manager's starting XI between the two most recently finished
gameweeks (prev_gw -> target_gw) and isolates the points impact of whoever
changed -- players newly in the XI vs players dropped out of it. This
catches every lineup change (waiver trades AND simple bench/start swaps),
not just formal trades -- see trade_impact.py for the waiver-only view.

Only players who actually played (minutes > 0 in target_gw) count toward
the net -- a player who was rested/injured/on international duty and
didn't feature shouldn't swing the number just because they scored 0 for
not playing at all. Non-players are still listed for transparency, just
excluded from the sums, tagged "DNP".

Requires at least 2 finished gameweeks. Writes seasons/2026-27/lineup_impact.md.
"""

import requests
import os

BASE = "https://draft.premierleague.com/api"
LEAGUE_ID = 1139
SEASON = "2026-27"
SEASON_DIR = os.path.join("seasons", SEASON)


def fetch(url):
    r = requests.get(url, timeout=10)
    r.raise_for_status()
    return r.json()


def main():
    print("Fetching data...")
    bootstrap = fetch(f"{BASE}/bootstrap-static")
    names = {el["id"]: el["web_name"] for el in bootstrap["elements"]}

    finished = sorted(e["id"] for e in bootstrap["events"]["data"] if e["finished"])
    if len(finished) < 2:
        print(
            f"Only {len(finished)} finished gameweek(s) so far -- need at least 2 "
            "to compare lineups. Nothing to write yet."
        )
        return
    target_gw, prev_gw = finished[-1], finished[-2]
    print(f"Comparing GW{prev_gw} starting XI -> GW{target_gw} starting XI")

    live = fetch(f"{BASE}/event/{target_gw}/live")
    points_lookup = {int(eid): d["stats"]["total_points"] for eid, d in live["elements"].items()}
    minutes_lookup = {int(eid): d["stats"]["minutes"] for eid, d in live["elements"].items()}

    league_data = fetch(f"{BASE}/league/{LEAGUE_ID}/details")
    entry_lookup = {
        e["id"]: {"manager": f"{e['player_first_name']} {e['player_last_name']}", "entry_id": e["entry_id"]}
        for e in league_data["league_entries"]
    }

    rows = []  # one row per manager
    for lid, info in entry_lookup.items():
        prev_picks = fetch(f"{BASE}/entry/{info['entry_id']}/event/{prev_gw}")["picks"]
        curr_picks = fetch(f"{BASE}/entry/{info['entry_id']}/event/{target_gw}")["picks"]

        prev_xi = {p["element"] for p in prev_picks if p["position"] <= 11}
        curr_xi = {p["element"] for p in curr_picks if p["position"] <= 11}

        added = curr_xi - prev_xi
        dropped = prev_xi - curr_xi

        changes = []
        net = 0
        for pid in added:
            pts, mins = points_lookup.get(pid, 0), minutes_lookup.get(pid, 0)
            played = mins > 0
            changes.append({"kind": "in", "name": names.get(pid, "Unknown"), "pts": pts, "played": played})
            if played:
                net += pts
        for pid in dropped:
            pts, mins = points_lookup.get(pid, 0), minutes_lookup.get(pid, 0)
            played = mins > 0
            changes.append({"kind": "out", "name": names.get(pid, "Unknown"), "pts": pts, "played": played})
            if played:
                net -= pts

        rows.append({"manager": info["manager"], "net": net, "changes": changes})

    os.makedirs(SEASON_DIR, exist_ok=True)
    out_path = os.path.join(SEASON_DIR, "lineup_impact.md")

    lines = [f"# Lineup Impact — GW{prev_gw} → GW{target_gw}\n"]
    lines.append(
        "_Starting XI changes between the two most recent gameweeks (trades and "
        "plain bench/start swaps both count). Net only counts players who actually "
        "played that gameweek -- others are listed but tagged DNP and excluded._\n"
    )

    lines.append("## Manager Leaderboard\n")
    lines.append("| Net | Manager | Changes |")
    lines.append("|---|---|---|")
    for r in sorted(rows, key=lambda r: r["net"], reverse=True):
        net_str = f"+{r['net']}" if r["net"] > 0 else str(r["net"])
        if not r["changes"]:
            lines.append(f"| {net_str} | {r['manager']} | No lineup changes |")
            continue
        parts = []
        for c in r["changes"]:
            sign = "+" if c["kind"] == "in" else "-"
            tag = f"{c['pts']}pt" if c["played"] else "DNP"
            parts.append(f"{sign}{c['name']} ({tag})")
        lines.append(f"| {net_str} | {r['manager']} | {', '.join(parts)} |")

    with open(out_path, "w") as f:
        f.write("\n".join(lines) + "\n")

    print(f"\nWrote {out_path}")


if __name__ == "__main__":
    main()
