"""
Ranks every accepted waiver/trade transaction in the league by points impact:
for each swap, how many points has the player brought in scored so far vs.
the player dropped (using season-to-date total_points).

Uses the (undocumented, but public) draft/league/{id}/transactions endpoint,
which the official transactions/trades endpoints don't expose but this one
does -- returns every waiver bid with a result code:
  'a'  = accepted (a real roster change)
  'di' = declined (invalid/ineligible bid)
  'do' = declined (outbid by another priority)
Only 'a' transactions represent real swaps.

Writes seasons/2026-27/trade_impact.md.
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
    players = {el["id"]: {"name": el["web_name"], "pts": el["total_points"]} for el in bootstrap["elements"]}

    league_data = fetch(f"{BASE}/league/{LEAGUE_ID}/details")
    managers = {
        e["entry_id"]: f"{e['player_first_name']} {e['player_last_name']}"
        for e in league_data["league_entries"]
    }

    tx = fetch(f"{BASE}/draft/league/{LEAGUE_ID}/transactions")["transactions"]
    accepted = sorted([t for t in tx if t["result"] == "a"], key=lambda t: t["id"])

    if not accepted:
        print("No accepted transactions yet -- nothing to rank.")
        return

    rows = []
    for t in accepted:
        pin, pout = players[t["element_in"]], players[t["element_out"]]
        net = pin["pts"] - pout["pts"]
        rows.append({
            "manager": managers.get(t["entry"], "Unknown"),
            "in_name": pin["name"], "in_pts": pin["pts"],
            "out_name": pout["name"], "out_pts": pout["pts"],
            "net": net,
        })

    # Manager leaderboard (all managers, including those with zero moves)
    agg = {m: {"net": 0, "moves": []} for m in managers.values()}
    for r in rows:
        agg[r["manager"]]["net"] += r["net"]
        agg[r["manager"]]["moves"].append(r["in_name"])

    os.makedirs(SEASON_DIR, exist_ok=True)
    out_path = os.path.join(SEASON_DIR, "trade_impact.md")

    lines = ["# Trade Impact\n"]
    lines.append(
        "_Every accepted waiver move, ranked by points scored so far by the player "
        "brought in vs. the player dropped._\n"
    )

    lines.append("## Manager Leaderboard\n")
    lines.append("| Net | Manager | Key Moves |")
    lines.append("|---|---|---|")
    for m, d in sorted(agg.items(), key=lambda kv: kv[1]["net"], reverse=True):
        net_str = f"+{d['net']}" if d["net"] > 0 else str(d["net"])
        moves = ", ".join(d["moves"]) if d["moves"] else "None"
        lines.append(f"| {net_str} | {m} | {moves} |")

    lines.append("\n## Every Trade, Ranked\n")
    lines.append("| Net | Manager | In | Out |")
    lines.append("|---|---|---|---|")
    for r in sorted(rows, key=lambda r: r["net"], reverse=True):
        net_str = f"+{r['net']}" if r["net"] > 0 else str(r["net"])
        lines.append(
            f"| {net_str} | {r['manager']} | {r['in_name']} ({r['in_pts']}) | "
            f"{r['out_name']} ({r['out_pts']}) |"
        )

    with open(out_path, "w") as f:
        f.write("\n".join(lines) + "\n")

    print(f"\nWrote {out_path}")


if __name__ == "__main__":
    main()
