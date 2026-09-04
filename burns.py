"""
Generates the punditry line-up for the dashboard carousel.

Every burn is derived from the actual numbers -- nobody gets abused for
something that didn't happen. Scope is deliberately narrow: league position,
bad trades, bad gameweeks, benched points and fixture luck. Nothing personal,
nothing outside the football.

SPICE controls the language:
    "full"  -- pub-standard swearing (default; it's a mates' league)
    "mild"  -- same jokes, no profanity
Set it and re-run render_dashboard.py; nothing else needs changing.
"""

import re

SPICE = "full"

# (full, mild) -- swapped in wherever a line wants some venom
WORDS = {
    "shite": ("shite", "rubbish"),
    "shit": ("shit", "dire"),
    "bollocks": ("bollocks", "nonsense"),
    "arse": ("arse", "backside"),
    "sod": ("jammy sod", "lucky thing"),
    "bastard": ("boring bastard", "boring so-and-so"),
    "fuckall": ("absolutely fuck all", "absolutely nothing"),
    "fucking": ("fucking ", ""),
    "state": ("absolute state of it", "dreadful stuff"),
    "muppet": ("muppet", "wally"),
    "pissing": ("pissing", "throwing"),
}


def w(key):
    full, mild = WORDS[key]
    return full if SPICE == "full" else mild


def num(s, default=0.0):
    m = re.search(r"-?\d+(?:\.\d+)?", str(s).replace("−", "-"))
    return float(m.group()) if m else default


def first_name(name):
    # the API returns some names lowercase ("chris Purnell"); tidy for prose
    part = name.split()[0]
    return part[:1].upper() + part[1:]


def pts_in(cell):
    """The number inside 'Xhaka (15)'."""
    m = re.search(r"\((\d+)\)", str(cell))
    return int(m.group(1)) if m else 0


def burn(severity, tag, text):
    return {"severity": severity, "tag": tag, "text": text}


def generate(stats, trade_leaderboard, all_trades, limit=10):
    """Returns the best `limit` burns, harshest first."""
    managers = stats["managers"]
    by_name = {m["manager"]: m for m in managers}
    gw = stats["gameweek"]
    out = []

    ranked_by_rank = sorted(managers, key=lambda m: m["real_rank"])
    top = ranked_by_rank[0]
    bottom = ranked_by_rank[-1]
    free_agents = {f["name"]: f for f in stats.get("free_agents", [])}

    # ---- bottom of the table vs the week's best score -----------------
    best_week = max(managers, key=lambda m: m["xi"])
    if bottom["pts_for"] < best_week["xi"] * 1.6:
        out.append(burn(95, "Table", (
            f"<b>{bottom['manager']}</b> is bottom with {bottom['pts_for']} points "
            f"<em>all season</em>. {first_name(best_week['manager'])} managed "
            f"{best_week['xi']} in one {w('fucking')}week."
        )))
    else:
        out.append(burn(70, "Table", (
            f"<b>{bottom['manager']}</b> props up the table on {bottom['real_pts']} points. "
            f"Someone check he's still logging in."
        )))

    # ---- winless in the all-play-all (beaten by literally everyone) ----
    for m in managers:
        wins, draws, losses = m["apa_record"]
        if wins == 0 and losses >= 9:
            out.append(burn(93, "Merit", (
                f"<b>{m['manager']}</b> has been beaten by every manager, every week. "
                f"{losses} out of {losses}. Take a bow."
            )))
        elif m["apa_pts"] == 0 and losses:
            out.append(burn(85, "Merit", (
                f"<b>{m['manager']}</b> has zero points on merit. Not few. Zero."
            )))

    # ---- worst single trade in the league -----------------------------
    if all_trades:
        worst = min(all_trades, key=lambda t: num(t.get("Net", 0)))
        net = num(worst.get("Net", 0))
        if net <= -4:
            gone = re.sub(r"\s*\(.*", "", worst.get("Out (pts since)", ""))
            got = re.sub(r"\s*\(.*", "", worst.get("In (pts since)", ""))
            gone_pts = pts_in(worst.get("Out (pts since)", ""))
            got_pts = pts_in(worst.get("In (pts since)", ""))
            line = (
                f"<b>{worst.get('Manager','')}</b> binned {gone}, watched him rack up "
                f"{gone_pts}, and got {got}'s grand total of {got_pts} back. Inspired."
            )
            out.append(burn(90, "Trades", line))

    # ---- dropped a player who is now scoring on the waiver wire -------
    for t in all_trades:
        gone = re.sub(r"\s*\(.*", "", t.get("Out (pts since)", ""))
        fa = free_agents.get(gone)
        if fa and fa["gw_points"] >= 6 and num(t.get("Net", 0)) < 0:
            out.append(burn(88, "Trades", (
                f"<b>{t.get('Manager','')}</b> dropped {gone}, who put up {fa['gw_points']} "
                f"in GW{gw} while sat on the waiver wire, owned by nobody. Still there, by the way."
            )))
            break

    # ---- benched points that cost a match -----------------------------
    for r in stats["results"]:
        loser = (r["away"] if r["winner"] == "home"
                 else r["home"] if r["winner"] == "away" else None)
        if not loser:
            continue
        m = by_name.get(loser)
        if m and m["bench"] > r["margin"]:
            worst_pick = m["bench_detail"][0] if m["bench_detail"] else None
            tail = (f" {worst_pick[0]} alone had {worst_pick[1]}."
                    if worst_pick and worst_pick[1] else "")
            out.append(burn(92, "Bench", (
                f"<b>{m['manager']}</b> left {m['bench']} points on his {w('arse')} "
                f"and lost by {r['margin']}.{tail} {w('state').capitalize()}."
            )))

    # ---- heaviest hiding of the week ----------------------------------
    if stats["results"]:
        thrashing = max(stats["results"], key=lambda r: r["margin"])
        if thrashing["margin"] >= 15:
            winner = (thrashing["home"] if thrashing["winner"] == "home" else thrashing["away"])
            loser = (thrashing["away"] if thrashing["winner"] == "home" else thrashing["home"])
            wp = max(thrashing["home_pts"], thrashing["away_pts"])
            lp = min(thrashing["home_pts"], thrashing["away_pts"])
            out.append(burn(80, "Results", (
                f"{winner} {wp}, <b>{loser}</b> {lp}. That's not a defeat, "
                f"that's a public flogging."
            )))

    # ---- flattered by the fixtures ------------------------------------
    flattered = [m for m in managers if m["luck"] <= -3]
    for m in sorted(flattered, key=lambda x: x["luck"])[:1]:
        out.append(burn(78, "Merit", (
            f"<b>{m['manager']}</b> sits {m['real_rank']}th. On merit he's {m['apa_rank']}th. "
            f"Enjoy the fixtures while they last, you {w('sod')}."
        )))

    # ---- robbed by the fixtures ---------------------------------------
    robbed = [m for m in managers if m["luck"] >= 3]
    for m in sorted(robbed, key=lambda x: -x["luck"])[:1]:
        out.append(burn(60, "Merit", (
            f"<b>{m['manager']}</b> has the {m['apa_rank']}{'st' if m['apa_rank']==1 else 'nd' if m['apa_rank']==2 else 'rd' if m['apa_rank']==3 else 'th'} "
            f"best squad in the league and is {m['real_rank']}th. Football, eh."
        )))

    # ---- busy trader, nothing to show for it --------------------------
    for row in trade_leaderboard:
        moves = [x for x in row.get("Key Moves", "").split(",") if x.strip() and "None" not in x]
        net = num(row.get("Net", 0))
        if len(moves) >= 5 and abs(net) <= 1:
            out.append(burn(82, "Trades", (
                f"<b>{row.get('Manager','')}</b> has made {len(moves)} trades this season "
                f"for a net gain of {w('fuckall')}."
            )))
            break

    # ---- best trader, still nowhere -----------------------------------
    if trade_leaderboard:
        best_trader = trade_leaderboard[0]
        m = by_name.get(best_trader.get("Manager", ""))
        if m and num(best_trader.get("Net", 0)) > 0 and m["real_rank"] >= 6:
            out.append(burn(84, "Trades", (
                f"<b>{m['manager']}</b> is the best trader in the league and still "
                f"{m['real_rank']}th with {m['real_pts']} points. Winning the transfer "
                f"market, losing the football."
            )))

    # ---- lost the lot -------------------------------------------------
    winless = [m for m in managers if m["trophies"]["won"] == 0 and m["trophies"]["lost"] >= 2]
    if len(winless) >= 2:
        names = ", ".join(first_name(m["manager"]) for m in winless[:-1])
        out.append(burn(72, "Form", (
            f"<b>{names}</b> and {first_name(winless[-1]['manager'])} have not won a game "
            f"between them all season. Consistency of a sort."
        )))

    # ---- bottom of the scoring, repeatedly ----------------------------
    for m in managers:
        if m["trophies"]["bottomed"] >= 2:
            out.append(burn(86, "Form", (
                f"<b>{m['manager']}</b> has finished bottom of the scoring in "
                f"{m['trophies']['bottomed']} of {len(stats['finished_gameweeks'])} gameweeks. "
                f"A perfect record."
            )))

    # ---- benched a hatful and got away with it ------------------------
    bench_king = max(managers, key=lambda m: m["bench"])
    if bench_king["bench"] >= 10 and bench_king["form"] and bench_king["form"][-1] == "W":
        pick = bench_king["bench_detail"][0] if bench_king["bench_detail"] else None
        if pick:
            out.append(burn(66, "Bench", (
                f"<b>{bench_king['manager']}</b> benched {pick[0]} and his {pick[1]} points, "
                f"then won anyway. Jammy."
            )))

    # ---- the best player nobody could be bothered with ----------------
    fa_list = stats.get("free_agents", [])
    if fa_list and fa_list[0]["gw_points"] >= 8:
        f = fa_list[0]
        out.append(burn(58, "Waivers", (
            f"{f['name']} scored {f['gw_points']} from the waiver wire. All ten of you "
            f"looked at him and thought: nah."
        )))

    # ---- backhanded nod to the leader ---------------------------------
    if top["bench"] == 0:
        out.append(burn(55, "Top", (
            f"<b>{top['manager']}</b> leads the league and didn't waste a single point "
            f"on the bench. {w('bastard').capitalize()}."
        )))
    else:
        out.append(burn(50, "Top", (
            f"<b>{top['manager']}</b> tops the table on {top['real_pts']} points. "
            f"Make the most of it."
        )))

    # de-duplicate by manager so one person isn't battered eight times
    out.sort(key=lambda b: -b["severity"])
    seen, final = {}, []
    for b in out:
        who = re.search(r"<b>(.+?)</b>", b["text"])
        key = who.group(1) if who else b["tag"]
        if seen.get(key, 0) >= 2:
            continue
        seen[key] = seen.get(key, 0) + 1
        final.append(b)
    return final[:limit]
