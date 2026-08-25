import requests
import csv
import os
import time

FPL_BASE = "https://fantasy.premierleague.com/api"

PLAYERS = [
    {"name": "Foden",           "id": 414, "dropped_by": "Greg",  "drop_gw": 2},
    {"name": "O.Dango",         "id": 83,  "dropped_by": "Greg",  "drop_gw": 2},
    {"name": "Ballard",         "id": 531, "dropped_by": "Greg",  "drop_gw": 3},
    {"name": "N.Williams",      "id": 508, "dropped_by": "Greg",  "drop_gw": 7},
    {"name": "Cherki",          "id": 417, "dropped_by": "Jason", "drop_gw": 4},
    {"name": "Schade",          "id": 120, "dropped_by": "Jason", "drop_gw": 2},
    {"name": "Calvert-Lewin",   "id": 691, "dropped_by": "Jason", "drop_gw": 14},
    {"name": "Szoboszlai",      "id": 387, "dropped_by": "Jason", "drop_gw": 18},
    {"name": "Igor Jesus",      "id": 526, "dropped_by": "Piers", "drop_gw": 2},
    {"name": "Tavernier",       "id": 84,  "dropped_by": "Piers", "drop_gw": 7},
]

rows = []

for player in PLAYERS:
    url = f"{FPL_BASE}/element-summary/{player['id']}/"
    print(f"Fetching {player['name']:20s} (id={player['id']})...", end=" ", flush=True)

    r = requests.get(url, timeout=10)
    if r.status_code != 200:
        print(f"ERROR {r.status_code}: {r.text[:100]}")
        continue

    history = r.json()["history"]
    print(f"{len(history)} fixture records")

    for h in history:
        rows.append({
            "player_name":    player["name"],
            "player_id":      player["id"],
            "dropped_by":     player["dropped_by"],
            "drop_gw":        player["drop_gw"],
            "gameweek":       h["round"],
            "minutes":        h["minutes"],
            "total_points":   h["total_points"],
            "was_home":       h["was_home"],
            "team_h_score":   h["team_h_score"],
            "team_a_score":   h["team_a_score"],
        })

    time.sleep(0.3)

SEASON_DIR = os.path.join("seasons", "2025-26")
os.makedirs(SEASON_DIR, exist_ok=True)
OUTPUT = os.path.join(SEASON_DIR, "player_minutes.csv")
FIELDNAMES = [
    "player_name", "player_id", "dropped_by", "drop_gw",
    "gameweek", "minutes", "total_points",
    "was_home", "team_h_score", "team_a_score",
]

with open(OUTPUT, "w", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
    writer.writeheader()
    writer.writerows(rows)

print(f"\nDone — {len(rows)} rows written to {OUTPUT}")

# Quick summary per player
print("\nSummary:")
from itertools import groupby
rows.sort(key=lambda r: (r["dropped_by"], r["player_name"]))
for (dropped_by, name), group in groupby(rows, key=lambda r: (r["dropped_by"], r["player_name"])):
    fixtures = list(group)
    total_mins = sum(f["minutes"] for f in fixtures)
    total_pts  = sum(f["total_points"] for f in fixtures)
    drop_gw    = fixtures[0]["drop_gw"]
    post_drop  = [f for f in fixtures if f["gameweek"] >= drop_gw]
    post_mins  = sum(f["minutes"] for f in post_drop)
    post_pts   = sum(f["total_points"] for f in post_drop)
    print(
        f"  {name:<20s}  dropped by {dropped_by} GW{drop_gw:2d}"
        f"  |  season: {total_mins:4d} mins {total_pts:3d} pts"
        f"  |  after drop: {post_mins:4d} mins {post_pts:3d} pts"
    )
