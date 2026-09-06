"""
Generates the punditry line-up for the dashboard carousel.

Every burn is derived from the actual numbers -- nobody gets abused for
something that didn't happen. Scope is deliberately narrow: league position,
bad trades, bad gameweeks, benched points, fixture luck and where the
simulation says you're heading. Nothing personal, nothing outside the football.

The pool is much larger than the carousel has slots for. The two harshest
lines are pinned so the page always leads with the real story; the rest are
shuffled with a date-derived seed, so the mix changes every day but stays
stable within a day. Between gameweeks the result-based burns can't change --
the projections, trades and waiver lines are what keep it moving.

SPICE controls the language:
    "full"  -- pub-standard swearing (default; it's a mates' league)
    "mild"  -- same jokes, no profanity
Set it and re-run render_dashboard.py; nothing else needs changing.
"""

import datetime
import random
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
    "bothered": ("can't be arsed", "can't be bothered"),
    "grim": ("grim as sin", "thoroughly grim"),
    "clown": ("clown show", "shambles"),
    "hell": ("what the hell", "what on earth"),
    "bin": ("bin fire", "shambles"),
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


def player_of(cell):
    return re.sub(r"\s*\(.*", "", str(cell)).strip()


def ordinal(n):
    n = int(n)
    if 11 <= n % 100 <= 13:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def burn(severity, tag, text):
    return {"severity": severity, "tag": tag, "text": text}


def moves_of(row):
    raw = row.get("Key Moves", "")
    return [x.strip() for x in raw.split(",") if x.strip() and "None" not in x]


# ----------------------------------------------------------------- burns

def table_burns(stats, managers, out):
    gw = stats["gameweek"]
    ranked = sorted(managers, key=lambda m: m["real_rank"])
    top, bottom = ranked[0], ranked[-1]
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

    # scored plenty, still nowhere -- points for vs actual position
    for m in managers:
        better_scorers = sum(1 for o in managers if o["pts_for"] > m["pts_for"])
        if m["real_rank"] - (better_scorers + 1) >= 3:
            out.append(burn(74, "Table", (
                f"<b>{m['manager']}</b> has the {ordinal(better_scorers + 1)}-highest points "
                f"total in the league and sits {ordinal(m['real_rank'])}. Somebody's having a laugh."
            )))
            break

    # the gulf at the top
    spread = top["pts_for"] - bottom["pts_for"]
    if spread >= 40:
        out.append(burn(68, "Table", (
            f"{first_name(top['manager'])} has outscored <b>{bottom['manager']}</b> by "
            f"{spread} points. That's not a gap, that's a different sport."
        )))

    # beaten by someone below you on points
    for i in range(len(ranked) - 1):
        above, below = ranked[i], ranked[i + 1]
        if below["pts_for"] - above["pts_for"] >= 20:
            out.append(burn(64, "Table", (
                f"<b>{above['manager']}</b> sits above {below['manager']} in the table "
                f"having scored {below['pts_for'] - above['pts_for']} fewer points. "
                f"Enjoy it while it lasts."
            )))
            break

    if len(stats["finished_gameweeks"]) >= 2 and top["trophies"]["lost"] == 0:
        out.append(burn(52, "Top", (
            f"<b>{top['manager']}</b> hasn't lost yet. Someone do something about it, this is "
            f"getting {w('grim')}."
        )))


def scoring_burns(stats, managers, out):
    gw = stats["gameweek"]
    scores = sorted(managers, key=lambda m: m["xi"], reverse=True)
    best, worst = scores[0], scores[-1]
    avg = sum(m["xi"] for m in managers) / len(managers)

    out.append(burn(76, "Scores", (
        f"<b>{worst['manager']}</b> managed {worst['xi']} points in GW{gw}. The league average "
        f"was {avg:.0f}. Eleven players, {worst['xi']} points."
    )))

    if best["xi"] - worst["xi"] >= 20:
        out.append(burn(62, "Scores", (
            f"{best['xi']} plays {worst['xi']}. That's the range in one gameweek, and "
            f"<b>{worst['manager']}</b> is the wrong end of it."
        )))

    # a losing score that would have beaten most people
    for r in stats["results"]:
        loser_pts = min(r["home_pts"], r["away_pts"])
        loser = r["away"] if r["winner"] == "home" else (r["home"] if r["winner"] == "away" else None)
        if not loser:
            continue
        beaten = sum(1 for m in managers if m["xi"] < loser_pts)
        if beaten >= 6:
            out.append(burn(72, "Scores", (
                f"<b>{loser}</b> scored {loser_pts}, enough to beat {beaten} of the other nine, "
                f"and drew the one man who turned up. Rotten luck."
            )))
            break

    # a winning score that would have lost to nearly everyone
    for r in stats["results"]:
        win_pts = max(r["home_pts"], r["away_pts"])
        winner = r["home"] if r["winner"] == "home" else (r["away"] if r["winner"] == "away" else None)
        if not winner:
            continue
        lost_to = sum(1 for m in managers if m["xi"] > win_pts)
        if lost_to >= 5:
            out.append(burn(66, "Scores", (
                f"<b>{winner}</b> won with {win_pts}, a score that would have lost to "
                f"{lost_to} other managers. Fixtures are a wonderful thing."
            )))
            break

    if stats["results"]:
        tight = min(stats["results"], key=lambda r: r["margin"] if r["winner"] != "draw" else 999)
        if tight["winner"] != "draw" and tight["margin"] <= 3:
            loser = tight["away"] if tight["winner"] == "home" else tight["home"]
            out.append(burn(69, "Results", (
                f"<b>{loser}</b> lost by {tight['margin']}. One substitution, one different hunch, "
                f"one anything. Sleep well."
            )))


def bench_burns(stats, managers, out):
    gw = stats["gameweek"]
    by_name = {m["manager"]: m for m in managers}
    total_bench = sum(m["bench"] for m in managers)

    for r in stats["results"]:
        loser = (r["away"] if r["winner"] == "home"
                 else r["home"] if r["winner"] == "away" else None)
        if not loser:
            continue
        m = by_name.get(loser)
        if m and m["bench"] > r["margin"]:
            pick = m["bench_detail"][0] if m["bench_detail"] else None
            tail = f" {pick[0]} alone had {pick[1]}." if pick and pick[1] else ""
            out.append(burn(92, "Bench", (
                f"<b>{m['manager']}</b> left {m['bench']} points on his {w('arse')} "
                f"and lost by {r['margin']}.{tail} {w('state').capitalize()}."
            )))

    if total_bench >= 40:
        out.append(burn(56, "Bench", (
            f"The league benched {total_bench} points in GW{gw}. Ten managers, one job, "
            f"and {total_bench} points watched from the side."
        )))

    # a benched player who outscored the manager's own worst starter
    worst_bench = max(managers, key=lambda m: m["bench"])
    if worst_bench["bench_detail"]:
        pick = worst_bench["bench_detail"][0]
        if pick[1] >= 8:
            out.append(burn(79, "Bench", (
                f"<b>{worst_bench['manager']}</b> looked at {pick[0]}, who went on to score "
                f"{pick[1]}, and thought: no, you sit this one out."
            )))

    king = worst_bench
    if king["bench"] >= 10 and king["form"] and king["form"][-1] == "W":
        pick = king["bench_detail"][0] if king["bench_detail"] else None
        if pick:
            out.append(burn(66, "Bench", (
                f"<b>{king['manager']}</b> benched {pick[0]} and his {pick[1]} points, "
                f"then won anyway. Jammy."
            )))

    clean = [m for m in managers if m["bench"] == 0]
    if clean and len(clean) <= 2:
        out.append(burn(48, "Bench", (
            f"<b>{clean[0]['manager']}</b> benched nothing at all. No sob story available. "
            f"Deeply irritating."
        )))


def trade_burns(stats, managers, leaderboard, all_trades, out):
    gw = stats["gameweek"]
    by_name = {m["manager"]: m for m in managers}
    free_agents = {f["name"]: f for f in stats.get("free_agents", [])}

    if all_trades:
        worst = min(all_trades, key=lambda t: num(t.get("Net", 0)))
        if num(worst.get("Net", 0)) <= -4:
            out.append(burn(90, "Trades", (
                f"<b>{worst.get('Manager','')}</b> binned {player_of(worst.get('Out (pts since)',''))}, "
                f"watched him rack up {pts_in(worst.get('Out (pts since)',''))}, and got "
                f"{player_of(worst.get('In (pts since)',''))}'s grand total of "
                f"{pts_in(worst.get('In (pts since)',''))} back. Inspired."
            )))

        best = max(all_trades, key=lambda t: num(t.get("Net", 0)))
        if num(best.get("Net", 0)) >= 4:
            out.append(burn(46, "Trades", (
                f"<b>{best.get('Manager','')}</b> got {player_of(best.get('In (pts since)',''))} for "
                f"{player_of(best.get('Out (pts since)',''))} and is {int(num(best.get('Net',0)))} up on it. "
                f"One good idea. Savour it."
            )))

        # dropped a player who is still unowned and still scoring
        for t in all_trades:
            gone = player_of(t.get("Out (pts since)", ""))
            fa = free_agents.get(gone)
            if fa and fa["gw_points"] >= 6 and num(t.get("Net", 0)) < 0:
                out.append(burn(88, "Trades", (
                    f"<b>{t.get('Manager','')}</b> dropped {gone}, who put up {fa['gw_points']} "
                    f"in GW{gw} while sat on the waiver wire, owned by nobody. Still there, by the way."
                )))
                break

        # deals that were bad the day they were made and still are
        old_stinkers = [t for t in all_trades
                        if num(t.get("Net", 0)) <= -5 and num(t.get("Since", "99")) <= gw - 1]
        if old_stinkers:
            t = min(old_stinkers, key=lambda x: num(x.get("Net", 0)))
            out.append(burn(75, "Trades", (
                f"<b>{t.get('Manager','')}</b> made that {player_of(t.get('In (pts since)',''))} deal back "
                f"in GW{int(num(t.get('Since', 0)))} and it's still {int(num(t.get('Net',0)))}. "
                f"Some mistakes just keep giving."
            )))

    for row in leaderboard:
        moves, net = moves_of(row), num(row.get("Net", 0))
        name = row.get("Manager", "")
        m = by_name.get(name)

        if len(moves) >= 5 and abs(net) <= 1:
            out.append(burn(82, "Trades", (
                f"<b>{name}</b> has made {len(moves)} trades this season for a net gain of {w('fuckall')}."
            )))
        if len(moves) >= 4 and net <= -5:
            out.append(burn(86, "Trades", (
                f"<b>{name}</b> has made {len(moves)} trades and is {int(net)} points worse off. "
                f"Every single one an act of self-harm."
            )))
        if not moves and m:
            if m["real_rank"] <= 4:
                out.append(burn(50, "Trades", (
                    f"<b>{name}</b> hasn't made a single trade and is {ordinal(m['real_rank'])}. "
                    f"The rest of you are just making work for yourselves."
                )))
            elif m["real_rank"] >= 8:
                out.append(burn(80, "Trades", (
                    f"<b>{name}</b> is {ordinal(m['real_rank'])} and hasn't made one trade. "
                    f"Not a bad squad, just {w('bothered')}."
                )))

    if leaderboard:
        best_trader = leaderboard[0]
        m = by_name.get(best_trader.get("Manager", ""))
        if m and num(best_trader.get("Net", 0)) > 0 and m["real_rank"] >= 6:
            out.append(burn(84, "Trades", (
                f"<b>{m['manager']}</b> is the best trader in the league and still "
                f"{ordinal(m['real_rank'])} with {m['real_pts']} points. Winning the transfer "
                f"market, losing the football."
            )))


def pending_burns(stats, pending, managers, out):
    if not pending:
        return
    gw = stats["gameweek"]
    by_name = {m["manager"]: m for m in managers}
    counts = {}
    for r in pending:
        counts[r.get("Manager", "")] = counts.get(r.get("Manager", ""), 0) + 1
    busiest, n = max(counts.items(), key=lambda kv: kv[1])
    m = by_name.get(busiest)

    if n >= 3:
        where = f", and he's still {ordinal(m['real_rank'])}" if m else ""
        out.append(burn(73, "Waivers", (
            f"<b>{busiest}</b> has {n} moves landing for GW{gw + 1}{where}. "
            f"That's not a strategy, that's panic."
        )))
    if len(pending) >= 10:
        out.append(burn(54, "Waivers", (
            f"{len(pending)} squad changes go through for GW{gw + 1}. Half this league has "
            f"looked at its own team and recoiled."
        )))


def waiver_burns(stats, managers, out):
    gw = stats["gameweek"]
    fa_list = stats.get("free_agents", [])
    if not fa_list:
        return
    f = fa_list[0]
    if f["gw_points"] >= 8:
        out.append(burn(58, "Waivers", (
            f"{f['name']} scored {f['gw_points']} from the waiver wire. All ten of you "
            f"looked at him and thought: nah."
        )))

    # one free player against a whole starting XI -- the only comparison the
    # stored numbers actually support, and the one that stings
    worst = min(managers, key=lambda m: m["xi"])
    if f["gw_points"] >= 6 and worst["xi"] < f["gw_points"] * 5:
        out.append(burn(71, "Waivers", (
            f"One unowned {f['position']} scored {f['gw_points']}. Eleven of "
            f"<b>{worst['manager']}</b>'s men managed {worst['xi']} between them."
        )))


def form_burns(stats, managers, out):
    played = len(stats["finished_gameweeks"])
    winless = [m for m in managers if m["trophies"]["won"] == 0 and m["trophies"]["lost"] >= 2]
    perfect = [m for m in managers if m["trophies"]["lost"] == 0 and m["trophies"]["won"] >= 2]

    if len(winless) >= 2:
        names = ", ".join(first_name(m["manager"]) for m in winless[:-1])
        out.append(burn(72, "Form", (
            f"<b>{names}</b> and {first_name(winless[-1]['manager'])} have not won a game "
            f"between them all season. Consistency of a sort."
        )))
    elif winless:
        out.append(burn(77, "Form", (
            f"<b>{winless[0]['manager']}</b> has played {played} and won none. "
            f"At some point this stops being bad luck."
        )))

    for m in managers:
        if m["trophies"]["bottomed"] >= 2:
            out.append(burn(86, "Form", (
                f"<b>{m['manager']}</b> has finished bottom of the scoring in "
                f"{m['trophies']['bottomed']} of {played} gameweeks. A perfect record."
            )))
        if m["trophies"]["robbed"] >= 2:
            out.append(burn(67, "Form", (
                f"<b>{m['manager']}</b> has been the highest-scoring loser {m['trophies']['robbed']} "
                f"times. The league is being run by someone who hates him."
            )))
        if m["trophies"]["drew"] >= 2:
            out.append(burn(53, "Form", (
                f"<b>{m['manager']}</b> has drawn {m['trophies']['drew']} of {played}. "
                f"Not winning, not losing, just there."
            )))

    never_topped = [m for m in managers if m["trophies"]["topped"] == 0 and played >= 2]
    if len(never_topped) >= 8:
        best_of_them = max(never_topped, key=lambda m: m["real_rank"])
        out.append(burn(44, "Form", (
            f"<b>{best_of_them['manager']}</b> has never once been the week's top scorer. "
            f"Neither have most of you, to be fair."
        )))

    if perfect and len(perfect) == 1:
        out.append(burn(47, "Top", (
            f"<b>{perfect[0]['manager']}</b> is unbeaten. Everyone else has managed to lose "
            f"to someone. Sort it out."
        )))


def merit_burns(stats, managers, out):
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

    flattered = sorted([m for m in managers if m["luck"] <= -3], key=lambda x: x["luck"])
    if flattered:
        m = flattered[0]
        out.append(burn(78, "Merit", (
            f"<b>{m['manager']}</b> sits {ordinal(m['real_rank'])}. On merit he's "
            f"{ordinal(m['apa_rank'])}. Enjoy the fixtures while they last, you {w('sod')}."
        )))

    robbed = sorted([m for m in managers if m["luck"] >= 3], key=lambda x: -x["luck"])
    if robbed:
        m = robbed[0]
        out.append(burn(60, "Merit", (
            f"<b>{m['manager']}</b> has the {ordinal(m['apa_rank'])} best squad in the league "
            f"and is {ordinal(m['real_rank'])}. Football, eh."
        )))

    # top on merit but not on the table
    apa_top = min(managers, key=lambda m: m["apa_rank"])
    if apa_top["real_rank"] != 1:
        leader = min(managers, key=lambda m: m["real_rank"])
        out.append(burn(63, "Merit", (
            f"<b>{apa_top['manager']}</b> would be top if the fixtures were fair. "
            f"They aren't, so {first_name(leader['manager'])} is."
        )))


def projection_burns(stats, managers, projection, predictions, out):
    by_name = {m["manager"]: m for m in managers}
    gw = stats["gameweek"]

    for row in projection or []:
        name = row.get("Manager", "")
        bottom3 = num(row.get("Bottom 3", 0))
        top3 = num(row.get("Top 3", 0))
        chance = num(row.get("Chance", 0))
        finish = num(row.get("Most Likely Finish", 0))
        m = by_name.get(name)

        if bottom3 >= 95:
            out.append(burn(89, "Sim", (
                f"The simulation ran 5,000 seasons. <b>{name}</b> finished bottom three in "
                f"{bottom3:.0f}% of them. Not most. Nearly all."
            )))
        elif bottom3 >= 70:
            out.append(burn(65, "Sim", (
                f"<b>{name}</b> has a {bottom3:.0f}% chance of finishing bottom three. "
                f"The computer has seen enough."
            )))
        if top3 == 0 and m and m["real_rank"] >= 7:
            out.append(burn(76, "Sim", (
                f"<b>{name}</b>'s chance of a top-three finish is {top3:.0f}%. Not slim. Zero. "
                f"In five thousand attempts it never once happened."
            )))
        if chance >= 80 and finish >= 9:
            out.append(burn(81, "Sim", (
                f"The model is {chance:.0f}% sure <b>{name}</b> finishes {ordinal(finish)}. "
                f"It is rarely that confident about anything."
            )))

    for c in predictions or []:
        if len(c) < 7:
            continue
        home, hproj, hwin, draw, aproj, away, awin = c[:7]
        hw, aw = num(hwin), num(awin)
        if hw <= 22:
            out.append(burn(57, "Next", (
                f"<b>{home}</b> is given a {hw:.0f}% chance in GW{gw + 1}. "
                f"Might be worth not watching."
            )))
        if aw <= 22:
            out.append(burn(57, "Next", (
                f"<b>{away}</b> is a {aw:.0f}% shot this week. The model has looked at that "
                f"squad and quietly closed the laptop."
            )))


def top_burns(stats, managers, out):
    top = min(managers, key=lambda m: m["real_rank"])
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


# ----------------------------------------------------------------- select

def generate(stats, trade_leaderboard, all_trades, pending=None,
             projection=None, predictions=None, limit=10, seed=None):
    """Best `limit` burns: two harshest pinned, the rest rotated by day."""
    managers = stats["managers"]
    pool = []

    table_burns(stats, managers, pool)
    scoring_burns(stats, managers, pool)
    bench_burns(stats, managers, pool)
    trade_burns(stats, managers, trade_leaderboard, all_trades, pool)
    pending_burns(stats, pending or [], managers, pool)
    waiver_burns(stats, managers, pool)
    form_burns(stats, managers, pool)
    merit_burns(stats, managers, pool)
    projection_burns(stats, managers, projection, predictions, pool)
    top_burns(stats, managers, pool)

    # drop accidental duplicates before anything else
    seen_text, unique = set(), []
    for b in pool:
        key = re.sub(r"<[^>]+>", "", b["text"])
        if key in seen_text:
            continue
        seen_text.add(key)
        unique.append(b)

    unique.sort(key=lambda b: -b["severity"])
    if seed is None:
        seed = datetime.date.today().toordinal()
    rest = unique[2:]
    random.Random(seed).shuffle(rest)
    ordered = unique[:2] + rest

    # cap each manager so one person isn't battered ten slides running
    picked, per_manager = [], {}
    for b in ordered:
        who = re.search(r"<b>(.+?)</b>", b["text"])
        key = who.group(1) if who else b["tag"]
        if per_manager.get(key, 0) >= 2:
            continue
        per_manager[key] = per_manager.get(key, 0) + 1
        picked.append(b)
        if len(picked) >= limit:
            break

    picked.sort(key=lambda b: -b["severity"])
    return picked
