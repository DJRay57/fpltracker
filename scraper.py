import requests
import csv
import os
import time

BASE = "https://draft.premierleague.com/api"
LEAGUE_ID = 1139
SEASON = "2026-27"  # update when running this for a different (completed) season
SEASON_DIR = os.path.join("seasons", SEASON)
OUTPUT_FILE = os.path.join(SEASON_DIR, "full_season_data.csv")

POSITION_LABELS = {1: "GKP", 2: "DEF", 3: "MID", 4: "FWD"}


def fetch(url):
    r = requests.get(url, timeout=10)
    r.raise_for_status()
    return r.json()


def build_player_lookup(bootstrap):
    """Return {element_id: {name, position}} for every player."""
    lookup = {}
    for el in bootstrap["elements"]:
        lookup[el["id"]] = {
            "name": el["web_name"],
            "position": POSITION_LABELS[el["element_type"]],
        }
    return lookup


def build_entry_lookup(league_entries):
    """Return {league_entry_id: {manager, team_name, entry_id}} for every manager."""
    lookup = {}
    for e in league_entries:
        lookup[e["id"]] = {
            "manager": f"{e['player_first_name']} {e['player_last_name']}",
            "team_name": e["entry_name"],
            "entry_id": e["entry_id"],
        }
    return lookup


def calculate_gw_standings(matches, entry_ids):
    """
    Work through matches event by event and return per-GW league positions.
    Returns {gw: {league_entry_id: rank}} where rank 1 = top of table.
    """
    table = {
        lid: {"league_pts": 0, "pts_for": 0}
        for lid in entry_ids
    }

    # Group matches by gameweek
    matches_by_gw = {}
    for m in matches:
        matches_by_gw.setdefault(m["event"], []).append(m)

    gw_standings = {}
    for gw in range(1, 39):
        for m in matches_by_gw.get(gw, []):
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

        sorted_ids = sorted(
            entry_ids,
            key=lambda lid: (table[lid]["league_pts"], table[lid]["pts_for"]),
            reverse=True,
        )
        gw_standings[gw] = {lid: rank + 1 for rank, lid in enumerate(sorted_ids)}

    return gw_standings


def main():
    print("Fetching static data...")

    bootstrap = fetch(f"{BASE}/bootstrap-static")
    player_lookup = build_player_lookup(bootstrap)
    print(f"  {len(player_lookup)} players loaded")

    league_data = fetch(f"{BASE}/league/{LEAGUE_ID}/details")
    entry_lookup = build_entry_lookup(league_data["league_entries"])
    print(f"  {len(entry_lookup)} managers loaded")

    gw_standings = calculate_gw_standings(
        league_data["matches"], list(entry_lookup.keys())
    )
    print("  GW standings calculated for all 38 gameweeks")

    rows = []
    total_gws = 38

    for gw in range(1, total_gws + 1):
        print(f"GW {gw:2d}/{total_gws} ", end="", flush=True)

        live_data = fetch(f"{BASE}/event/{gw}/live")
        # Keys are strings in JSON; convert to int for consistent lookup
        points_lookup = {
            int(eid): edata["stats"]["total_points"]
            for eid, edata in live_data["elements"].items()
        }

        for league_entry_id, entry_info in entry_lookup.items():
            picks_data = fetch(f"{BASE}/entry/{entry_info['entry_id']}/event/{gw}")
            league_pos = gw_standings[gw][league_entry_id]

            for pick in picks_data["picks"]:
                element_id = pick["element"]
                player = player_lookup.get(element_id, {"name": "Unknown", "position": "?"})
                rows.append({
                    "gameweek": gw,
                    "manager": entry_info["manager"],
                    "team_name": entry_info["team_name"],
                    "league_position": league_pos,
                    "squad_position": pick["position"],
                    "is_starter": pick["position"] <= 11,
                    "player_name": player["name"],
                    "player_position": player["position"],
                    "player_gw_points": points_lookup.get(element_id, 0),
                })

            time.sleep(0.1)

        print("done")

    fieldnames = [
        "gameweek",
        "manager",
        "team_name",
        "league_position",
        "squad_position",
        "is_starter",
        "player_name",
        "player_position",
        "player_gw_points",
    ]

    os.makedirs(SEASON_DIR, exist_ok=True)
    with open(OUTPUT_FILE, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"\nDone! {len(rows)} rows written to {OUTPUT_FILE}")
    print(f"  ({total_gws} GWs × {len(entry_lookup)} managers × 15 players = {total_gws * len(entry_lookup) * 15} expected rows)")


if __name__ == "__main__":
    main()
