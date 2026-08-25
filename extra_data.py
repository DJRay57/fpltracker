import requests
import csv
import json
import os
import time

BASE = "https://draft.premierleague.com/api"
LEAGUE_ID = 1139
SEASON = "2026-27"  # update when running this for a different (completed) season
SEASON_DIR = os.path.join("seasons", SEASON)

POSITION_LABELS = {1: "GKP", 2: "DEF", 3: "MID", 4: "FWD"}


def fetch(url):
    r = requests.get(url, timeout=10)
    r.raise_for_status()
    return r.json()


def write_csv(filename, fieldnames, rows):
    os.makedirs(SEASON_DIR, exist_ok=True)
    filename = os.path.join(SEASON_DIR, filename)
    with open(filename, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    print(f"  -> Wrote {len(rows)} rows to {filename}")


def main():
    # ------------------------------------------------------------------
    # Shared static data (reused across all three tasks)
    # ------------------------------------------------------------------
    print("Loading static data...")

    bootstrap = fetch(f"{BASE}/bootstrap-static")
    player_lookup = {
        el["id"]: {
            "name": el["web_name"],
            "position": POSITION_LABELS[el["element_type"]],
        }
        for el in bootstrap["elements"]
    }
    print(f"  {len(player_lookup)} players loaded")

    league_data = fetch(f"{BASE}/league/{LEAGUE_ID}/details")

    # league_entry_lookup: keyed by the 'id' field (used in matches)
    # entry_lookup:        keyed by 'entry_id' (used in picks/history calls)
    league_entry_lookup = {}
    entry_lookup = {}
    for e in league_data["league_entries"]:
        info = {
            "manager": f"{e['player_first_name']} {e['player_last_name']}",
            "team_name": e["entry_name"],
            "entry_id": e["entry_id"],
            "league_entry_id": e["id"],
        }
        league_entry_lookup[e["id"]] = info
        entry_lookup[e["entry_id"]] = info

    print(f"  {len(entry_lookup)} managers loaded")

    # ==================================================================
    # TASK 1: H2H Match Results  ->  h2h_matches.csv
    # ==================================================================
    print("\n=== Task 1: H2H Match Results ===")

    matches = league_data["matches"]
    print(f"Total matches found: {len(matches)}")
    print("\nFirst 3 match structures (raw API fields):")
    for m in matches[:3]:
        print(f"  {json.dumps(m)}")

    rows = []
    for m in matches:
        home_lid = m["league_entry_1"]
        away_lid = m["league_entry_2"]
        home_info = league_entry_lookup.get(home_lid, {})
        away_info = league_entry_lookup.get(away_lid, {})
        home_score = m["league_entry_1_points"]
        away_score = m["league_entry_2_points"]

        # Compute winner from scores (winning_league_entry field is unreliable/null)
        if home_score > away_score:
            winner_entry_id = home_info.get("entry_id", "")
        elif away_score > home_score:
            winner_entry_id = away_info.get("entry_id", "")
        else:
            winner_entry_id = "draw"

        rows.append({
            "gameweek": m["event"],
            "home_entry_id": home_info.get("entry_id", home_lid),
            "home_manager": home_info.get("manager", "Unknown"),
            "home_score": home_score,
            "away_entry_id": away_info.get("entry_id", away_lid),
            "away_manager": away_info.get("manager", "Unknown"),
            "away_score": away_score,
            "winner_entry_id": winner_entry_id,
        })

    write_csv(
        "h2h_matches.csv",
        ["gameweek", "home_entry_id", "home_manager", "home_score",
         "away_entry_id", "away_manager", "away_score", "winner_entry_id"],
        rows,
    )

    # ==================================================================
    # TASK 2: Transfer/Waiver History  ->  transfers.csv
    # ==================================================================
    print("\n=== Task 2: Transfer/Waiver History ===")
    print("Note: entry/{entry_id}/transfers returns 404 for FPL Draft.")
    print("Checking entry/{entry_id}/history for each manager instead...\n")

    # Show structure of one history response
    sample_entry_id = list(entry_lookup.keys())[0]
    sample_r = requests.get(f"{BASE}/entry/{sample_entry_id}/history", timeout=10)
    if sample_r.status_code == 200:
        sample_history = sample_r.json()["history"]
        print(f"History response structure (one GW item):")
        print(f"  {json.dumps(sample_history[0], indent=2)}")
        print()
    time.sleep(0.3)

    transfer_rows = []
    for entry_id, info in entry_lookup.items():
        r = requests.get(f"{BASE}/entry/{entry_id}/history", timeout=10)
        if r.status_code != 200:
            print(f"  {info['manager']}: HTTP {r.status_code}")
            time.sleep(0.3)
            continue

        history = r.json()["history"]
        total = sum(h["event_transfers"] for h in history)
        print(f"  {info['manager']:<30s}  total event_transfers: {total}")

        for h in history:
            if h["event_transfers"] > 0:
                # event_transfers > 0 but detailed player-level endpoint
                # (entry/{id}/transfers) returns 404 in FPL Draft.
                # Record the count only.
                for _ in range(h["event_transfers"]):
                    transfer_rows.append({
                        "gameweek": h["event"],
                        "manager_name": info["manager"],
                        "entry_id": entry_id,
                        "player_in_id": "",
                        "player_in_name": "",
                        "player_out_id": "",
                        "player_out_name": "",
                    })

        time.sleep(0.3)

    if not transfer_rows:
        print(
            "\nResult: 0 transfers recorded across all 10 managers / 38 GWs.\n"
            "This league played with original draft squads all season.\n"
            "Creating transfers.csv with headers only."
        )

    write_csv(
        "transfers.csv",
        ["gameweek", "manager_name", "entry_id",
         "player_in_id", "player_in_name", "player_out_id", "player_out_name"],
        transfer_rows,
    )

    # ==================================================================
    # TASK 3: Draft Pick Order  ->  draft_order.csv
    # ==================================================================
    print("\n=== Task 3: Draft Pick Order ===")

    r = requests.get(f"{BASE}/draft/{LEAGUE_ID}/choices", timeout=10)
    print(f"draft/{LEAGUE_ID}/choices status: {r.status_code}")

    if r.status_code != 200:
        print(f"Unexpected response: {r.text[:300]}")
        print(f"\nTrying draft/{LEAGUE_ID}/picks as fallback...")
        r = requests.get(f"{BASE}/draft/{LEAGUE_ID}/picks", timeout=10)
        print(f"Status: {r.status_code}  Response: {r.text[:300]}")
        return

    data = r.json()
    choices = data["choices"]
    num_rounds = len(set(c["round"] for c in choices))
    num_teams = len(entry_lookup)
    print(f"Found {len(choices)} picks  ({num_rounds} rounds × {num_teams} teams)")

    print("\nFirst 3 pick structures (raw API fields):")
    for c in choices[:3]:
        print(f"  {json.dumps(c)}")
    print()

    rows = []
    for c in choices:
        player = player_lookup.get(c["element"], {"name": "Unknown", "position": "?"})
        # overall pick = sequential position 1..150
        overall_pick = (c["round"] - 1) * num_teams + c["pick"]
        rows.append({
            "pick_number": overall_pick,
            "round": c["round"],
            "manager_name": f"{c['player_first_name']} {c['player_last_name']}",
            "entry_id": c["entry"],
            "player_id": c["element"],
            "player_name": player["name"],
            "player_position": player["position"],
            "was_auto_pick": c["was_auto"],
        })

    rows.sort(key=lambda x: x["pick_number"])

    write_csv(
        "draft_order.csv",
        ["pick_number", "round", "manager_name", "entry_id",
         "player_id", "player_name", "player_position", "was_auto_pick"],
        rows,
    )

    print("\nAll tasks complete!")


if __name__ == "__main__":
    main()
