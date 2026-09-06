"""
Tracks the gameweek that's currently being played.

The recap scripts only ever look at finished gameweeks. This one covers the
gap: while GW{n} is in progress it works out, for every manager, what their
starting XI has scored so far, how many of them still have football to come,
and where the score is likely to end up.

Projection method, per starter:
  - fixture not kicked off yet  -> add the player's expected points (ep_this)
  - fixture in progress         -> add ep_this pro-rata for the minutes left
  - fixture finished            -> add nothing; the points are already banked
so the projection converges on the real score as the day goes on.

Writes live_gameweek.json (+ .md) including the timestamp of the fetch. If no
gameweek is in progress the file records state "idle" and the dashboard falls
back to showing the pre-gameweek predictions instead.
"""

import json
import math
import os
import random
import statistics
from datetime import datetime, timezone

import requests

import scoring_model

DRAFT = "https://draft.premierleague.com/api"
CLASSIC = "https://fantasy.premierleague.com/api"
LEAGUE_ID = 1139
SEASON = "2026-27"
SEASON_DIR = os.path.join("seasons", SEASON)
JSON_OUT = os.path.join(SEASON_DIR, "live_gameweek.json")
MD_OUT = os.path.join(SEASON_DIR, "live_gameweek.md")

UA = {"User-Agent": "Mozilla/5.0"}
FULL_MATCH = 90.0
POSITION = {1: "GK", 2: "DEF", 3: "MID", 4: "FWD"}
# A legal XI: one keeper, three at the back, two in midfield, one up top.
MINIMUMS = {"GK": 1, "DEF": 3, "MID": 2, "FWD": 1}


def legal(types):
    counts = {k: 0 for k in MINIMUMS}
    for t in types:
        counts[t] += 1
    return counts["GK"] == 1 and all(counts[k] >= v for k, v in MINIMUMS.items())


def autosub(xi, bench, blanks):
    """Apply the game's substitution rules to starters who didn't feature.

    `xi` and `bench` are [(position_type, points, played)], bench in its stated
    order. A blank is replaced by the first bench player who actually played
    and whose introduction leaves a legal side; keepers only cover keepers.
    Returns the resulting score and the bench slots still unused.
    """
    xi = list(xi)
    spare = list(range(len(bench)))
    for idx in blanks:
        out_type = xi[idx][0]
        for slot in list(spare):
            cand = bench[slot]
            if not cand[2]:                       # never came on: not eligible
                continue
            if (out_type == "GK") != (cand[0] == "GK"):
                continue
            trial = [p[0] for p in xi]
            trial[idx] = cand[0]
            if legal(trial):
                xi[idx] = cand
                spare.remove(slot)
                break
    return sum(p[1] for p in xi), spare


# Used only until enough of the gameweek has been played to measure the real
# forecast error; close to what it typically settles at.
FALLBACK_SIGMA = 2.6


def fetch(url):
    r = requests.get(url, headers=UA, timeout=20)
    r.raise_for_status()
    return r.json()


def write(payload, lines):
    os.makedirs(SEASON_DIR, exist_ok=True)
    with open(JSON_OUT, "w") as f:
        json.dump(payload, f, indent=2)
    with open(MD_OUT, "w") as f:
        f.write("\n".join(lines))
    print(f"Wrote {JSON_OUT}")
    print(f"Wrote {MD_OUT}")


def main():
    now = datetime.now(timezone.utc).replace(microsecond=0)
    bootstrap = fetch(f"{DRAFT}/bootstrap-static")
    events = bootstrap["events"]
    current = events["current"]
    event = next((e for e in events["data"] if e["id"] == current), None)

    if not current or not event or event["finished"]:
        print(f"No gameweek in progress (current={current}).")
        write({"state": "idle", "updated_at": now.isoformat()},
              ["# Live Gameweek\n", "_No gameweek in progress._"])
        return

    gw = current
    print(f"GW{gw} is in progress")

    fixtures = fetch(f"{CLASSIC}/fixtures/?event={gw}")
    classic = fetch(f"{CLASSIC}/bootstrap-static/")

    # Map by short_name rather than trusting the two APIs to share ids
    draft_team = {t["id"]: t["short_name"] for t in bootstrap["teams"]}
    classic_id = {t["short_name"]: t["id"] for t in classic["teams"]}

    ep_this = {}
    for el in classic["elements"]:
        try:
            ep_this[el["id"]] = float(el.get("ep_this") or 0)
        except (TypeError, ValueError):
            ep_this[el["id"]] = 0.0

    # For each classic team id, the fixtures it still has to come this gameweek
    team_fixtures = {}
    for f in fixtures:
        for tid in (f["team_h"], f["team_a"]):
            team_fixtures.setdefault(tid, []).append(f)

    started = sum(1 for f in fixtures if f["started"])
    done = sum(1 for f in fixtures if f["finished_provisional"])
    state = "pre" if started == 0 else ("done" if done == len(fixtures) else "live")

    live = fetch(f"{DRAFT}/event/{gw}/live")["elements"]
    points = {int(eid): d["stats"]["total_points"] for eid, d in live.items()}

    players = {}
    for el in bootstrap["elements"]:
        players[el["id"]] = {
            "name": el["web_name"],
            "type": POSITION[el["element_type"]],
            "team": classic_id.get(draft_team.get(el["team"], ""), el["team"]),
        }

    # How wrong the expected-points figure typically is, measured on the
    # players who have already started this gameweek. Falls back to a typical
    # value early on, before there's enough played to measure.
    # Only players whose match is actually over: someone still on the pitch
    # has a part-finished score, which would understate the real spread.
    settled_teams = {t for f in fixtures if f["finished_provisional"]
                     for t in (f["team_h"], f["team_a"])}
    residuals = [
        points.get(pid, 0) - ep_this.get(pid, 0.0)
        for pid, d in ((int(k), v) for k, v in live.items())
        if d["stats"].get("starts") and players.get(pid, {}).get("team") in settled_teams
    ]
    if len(residuals) >= 30:
        sigma = max(1.0, min(statistics.pstdev(residuals), 6.0))
    else:
        sigma = FALLBACK_SIGMA
    print(f"  per-player forecast error: {sigma:.2f} pts (n={len(residuals)})")

    # Comparable-player pools, shared with predictions.py so the live odds and
    # the pre-gameweek ones are built the same way. Every gameweek played so
    # far, not just this one: a single week's sample is far too small and
    # inherits that week's luck.
    played_before = [e["id"] for e in events["data"] if e["finished"] and e["id"] < gw]
    samples = [
        (ep_this.get(pid, 0.0), points.get(pid, 0), players.get(pid, {}).get("type"))
        for pid, d in ((int(k), v) for k, v in live.items())
        if d["stats"].get("starts") and players.get(pid, {}).get("team") in settled_teams
    ]
    samples += scoring_model.gather_samples(fetch, DRAFT, ep_this, players, played_before)
    pools = scoring_model.Pools(samples)
    print(f"  comparable-player sample: {len(pools)} starter performances")

    def remaining_for(pid):
        """(still_to_play, expected points to come, share of a match left)."""
        team = players.get(pid, {}).get("team")
        to_come = 0.0
        share = 0.0
        pending = False
        for f in team_fixtures.get(team, []):
            if f["finished_provisional"]:
                continue
            pending = True
            left = 1.0 if not f["started"] else max(
                0.0, 1.0 - (f.get("minutes") or 0) / FULL_MATCH)
            to_come += ep_this.get(pid, 0.0) * left
            share += left
        return pending, to_come, share

    league = fetch(f"{DRAFT}/league/{LEAGUE_ID}/details")
    entries = {}
    for e in league["league_entries"]:
        entries[e["id"]] = {
            "manager": f"{e['player_first_name']} {e['player_last_name']}",
            "team_name": e["entry_name"],
            "entry_id": e["entry_id"],
        }

    # The league table's own points lag the live player feed by some minutes
    # and settle later. Keep them alongside ours so the two can be compared,
    # but display the live figure -- it's the one that actually moves.
    official = {}
    for m in league["matches"]:
        if m["event"] != gw:
            continue
        official[m["league_entry_1"]] = m["league_entry_1_points"]
        official[m["league_entry_2"]] = m["league_entry_2_points"]

    managers = {}
    def minutes_of(pid):
        return live.get(str(pid), {}).get("stats", {}).get("minutes", 0)

    for lid, info in entries.items():
        picks = fetch(f"{DRAFT}/entry/{info['entry_id']}/event/{gw}")["picks"]
        starters = sorted((p for p in picks if p["position"] <= 11),
                          key=lambda x: x["position"])
        bench = sorted((p for p in picks if p["position"] > 11),
                       key=lambda x: x["position"])

        xi = [(players.get(p["element"], {}).get("type", "MID"),
               points.get(p["element"], 0) * p.get("multiplier", 1),
               minutes_of(p["element"]) > 0) for p in starters]
        bench_rows = [(players.get(p["element"], {}).get("type", "MID"),
                       points.get(p["element"], 0),
                       minutes_of(p["element"]) > 0) for p in bench]

        # A starter on nought whose match is over is never going to play, so
        # his replacement is already decided -- bank it now rather than leaving
        # a hole in the score the manager will not actually finish with.
        blanks = [i for i, p in enumerate(starters)
                  if minutes_of(p["element"]) == 0
                  and not remaining_for(p["element"])[0]]
        current_pts, spare = autosub(xi, bench_rows, blanks)
        subbed = [players.get(starters[i]["element"], {}).get("name", "?") for i in blanks]

        xi_types = [t for t, _, _ in xi]
        bench_avail = [(s, bench_rows[s][0], bench_rows[s][1], bench_rows[s][2])
                       for s in spare]

        to_play = 0
        to_come = 0.0
        remaining_list = []
        yet = []
        for idx, p in enumerate(starters):
            pid = p["element"]
            pending, extra, share = remaining_for(pid)
            if not pending:
                continue
            pinfo = players.get(pid, {})
            entry = {
                "ep": ep_this.get(pid, 0.0),
                "pos": pinfo.get("type", "MID"),
                "share": share,
                "team": pinfo.get("team"),
                # who could legally come on for this particular man
                "cover": scoring_model.eligible_cover(xi_types, idx, bench_avail),
            }
            # A man on nought in a match already under way has been left out of
            # the side. He can still come off the bench, but that gets less
            # likely as the match runs down -- and if he doesn't, his cover
            # comes on at the end of the gameweek.
            if share < 0.99 and minutes_of(pid) == 0:
                entry["p_play"] = min(
                    scoring_model.SUB_APPEARANCE * share,
                    entry["ep"] / 3.0 if entry["ep"] else 0.0,
                )
                to_come += entry["ep"] * entry["p_play"]
                if entry["cover"]:
                    to_come += (1 - entry["p_play"]) * entry["cover"][0][1]
            else:
                to_come += extra
            to_play += 1
            remaining_list.append(entry)
            yet.append(pinfo.get("name", "?"))

        managers[lid] = {
            "manager": info["manager"],
            "team_name": info["team_name"],
            "current": current_pts,
            "official": official.get(lid),
            "to_play": to_play,
            "projection": round(current_pts + to_come, 1),
            "remaining": remaining_list,
            "to_come": round(to_come, 1),
            "auto_subbed": subbed,
            "yet_to_play": sorted(yet),
        }

    fixtures_out = []
    for m in league["matches"]:
        if m["event"] != gw:
            continue
        a, b = managers[m["league_entry_1"]], managers[m["league_entry_2"]]
        hw, dr, aw = scoring_model.match_odds(
            pools, (a["current"], a["remaining"]), (b["current"], b["remaining"]))
        fixtures_out.append({
            "home_win": hw, "draw": dr, "away_win": aw,
            "home": a["manager"], "home_team": a["team_name"],
            "home_current": a["current"], "home_to_play": a["to_play"],
            "home_projection": a["projection"], "home_to_come": a["to_come"],
            "away": b["manager"], "away_team": b["team_name"],
            "away_current": b["current"], "away_to_play": b["to_play"],
            "away_projection": b["projection"], "away_to_come": b["to_come"],
        })

    for m in managers.values():
        m.pop("remaining", None)

    payload = {
        "state": state,
        "sigma": round(sigma, 2),
        "team_rho": scoring_model.TEAM_RHO,
        "gameweek": gw,
        "updated_at": now.isoformat(),
        "fixtures_total": len(fixtures),
        "fixtures_finished": done,
        "fixtures_started": started,
        "players_to_play": sum(m["to_play"] for m in managers.values()),
        "fixtures": fixtures_out,
        "managers": sorted(managers.values(), key=lambda m: m["current"], reverse=True),
    }

    lines = [
        f"# Live Gameweek — GW{gw}\n",
        f"_State: {state}. {done} of {len(fixtures)} matches finished. "
        f"Updated {now.strftime('%Y-%m-%d %H:%M')} UTC._\n",
        "_Scores are computed from the live player feed and include provisional "
        "bonus, so they can run ahead of the official league table, which settles "
        "later._\n",
        f"_Win chances come from simulating the players still to play, drawing "
        f"their scores from what players actually did this gameweek (forecast "
        f"error {sigma:.1f} pts per starter) and letting teammates move together. "
        f"They tighten as matches finish, so late in a gameweek they get "
        f"lopsided quickly._\n",
        "## Head to Head\n",
        "| Home | Now | Proj | Win% | Draw% | Away | Now | Proj | Win% |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for f in fixtures_out:
        lines.append(
            f"| {f['home']} | {f['home_current']} | {f['home_projection']} | {f['home_win']:.0f}% "
            f"| {f['draw']:.0f}% | {f['away']} | {f['away_current']} | {f['away_projection']} "
            f"| {f['away_win']:.0f}% |"
        )
    lines.append("")
    lines.append("## Live Scores\n")
    lines.append("| Manager | Now | Projection | Players left |")
    lines.append("|---|---|---|---|")
    for m in payload["managers"]:
        lines.append(
            f"| {m['manager']} | {m['current']} | {m['projection']} | {m['to_play']} |"
        )
    lines.append("")

    write(payload, lines)
    print(f"  state={state}, {done}/{len(fixtures)} matches done, "
          f"{payload['players_to_play']} players still to play")


if __name__ == "__main__":
    main()
