"""
Ranks every accepted waiver/trade transaction in the league by points impact
SINCE it took effect: points the player brought in has scored from that
gameweek onward vs. points the player dropped has scored in that same
window -- i.e. the counterfactual of what you'd have gotten had you kept
them instead. Points from before the trade don't count either way.

Uses the (undocumented, but public) draft/league/{id}/transactions endpoint,
which the official transactions/trades endpoints don't expose but this one
does -- returns every waiver bid with a result code:
  'a'  = accepted (a real roster change)
  'di' = declined (invalid/ineligible bid)
  'do' = declined (outbid by another priority)
Only 'a' transactions represent real swaps. Each transaction's `event` field
is the first gameweek it's in effect for.

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
    players = {el["id"]: el["web_name"] for el in bootstrap["elements"]}
    finished_gws = sorted(e["id"] for e in bootstrap["events"]["data"] if e["finished"])

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

    # Fetch each finished gameweek's live points once and reuse across all trades.
    points_by_gw = {}
    for gw in finished_gws:
        live = fetch(f"{BASE}/event/{gw}/live")
        points_by_gw[gw] = {int(eid): d["stats"]["total_points"] for eid, d in live["elements"].items()}

    rows, pending = [], []
    for t in accepted:
        effective_gw = t["event"]
        played_gws = [gw for gw in finished_gws if gw >= effective_gw]
        manager = managers.get(t["entry"], "Unknown")
        in_name = players.get(t["element_in"], "Unknown")
        out_name = players.get(t["element_out"], "Unknown")

        if not played_gws:
            pending.append({
                "manager": manager, "in_name": in_name, "out_name": out_name,
                "effective_gw": effective_gw,
            })
            continue

        in_pts = sum(points_by_gw[gw].get(t["element_in"], 0) for gw in played_gws)
        out_pts = sum(points_by_gw[gw].get(t["element_out"], 0) for gw in played_gws)
        rows.append({
            "manager": manager, "in_name": in_name, "in_pts": in_pts,
            "out_name": out_name, "out_pts": out_pts, "net": in_pts - out_pts,
            "since_gw": effective_gw,
        })

    # Manager leaderboard (all managers, including those with zero scoreable moves)
    agg = {m: {"net": 0, "moves": []} for m in managers.values()}
    for r in rows:
        agg[r["manager"]]["net"] += r["net"]
        agg[r["manager"]]["moves"].append(r["in_name"])

    os.makedirs(SEASON_DIR, exist_ok=True)
    out_path = os.path.join(SEASON_DIR, "trade_impact.md")

    trade_master_name, trade_master_stats = max(agg.items(), key=lambda kv: kv[1]["net"])
    trade_master_net = trade_master_stats["net"]

    lines = ["# Trade Impact\n"]
    lines.append(
        "_Every accepted waiver move, scored from the gameweek it took effect "
        "onward: points the player brought in has scored since joining vs. "
        "points the player dropped has scored in that same window (i.e. what "
        "you'd have gotten had you kept them instead). Points from before the "
        "trade don't count either way._\n"
    )
    if trade_master_net > 0:
        lines.append(f"**Trade Master: {trade_master_name}** (+{trade_master_net} pts net)\n")
    else:
        lines.append("**Trade Master:** no one's net positive yet.\n")

    lines.append("## Manager Leaderboard\n")
    lines.append("| Net | Manager | Key Moves |")
    lines.append("|---|---|---|")
    for m, d in sorted(agg.items(), key=lambda kv: kv[1]["net"], reverse=True):
        net_str = f"+{d['net']}" if d["net"] > 0 else str(d["net"])
        moves = ", ".join(d["moves"]) if d["moves"] else "None"
        lines.append(f"| {net_str} | {m} | {moves} |")

    lines.append("\n## Every Trade, Ranked\n")
    lines.append("| Net | Manager | In (pts since) | Out (pts since) | Since |")
    lines.append("|---|---|---|---|---|")
    for r in sorted(rows, key=lambda r: r["net"], reverse=True):
        net_str = f"+{r['net']}" if r["net"] > 0 else str(r["net"])
        lines.append(
            f"| {net_str} | {r['manager']} | {r['in_name']} ({r['in_pts']}) | "
            f"{r['out_name']} ({r['out_pts']}) | GW{r['since_gw']} |"
        )

    if pending:
        lines.append("\n## Pending\n")
        lines.append(
            "_Took effect from a gameweek that hasn't been played yet -- "
            "nothing to score until it has._\n"
        )
        lines.append("| Manager | In | Out | Effective |")
        lines.append("|---|---|---|---|")
        for p in pending:
            lines.append(f"| {p['manager']} | {p['in_name']} | {p['out_name']} | GW{p['effective_gw']} |")

    with open(out_path, "w") as f:
        f.write("\n".join(lines) + "\n")

    print(f"\nWrote {out_path}")


if __name__ == "__main__":
    main()
