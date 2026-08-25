"""
Captures point-in-time data that the Draft API does NOT retain historically:
  - player_status_log.csv : injury/availability status, news, form, rankings
  - free_agents_log.csv   : who's a free agent vs owned (waiver/trade pool)

Unlike scraper.py / extra_data.py (which can be run once at season end because
the API backfills points/picks/matches), this script must run repeatedly
during the season -- each snapshot captures state that gets overwritten by
the next gameweek and is gone for good otherwise.

Safe to run more than once per day: if today's date is already logged in a
file, that file is skipped so re-running doesn't create duplicate rows.
"""

import requests
import csv
import os
from datetime import date, datetime, timezone

BASE = "https://draft.premierleague.com/api"
LEAGUE_ID = 1139
SEASON = "2026-27"
SEASON_DIR = os.path.join("seasons", SEASON)

POSITION_LABELS = {1: "GKP", 2: "DEF", 3: "MID", 4: "FWD"}

PLAYER_STATUS_FILE = os.path.join(SEASON_DIR, "player_status_log.csv")
PLAYER_STATUS_FIELDS = [
    "snapshot_date", "gameweek", "player_id", "player_name", "position", "team_id",
    "status", "news", "news_added",
    "chance_of_playing_this_round", "chance_of_playing_next_round",
    "form", "ep_next", "ep_this", "event_points", "total_points",
    "draft_rank", "form_rank", "points_per_game_rank", "ict_index_rank",
    "penalties_order", "direct_freekicks_order", "corners_and_indirect_freekicks_order",
]

FREE_AGENTS_FILE = os.path.join(SEASON_DIR, "free_agents_log.csv")
FREE_AGENTS_FIELDS = [
    "snapshot_date", "gameweek", "player_id", "player_name", "position",
    "ownership_status", "owner_entry_id", "owner_manager", "in_accepted_trade",
]


def fetch(url):
    r = requests.get(url, timeout=10)
    r.raise_for_status()
    return r.json()


def already_logged_today(filename, snapshot_date):
    if not os.path.exists(filename):
        return False
    with open(filename, newline="") as f:
        for row in csv.DictReader(f):
            if row.get("snapshot_date") == snapshot_date:
                return True
    return False


def append_csv(filename, fieldnames, rows):
    file_exists = os.path.exists(filename)
    with open(filename, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if not file_exists:
            writer.writeheader()
        writer.writerows(rows)
    print(f"  -> Appended {len(rows)} rows to {filename}")


def main():
    os.makedirs(SEASON_DIR, exist_ok=True)
    snapshot_date = date.today().isoformat()
    print(f"Snapshot date: {snapshot_date}  ({datetime.now(timezone.utc).isoformat()})")

    print("Fetching static data...")
    bootstrap = fetch(f"{BASE}/bootstrap-static")
    current_gw = bootstrap["events"]["current"]
    elements = bootstrap["elements"]
    print(f"  {len(elements)} players loaded, current GW = {current_gw}")

    # ------------------------------------------------------------------
    # player_status_log.csv
    # ------------------------------------------------------------------
    if already_logged_today(PLAYER_STATUS_FILE, snapshot_date):
        print(f"  {PLAYER_STATUS_FILE}: already has a {snapshot_date} snapshot, skipping")
    else:
        rows = []
        for el in elements:
            rows.append({
                "snapshot_date": snapshot_date,
                "gameweek": current_gw,
                "player_id": el["id"],
                "player_name": el["web_name"],
                "position": POSITION_LABELS.get(el["element_type"], "?"),
                "team_id": el["team"],
                "status": el["status"],
                "news": el["news"],
                "news_added": el["news_added"],
                "chance_of_playing_this_round": el["chance_of_playing_this_round"],
                "chance_of_playing_next_round": el["chance_of_playing_next_round"],
                "form": el["form"],
                "ep_next": el["ep_next"],
                "ep_this": el["ep_this"],
                "event_points": el["event_points"],
                "total_points": el["total_points"],
                "draft_rank": el["draft_rank"],
                "form_rank": el["form_rank"],
                "points_per_game_rank": el["points_per_game_rank"],
                "ict_index_rank": el["ict_index_rank"],
                "penalties_order": el["penalties_order"],
                "direct_freekicks_order": el["direct_freekicks_order"],
                "corners_and_indirect_freekicks_order": el["corners_and_indirect_freekicks_order"],
            })
        append_csv(PLAYER_STATUS_FILE, PLAYER_STATUS_FIELDS, rows)

    # ------------------------------------------------------------------
    # free_agents_log.csv  (waiver/trade pool -- NOT retained by the API)
    # ------------------------------------------------------------------
    if already_logged_today(FREE_AGENTS_FILE, snapshot_date):
        print(f"  {FREE_AGENTS_FILE}: already has a {snapshot_date} snapshot, skipping")
    else:
        player_lookup = {
            el["id"]: {"name": el["web_name"], "position": POSITION_LABELS.get(el["element_type"], "?")}
            for el in elements
        }

        league_data = fetch(f"{BASE}/league/{LEAGUE_ID}/details")
        manager_by_entry_id = {
            e["entry_id"]: f"{e['player_first_name']} {e['player_last_name']}"
            for e in league_data["league_entries"]
        }

        status_data = fetch(f"{BASE}/league/{LEAGUE_ID}/element-status")

        rows = []
        for e in status_data["element_status"]:
            player = player_lookup.get(e["element"], {"name": "Unknown", "position": "?"})
            rows.append({
                "snapshot_date": snapshot_date,
                "gameweek": current_gw,
                "player_id": e["element"],
                "player_name": player["name"],
                "position": player["position"],
                "ownership_status": e["status"],  # "a" = free agent, "o" = owned
                "owner_entry_id": e["owner"] or "",
                "owner_manager": manager_by_entry_id.get(e["owner"], ""),
                "in_accepted_trade": e["in_accepted_trade"],
            })
        append_csv(FREE_AGENTS_FILE, FREE_AGENTS_FIELDS, rows)

    print("\nDone!")


if __name__ == "__main__":
    main()
