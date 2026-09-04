"""
Renders the Sharnbrook Wire dashboard to a single self-contained HTML file.

Reads everything the other pipeline scripts already wrote into seasons/<season>/
(dashboard_stats.json plus the markdown recaps) and emits site/index.html with
fonts inlined as base64 so it works under the Artifact CSP.

This exists so the weekly publishing routine renders the design by running a
script rather than re-describing it in prose every week -- the layout, colours
and interactions live here, in version control, and only the data changes.

Usage:  python3 render_dashboard.py
"""

import base64
import json
import os
import re
import urllib.request

import burns

SEASON = "2026-27"
SEASON_DIR = os.path.join("seasons", SEASON)
OUT_DIR = "site"
OUT_FILE = os.path.join(OUT_DIR, "index.html")
FONT_CACHE = os.path.join(OUT_DIR, ".fonts")
ME = "Greg Woodward"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

FONT_SPECS = [
    ("archivoblack_400", "Archivo+Black", "Archivo Black", "400"),
    ("bebas_400", "Bebas+Neue", "Bebas Neue", "400"),
    ("barlow_400", "Barlow:wght@400;600;700", "Barlow", "400"),
    ("barlow_600", "Barlow:wght@400;600;700", "Barlow", "600"),
    ("barlow_700", "Barlow:wght@400;600;700", "Barlow", "700"),
]


# ---------------------------------------------------------------- helpers

def esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;"))


def read(path):
    full = os.path.join(SEASON_DIR, path)
    if not os.path.exists(full):
        return ""
    with open(full) as f:
        return f.read()


def latest(subdir, pattern):
    d = os.path.join(SEASON_DIR, subdir)
    if not os.path.isdir(d):
        return ""
    files = [f for f in os.listdir(d) if re.match(pattern, f)]
    if not files:
        return ""
    files.sort(key=lambda f: int(re.search(r"GW(\d+)", f).group(1)))
    with open(os.path.join(d, files[-1])) as f:
        return f.read()


def tables(md):
    """Every markdown table in a document, as {heading: [row dicts]}."""
    out, heading, header, rows = {}, "", None, []

    def flush():
        if header and rows:
            out.setdefault(heading, []).extend(rows)

    for line in md.splitlines():
        if line.startswith("#"):
            flush()
            header, rows = None, []
            heading = line.lstrip("#").strip()
            continue
        if line.strip().startswith("|"):
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if set("".join(cells)) <= set("-: "):
                continue
            if header is None:
                header = cells
            else:
                rows.append(dict(zip(header, cells)))
        elif header and rows:
            flush()
            header, rows = None, []
    flush()
    return out


def find_table(md, *heading_hints):
    """First table whose heading contains any hint (case-insensitive)."""
    tabs = tables(md)
    for hint in heading_hints:
        for heading, rows in tabs.items():
            if hint.lower() in heading.lower():
                return rows
    for rows in tabs.values():
        return rows
    return []


def find_rows(md):
    """First table as raw cell lists -- needed where headers repeat (Proj/Win%)."""
    rows, header = [], None
    for line in md.splitlines():
        if line.strip().startswith("|"):
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if set("".join(cells)) <= set("-: "):
                continue
            if header is None:
                header = cells
            else:
                rows.append(cells)
        elif rows:
            break
    return rows


def hero_stats(md):
    """The three ## Hero Stats lines, already computed by weekly_summary.py."""
    out = {}
    for line in md.splitlines():
        m = re.match(r"\*\*(.+?):\*\*\s*(.+)", line.strip())
        if m:
            out[m.group(1).strip().lower()] = m.group(2).strip()
    return out


def lede(md, after=None):
    """First italic _..._ paragraph, optionally after a given heading."""
    text = md
    if after:
        idx = md.find(after)
        if idx >= 0:
            text = md[idx:]
    m = re.search(r"^_(.+?)_$", text, re.M | re.S)
    return m.group(1).strip().replace("\n", " ") if m else ""


def num(s, default=0.0):
    m = re.search(r"-?\d+(?:\.\d+)?", str(s).replace("−", "-"))
    return float(m.group()) if m else default


def sign(v):
    return f"+{v}" if v > 0 else ("−" + str(abs(v)) if v < 0 else "0")


def ordinal(n):
    if 11 <= n % 100 <= 13:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def pct(s):
    return num(s)


# ---------------------------------------------------------------- fonts

def font_faces():
    os.makedirs(FONT_CACHE, exist_ok=True)
    css, cache = [], {}
    for key, family, fam_name, weight in FONT_SPECS:
        path = os.path.join(FONT_CACHE, key + ".b64")
        if not os.path.exists(path):
            try:
                if family not in cache:
                    url = f"https://fonts.googleapis.com/css2?family={family}&display=swap"
                    req = urllib.request.Request(url, headers={"User-Agent": UA})
                    cache[family] = urllib.request.urlopen(req, timeout=20).read().decode()
                block = None
                for b in re.split(r"\n(?=/\*)", cache[family]):
                    if "/* latin */" not in b or f"font-family: '{fam_name}'" not in b:
                        continue
                    w = re.search(r"font-weight: (\d+)", b)
                    if w and w.group(1) != weight:
                        continue
                    block = b
                    break
                src = re.search(r"url\((https://[^)]+)\)", block).group(1)
                data = urllib.request.urlopen(src, timeout=20).read()
                with open(path, "w") as f:
                    f.write(base64.b64encode(data).decode())
            except Exception as e:  # noqa: BLE001 - fall back to system fonts
                print(f"  ! font {key} unavailable ({e}); falling back")
                continue
        with open(path) as f:
            b64 = f.read()
        css.append(
            "@font-face{font-family:'%s';font-style:normal;font-weight:%s;"
            "font-display:swap;src:url(data:font/woff2;base64,%s) format('woff2')}"
            % (fam_name, weight, b64)
        )
    return "\n".join(css)


# ---------------------------------------------------------------- markup

def form_pills(form):
    return "".join(
        f'<i class="fp fp-{r.lower()}" title="{"Win" if r=="W" else "Loss" if r=="L" else "Draw"}">{r}</i>'
        for r in form
    )


def drawer(label, count_note, inner, open_=False):
    return (
        f'<details class="drawer"{" open" if open_ else ""}>'
        f'<summary class="drawer-btn"><span class="drawer-label">{esc(label)}</span>'
        f'<span class="drawer-note">{esc(count_note)}</span>'
        f'<span class="chev" aria-hidden="true"></span></summary>'
        f'<div class="drawer-body">{inner}</div></details>'
    )


def table_html(headers, rows, aligns=None, me_col=None):
    aligns = aligns or [""] * len(headers)
    head = "".join(
        f'<th class="{a}">{esc(h)}</th>' for h, a in zip(headers, aligns)
    )
    body = []
    for r in rows:
        cls = ' class="me"' if me_col is not None and ME in str(r[me_col]) else ""
        cells = "".join(
            f'<td class="{a}">{c}</td>' for c, a in zip(r["cells"], aligns)
        )
        body.append(f"<tr{cls}>{cells}</tr>")
    return (
        f'<div class="scroll"><table><thead><tr>{head}</tr></thead>'
        f'<tbody>{"".join(body)}</tbody></table></div>'
    )


def build(stats, summary_md, power_md, trade_md, pred_md, proj_md, lineup_md):
    gw = stats["gameweek"]
    managers = stats["managers"]
    by_name = {m["manager"]: m for m in managers}
    heroes = hero_stats(summary_md)
    out = []

    # ---------- top bar ----------
    out.append(
        '<header class="topbar"><div class="wrap topbar-in">'
        '<a class="brand" href="#top"><span class="brand-mark"></span>'
        '<span class="brand-text">The Sharnbrook Wire</span></a>'
        '<nav class="jump">'
        '<a href="#results">Results</a><a href="#table">Table</a>'
        '<a href="#bench">Bench</a><a href="#truth">Truth</a>'
        '<a href="#trades">Trades</a><a href="#ahead">Ahead</a></nav>'
        f'<span class="gw-chip">GW{gw:02d}</span>'
        "</div></header>"
    )

    # ---------- hero ----------
    top_line = heroes.get("top of the wire", "")
    robbed_line = heroes.get("robbed blind", "")
    bottom_line = heroes.get("propping up the table", "")

    bench_sorted = sorted(managers, key=lambda m: m["bench"], reverse=True)
    worst_bench = bench_sorted[0]
    tied = [m["manager"] for m in bench_sorted if m["bench"] == worst_bench["bench"]]
    # Surnames keep the caption on one line when several managers tie.
    bench_label = (tied[0] if len(tied) == 1
                   else " &amp; ".join(esc(n.split()[-1]) for n in tied))

    # The verdict carousel -- every line generated from the actual numbers.
    verdicts = burns.generate(
        stats,
        find_table(trade_md, "Manager Leaderboard"),
        find_table(trade_md, "Every Trade"),
    )
    slides, dots = [], []
    for i, b in enumerate(verdicts):
        on = " is-active" if i == 0 else ""
        slides.append(
            f'<p class="slide{on}" aria-hidden="{"false" if i == 0 else "true"}">'
            f'<span class="slide-tag">{esc(b["tag"])}</span>{b["text"]}</p>'
        )
        dots.append(
            f'<button type="button" class="vdot{" on" if i == 0 else ""}" role="tab" '
            f'aria-label="Verdict {i + 1} of {len(verdicts)}" '
            f'aria-selected="{"true" if i == 0 else "false"}"></button>'
        )

    verdict = (
        '<div class="verdict" data-carousel role="region" aria-roledescription="carousel" '
        'aria-label="The verdict">'
        '<div class="verdict-top"><span class="verdict-tag">The Verdict</span>'
        '<span class="verdict-sub">No one is safe</span></div>'
        f'<div class="slides">{"".join(slides)}</div>'
        '<div class="verdict-ctl">'
        '<button type="button" class="cbtn" data-prev aria-label="Previous verdict">'
        '<span aria-hidden="true">&lsaquo;</span></button>'
        f'<div class="dots" role="tablist">{"".join(dots)}</div>'
        '<button type="button" class="cbtn" data-next aria-label="Next verdict">'
        '<span aria-hidden="true">&rsaquo;</span></button>'
        '<i class="tick" aria-hidden="true"></i></div></div>'
    ) if verdicts else ""

    out.append(
        '<section class="hero" id="top"><div class="floodlight" aria-hidden="true"></div>'
        '<div class="wrap hero-in">'
        '<div class="hero-meta"><span class="live"><i></i>Full time</span>'
        f'<span class="sep"></span><span>Fantasy Draft &middot; {SEASON} Season</span></div>'
        f'<h1 class="hero-title"><span class="gw-word">Gameweek</span>'
        f'<span class="gw-num" data-count="{gw}">{gw:02d}</span></h1>'
        f'{verdict}'
        "</div></section>"
    )

    # ---------- KPI cards ----------
    def kpi(label, value, caption, tone):
        return (
            f'<article class="kpi kpi-{tone}"><span class="kpi-label">{esc(label)}</span>'
            f'<span class="kpi-num" data-count="{value}">{value}</span>'
            f'<span class="kpi-cap">{caption}</span></article>'
        )

    top_m = max(managers, key=lambda m: m["xi"])
    bottom_m = min(managers, key=lambda m: m["xi"])
    robbed_name = robbed_line.split("—")[0].strip() if robbed_line else ""
    robbed_m = by_name.get(robbed_name.replace(" & ", "").strip())
    if robbed_m is None:
        losers = [m for m in managers if m["form"] and m["form"][-1] == "L"]
        robbed_m = max(losers, key=lambda m: m["xi"]) if losers else bottom_m

    out.append(
        '<section class="kpis"><div class="wrap kpi-grid">'
        + kpi("Top of the wire", top_m["xi"],
              f'<b>{esc(top_m["manager"])}</b><span>{esc(top_m["team_name"])}</span>', "good")
        + kpi("Robbed blind", robbed_m["xi"],
              f'<b>{esc(robbed_m["manager"])}</b><span>scored big, lost anyway</span>', "bad")
        + kpi("Left on the bench", worst_bench["bench"],
              f'<b>{bench_label if len(tied) > 1 else esc(bench_label)}</b>'
              f'<span>points that never played</span>', "warn")
        + kpi("Propping up the table", bottom_m["xi"],
              f'<b>{esc(bottom_m["manager"])}</b><span>{esc(bottom_m["team_name"])}</span>', "dim")
        + "</div></section>"
    )

    # ---------- results ----------
    cards = []
    for r in stats["results"]:
        hw = "win" if r["winner"] == "home" else ("loss" if r["winner"] == "away" else "draw")
        aw = "win" if r["winner"] == "away" else ("loss" if r["winner"] == "home" else "draw")
        note = "Drawn" if r["winner"] == "draw" else f"by {r['margin']}"
        cards.append(
            f'<article class="fixture">'
            f'<div class="side {hw}{" me" if ME in r["home"] else ""}">'
            f'<span class="side-name">{esc(r["home"])}</span>'
            f'<span class="side-team">{esc(r["home_team"])}</span></div>'
            f'<div class="score"><span class="s {hw}">{r["home_pts"]}</span>'
            f'<span class="dash"></span><span class="s {aw}">{r["away_pts"]}</span>'
            f'<span class="margin">{note}</span></div>'
            f'<div class="side right {aw}{" me" if ME in r["away"] else ""}">'
            f'<span class="side-name">{esc(r["away"])}</span>'
            f'<span class="side-team">{esc(r["away_team"])}</span></div>'
            f"</article>"
        )
    out.append(
        f'<section class="band" id="results"><div class="wrap">'
        f'<div class="head"><h2>GW{gw} Results</h2>'
        f'<span class="head-note">Head to head</span></div>'
        f'<div class="fixtures">{"".join(cards)}</div></div></section>'
    )

    # ---------- standings + scores ----------
    st_rows = find_table(summary_md, "Standings")
    rows = []
    for r in st_rows:
        name = r.get("Manager", "")
        m = by_name.get(name)
        move = r.get("Move", "=")
        tone = "good" if "UP" in move else ("bad" if "DOWN" in move else "flat")
        rows.append({"cells": [
            f'<span class="pos">{esc(r.get("Rank",""))}</span>',
            f'<b>{esc(name)}</b><span class="sub">{esc(r.get("Team",""))}</span>',
            f'<span class="movement {tone}">{esc(move)}</span>',
            f'<span class="formline">{form_pills(m["form"]) if m else ""}</span>',
            f'<b>{esc(r.get("Pts",""))}</b>',
            esc(r.get("For", "")),
        ], "Manager": name})
    standings = table_html(
        ["#", "Manager", "Move", "Form", "Pts", "For"], rows,
        ["", "", "", "", "num", "num"], me_col="Manager",
    )

    score_rows = find_table(summary_md, "Scores")
    top_score = max((num(r.get("Points", 0)) for r in score_rows), default=1) or 1
    bars = []
    for r in score_rows:
        v = num(r.get("Points", 0))
        w = v / top_score * 100
        me = " me" if ME in r.get("Manager", "") else ""
        bars.append(
            f'<div class="bar-row{me}"><span class="bar-name">{esc(r.get("Manager",""))}</span>'
            f'<span class="bar-track"><i class="bar-fill" style="--w:{w:.1f}%"></i></span>'
            f'<span class="bar-val">{int(v)}</span></div>'
        )

    out.append(
        f'<section class="band" id="table"><div class="wrap grid-2">'
        f'<div class="panel"><div class="head"><h2>Table</h2>'
        f'<span class="head-note">After GW{gw}</span></div>{standings}</div>'
        f'<div class="panel"><div class="head"><h2>GW{gw} Scores</h2>'
        f'<span class="head-note">Starting XI</span></div>'
        f'<div class="bars">{"".join(bars)}</div></div>'
        f"</div></section>"
    )

    # ---------- bench ----------
    bench_bars = []
    bench_max = max((m["bench"] for m in managers), default=1) or 1
    for m in bench_sorted:
        worst = m["bench_detail"][0] if m["bench_detail"] else ("--", 0)
        me = " me" if ME in m["manager"] else ""
        bench_bars.append(
            f'<div class="bench-row{me}"><span class="bench-name">{esc(m["manager"])}</span>'
            f'<span class="bar-track warn"><i class="bar-fill" '
            f'style="--w:{m["bench"] / bench_max * 100:.1f}%"></i></span>'
            f'<span class="bench-val">{m["bench"]}</span>'
            f'<span class="bench-worst">{esc(worst[0])} <b>{worst[1]}</b></span></div>'
        )
    out.append(
        '<section class="band" id="bench"><div class="wrap"><div class="head">'
        '<h2>Left On The Bench</h2><span class="head-note">Points that never played</span></div>'
        '<p class="lede">Every point your bench scored is a point you chose not to have. '
        'The right-hand name is the single worst call of the week.</p>'
        f'<div class="bench">{"".join(bench_bars)}</div></div></section>'
    )

    # ---------- all-play-all ----------
    apa_rows = sorted(managers, key=lambda m: m["apa_rank"])
    slope = []
    for m in apa_rows:
        swing = m["luck"]
        tone = "good" if swing > 0 else ("bad" if swing < 0 else "flat")
        word = "unlucky" if swing > 0 else ("flattered" if swing < 0 else "fair")
        me = " me" if ME in m["manager"] else ""
        w, d, l = m["apa_record"]
        slope.append(
            f'<div class="slope-row{me}">'
            f'<span class="slope-rank">{m["apa_rank"]}</span>'
            f'<span class="slope-name"><b>{esc(m["manager"])}</b>'
            f'<span class="sub">{w}-{d}-{l} &middot; {m["apa_pts"]} pts</span></span>'
            f'<span class="slope-line {tone}" style="--from:{m["apa_rank"]};--to:{m["real_rank"]}">'
            f'<i class="dot a"></i><i class="dot b"></i></span>'
            f'<span class="slope-real">{m["real_rank"]}</span>'
            f'<span class="swing {tone}">{sign(swing)} <em>{word}</em></span>'
            f"</div>"
        )
    out.append(
        '<section class="band" id="truth"><div class="wrap"><div class="head">'
        '<h2>The Truth Table</h2><span class="head-note">All-play-all</span></div>'
        '<p class="lede">Every manager scored against all nine others, every week &mdash; '
        'fixture luck removed. Left column is where you\'d be; right column is where you '
        'actually are.</p>'
        '<div class="slope-head"><span>Deserved</span><span>Actual</span></div>'
        f'<div class="slope">{"".join(slope)}</div></div></section>'
    )

    # ---------- trades ----------
    tm = re.search(r"\*\*Trade Master:\s*(.+?)\*\*\s*\((.+?)\)", trade_md)
    badge = (
        f'<div class="badge"><span class="badge-label">Trade Master</span>'
        f'<span class="badge-name">{esc(tm.group(1))}</span>'
        f'<span class="badge-val">{esc(tm.group(2))}</span></div>' if tm else ""
    )
    lb = find_table(trade_md, "Manager Leaderboard")
    max_abs = max((abs(num(r.get("Net", 0))) for r in lb), default=1) or 1
    tor = []
    for r in lb:
        v = num(r.get("Net", 0))
        half = abs(v) / max_abs * 50
        tone = "good" if v > 0 else ("bad" if v < 0 else "flat")
        side = "pos" if v > 0 else ("neg" if v < 0 else "zero")
        me = " me" if ME in r.get("Manager", "") else ""
        tor.append(
            f'<div class="tor-row{me}"><span class="tor-name">{esc(r.get("Manager",""))}</span>'
            f'<span class="tor-track"><i class="tor-zero"></i>'
            f'<i class="tor-fill {side}" style="--w:{half:.1f}%"></i></span>'
            f'<span class="tor-val {tone}">{sign(int(v))}</span></div>'
        )

    all_trades = find_table(trade_md, "Every Trade")
    best = all_trades[:5]
    worst = all_trades[-5:]

    def trade_rows(rs):
        return [{"cells": [
            f'<span class="chip {"good" if num(r.get("Net",0))>0 else "bad" if num(r.get("Net",0))<0 else "flat"}">'
            f'{esc(r.get("Net",""))}</span>',
            esc(r.get("Manager", "")),
            esc(r.get("In (pts since)", "")),
            esc(r.get("Out (pts since)", "")),
            esc(r.get("Since", "")),
        ], "Manager": r.get("Manager", "")} for r in rs]

    headline_trades = table_html(
        ["Net", "Manager", "In", "Out", "Since"],
        trade_rows(best) + trade_rows(worst), me_col="Manager",
    )
    full_trades = table_html(
        ["Net", "Manager", "In", "Out", "Since"],
        trade_rows(all_trades), me_col="Manager",
    )
    pending = find_table(trade_md, "Pending")
    pending_html = table_html(
        ["Manager", "In", "Out", "Effective"],
        [{"cells": [esc(r.get("Manager", "")), esc(r.get("In", "")),
                    esc(r.get("Out", "")), esc(r.get("Effective", ""))],
          "Manager": r.get("Manager", "")} for r in pending], me_col="Manager",
    ) if pending else ""

    churn = {}
    for r in pending:
        churn[r.get("Manager", "")] = churn.get(r.get("Manager", ""), 0) + 1
    churn_line = ""
    if churn:
        busiest, n = max(churn.items(), key=lambda kv: kv[1])
        churn_line = (
            f'<p class="lede">{len(pending)} more moves land for GW{gw + 1} &mdash; '
            f'<b>{esc(busiest)}</b> made {n} of them.</p>'
        )

    out.append(
        f'<section class="band" id="trades"><div class="wrap"><div class="head">'
        f'<h2>Trade Impact</h2><span class="head-note">Net points since each deal</span></div>'
        f'{badge}<div class="tornado">{"".join(tor)}</div>'
        f'<h3 class="sub-head">Best &amp; worst deals</h3>{headline_trades}'
        f'{drawer("See all " + str(len(all_trades)) + " trades", "full ledger", full_trades)}'
        f'{churn_line}'
        f'{drawer("See pending moves", f"effective GW{gw + 1}", pending_html) if pending_html else ""}'
        f"</div></section>"
    )

    # ---------- ahead ----------
    # Header repeats "Proj"/"Win%", so read this one positionally:
    # Home | Proj | Win% | Draw% | Proj | Away | Win%
    pred_cards = []
    for c in find_rows(pred_md):
        if len(c) < 7:
            continue
        home, hproj, hwin, draw, aproj, away, awin = c[:7]
        hp, ap, dp = num(hwin), num(awin), num(draw)
        me = " me" if ME in home or ME in away else ""
        lead = "home" if hp > ap else ("away" if ap > hp else "level")
        pred_cards.append(
            f'<article class="pred{me}">'
            f'<div class="pred-side"><b>{esc(home)}</b>'
            f'<span class="proj{" lead" if lead == "home" else ""}">{esc(hproj)}</span></div>'
            f'<div class="odds"><span class="odds-bar">'
            f'<i style="--w:{hp:.0f}%"></i><u style="--w:{dp:.0f}%"></u>'
            f'<em style="--w:{ap:.0f}%"></em></span>'
            f'<span class="odds-nums"><b>{hp:.0f}%</b>'
            f'<s>{dp:.0f}% draw</s><b>{ap:.0f}%</b></span></div>'
            f'<div class="pred-side right"><b>{esc(away)}</b>'
            f'<span class="proj{" lead" if lead == "away" else ""}">{esc(aproj)}</span></div>'
            f"</article>"
        )

    proj = find_table(proj_md, "")
    proj_rows = []
    for r in proj:
        t3 = pct(r.get("Top 3", 0))
        b3 = pct(r.get("Bottom 3", 0))
        mid = max(0.0, 100 - t3 - b3)
        segs = ""
        if t3:
            segs += f'<i class="seg top" style="--w:{t3}%"></i>'
        if mid:
            segs += f'<i class="seg mid" style="--w:{mid}%"></i>'
        if b3:
            segs += f'<i class="seg bot" style="--w:{b3}%"></i>'
        proj_rows.append({"cells": [
            f'<b>{esc(r.get("Manager",""))}</b>',
            f'<span class="finish">{esc(ordinal(int(num(r.get("Most Likely Finish", 0)))))}</span>',
            esc(r.get("Chance", "")),
            f'<span class="stack">{segs}</span>',
        ], "Manager": r.get("Manager", "")})
    proj_html = table_html(
        ["Manager", "Likely", "Chance", "Range"], proj_rows,
        ["", "num", "num", ""], me_col="Manager",
    )

    out.append(
        f'<section class="band" id="ahead"><div class="wrap"><div class="head">'
        f'<h2>Gameweek {gw + 1}</h2><span class="head-note">Projected</span></div>'
        f'<div class="preds">{"".join(pred_cards)}</div>'
        f'<h3 class="sub-head">Where it ends<span class="legend">'
        f'<i class="k top"></i>Top 3<i class="k mid"></i>Mid<i class="k bot"></i>Bottom 3</span></h3>'
        f'{proj_html}</div></section>'
    )

    # ---------- extras ----------
    fa = stats.get("free_agents", [])
    fa_html = table_html(
        [f"GW{gw}", "Player", "Pos", "Team"],
        [{"cells": [f'<span class="chip good">{p["gw_points"]}</span>', esc(p["name"]),
                    esc(p["position"]), esc(p["team"])], "Manager": ""} for p in fa],
    ) if fa else ""

    trophy_html = table_html(
        ["Manager", "Topped", "Bottomed", "Robbed", "W-D-L"],
        [{"cells": [
            f'<b>{esc(m["manager"])}</b>',
            f'<span class="chip good">{m["trophies"]["topped"]}</span>' if m["trophies"]["topped"] else "0",
            f'<span class="chip bad">{m["trophies"]["bottomed"]}</span>' if m["trophies"]["bottomed"] else "0",
            f'<span class="chip warn">{m["trophies"]["robbed"]}</span>' if m["trophies"]["robbed"] else "0",
            f'{m["trophies"]["won"]}-{m["trophies"]["drew"]}-{m["trophies"]["lost"]}',
        ], "Manager": m["manager"]} for m in managers], me_col="Manager",
    )

    perf = []
    for line in summary_md.splitlines():
        m = re.match(r"-\s*\*\*(.+?)\*\*\s*—\s*(\d+)\s*pts\s*\((.+?)\)", line.strip())
        if m:
            perf.append((m.group(1), m.group(2), m.group(3)))
    perf_html = "".join(
        f'<div class="perf"><span class="perf-rank">{i+1}</span>'
        f'<span class="perf-name"><b>{esc(n)}</b><span class="sub">{esc(who)}</span></span>'
        f'<span class="perf-pts">{esc(p)}</span></div>'
        for i, (n, p, who) in enumerate(perf)
    )

    pr = find_table(power_md, "")
    power_html = table_html(
        ["#", "Manager", "Table", "PPG", "Luck"],
        [{"cells": [
            esc(r.get("Power Rank", "")), f'<b>{esc(r.get("Manager",""))}</b>',
            esc(r.get("Standings", "")), esc(r.get("Season PPG", "")),
            f'<span class="chip {"good" if "lucky" in r.get("Luck","") and "un" not in r.get("Luck","") else "bad" if "unlucky" in r.get("Luck","") else "flat"}">'
            f'{esc(r.get("Luck",""))}</span>',
        ], "Manager": r.get("Manager", "")} for r in pr], ["num", "", "num", "num", ""],
        me_col="Manager",
    ) if pr else ""

    li = find_table(lineup_md, "Manager Leaderboard")
    lineup_html = table_html(
        ["Net", "Manager", "Changes"],
        [{"cells": [
            f'<span class="chip {"good" if num(r.get("Net",0))>0 else "bad" if num(r.get("Net",0))<0 else "flat"}">'
            f'{esc(r.get("Net",""))}</span>',
            f'<b>{esc(r.get("Manager",""))}</b>',
            f'<span class="sub">{esc(r.get("Changes",""))}</span>',
        ], "Manager": r.get("Manager", "")} for r in li], me_col="Manager",
    ) if li else ""

    extras = [
        drawer("Top performers", f"best individual returns, GW{gw}",
               f'<div class="perf-list">{perf_html}</div>') if perf_html else "",
        drawer("Power rankings", "form-weighted order", power_html) if power_html else "",
        drawer("Trophy cabinet", "season-long counters", trophy_html),
        drawer("Best players nobody owns", "still on the waiver wire", fa_html) if fa_html else "",
        drawer("Lineup changes", f"GW{gw-1} → GW{gw}", lineup_html) if lineup_html else "",
    ]
    out.append(
        '<section class="band" id="more"><div class="wrap"><div class="head">'
        '<h2>The Deep End</h2><span class="head-note">Open what you need</span></div>'
        f'<div class="drawers">{"".join(extras)}</div></div></section>'
    )

    out.append(
        '<footer class="foot"><div class="wrap">'
        '<span class="foot-mark">End of wire</span>'
        f'<span class="foot-note">Gameweek {gw + 1} transmits on the next deadline. '
        'Compiled automatically from the league&rsquo;s live results.</span>'
        "</div></footer>"
    )
    return "\n".join(out)


# ---------------------------------------------------------------- shell

CSS = r"""
:root{
  --pitch:#071410; --turf:#0d2018; --turf-2:#122a20; --line:#1c3a2c;
  --chalk:#eefff5; --fog:#7d9a8c;
  --volt:#d4ff2e; --volt-deep:#3d4d0b;
  --flare:#ff3d6a; --flare-deep:#4d0f1f;
  --amber:#ffb020; --amber-deep:#4d3208;
  --w:1180px;
}
*{box-sizing:border-box}
html{scroll-behavior:smooth}
body{
  margin:0;background:var(--pitch);color:var(--chalk);
  font-family:'Barlow',system-ui,-apple-system,sans-serif;font-size:16px;line-height:1.55;
  -webkit-font-smoothing:antialiased;overflow-x:hidden;
}
body::before{
  content:"";position:fixed;inset:0;pointer-events:none;z-index:0;opacity:.5;
  background:
    radial-gradient(circle at 50% 8%,rgba(212,255,46,.10),transparent 55%),
    repeating-linear-gradient(90deg,transparent 0 78px,rgba(255,255,255,.014) 78px 156px);
}
.wrap{width:min(var(--w),calc(100% - 3rem));margin-inline:auto;position:relative;z-index:1}
h1,h2,h3{margin:0;font-family:'Archivo Black',system-ui,sans-serif;font-weight:400;
  text-transform:uppercase;letter-spacing:-.02em;text-wrap:balance}
a{color:inherit}
.num{text-align:right;font-variant-numeric:tabular-nums}

/* ---------- top bar ---------- */
.topbar{position:sticky;top:0;z-index:40;background:rgba(7,20,16,.82);
  backdrop-filter:blur(14px);border-bottom:1px solid var(--line)}
.topbar-in{display:flex;align-items:center;gap:1.5rem;height:60px}
.brand{display:flex;align-items:center;gap:.6rem;text-decoration:none;font-weight:700;
  font-size:.86rem;letter-spacing:.14em;text-transform:uppercase;white-space:nowrap}
.brand-mark{width:12px;height:12px;background:var(--volt);
  clip-path:polygon(50% 0,100% 50%,50% 100%,0 50%);flex:none}
.jump{display:flex;gap:1.35rem;margin-left:auto;font-size:.76rem;font-weight:600;
  letter-spacing:.13em;text-transform:uppercase}
.jump a{color:var(--fog);text-decoration:none;padding:.2rem 0;border-bottom:2px solid transparent;
  transition:color .18s,border-color .18s}
.jump a:hover,.jump a:focus-visible{color:var(--volt);border-color:var(--volt)}
.gw-chip{font-family:'Bebas Neue',sans-serif;font-size:1.15rem;letter-spacing:.08em;
  background:var(--volt);color:#0a1207;padding:.1rem .6rem 0;line-height:1.5;flex:none}
@media(max-width:820px){.jump{display:none}.gw-chip{margin-left:auto}}

/* ---------- hero ---------- */
.hero{position:relative;padding:clamp(3rem,9vw,6.5rem) 0 clamp(2rem,5vw,3.5rem);overflow:hidden}
.floodlight{position:absolute;inset:-40% -10% auto;height:150%;
  background:conic-gradient(from 200deg at 50% 0,transparent 0deg,rgba(212,255,46,.13) 26deg,
  transparent 60deg,transparent 300deg,rgba(212,255,46,.09) 334deg,transparent 360deg);
  animation:sweep 14s ease-in-out infinite alternate;pointer-events:none}
@keyframes sweep{from{transform:translateX(-6%) rotate(-2deg)}to{transform:translateX(6%) rotate(2deg)}}
.hero-meta{display:flex;align-items:center;gap:.9rem;font-size:.75rem;font-weight:600;
  letter-spacing:.18em;text-transform:uppercase;color:var(--fog);margin-bottom:1.1rem}
.live{display:inline-flex;align-items:center;gap:.45rem;color:var(--volt)}
.live i{width:7px;height:7px;border-radius:50%;background:var(--volt);
  box-shadow:0 0 0 0 rgba(212,255,46,.6);animation:ping 2s ease-out infinite}
@keyframes ping{0%,100%{box-shadow:0 0 0 0 rgba(212,255,46,.55)}50%{box-shadow:0 0 0 7px rgba(212,255,46,0)}}
.sep{width:22px;height:1px;background:var(--line)}
.hero-title{display:flex;align-items:baseline;gap:clamp(.6rem,2vw,1.4rem);flex-wrap:wrap}
.gw-word{font-size:clamp(1.6rem,5.2vw,3.4rem);line-height:.95}
.gw-num{font-family:'Bebas Neue',sans-serif;font-size:clamp(5rem,20vw,13rem);line-height:.78;
  color:var(--volt);letter-spacing:-.01em;
  text-shadow:0 0 60px rgba(212,255,46,.28)}
.hero-sub{max-width:52ch;color:var(--fog);font-size:clamp(.95rem,1.6vw,1.1rem);margin:1.2rem 0 0}

/* ---------- the verdict carousel ---------- */
.verdict{margin-top:clamp(1.3rem,3vw,2rem);max-width:46rem}
.verdict-top{display:flex;align-items:center;gap:.7rem;margin-bottom:.85rem}
.verdict-tag{font-family:'Archivo Black',sans-serif;font-size:.66rem;letter-spacing:.16em;
  text-transform:uppercase;background:var(--volt);color:#0a1207;padding:.2rem .5rem}
.verdict-sub{font-size:.66rem;font-weight:700;letter-spacing:.16em;text-transform:uppercase;
  color:var(--fog)}
/* all slides share one grid cell: the box sizes to the tallest, so rotating
   never shifts the layout underneath it */
.slides{display:grid;align-items:start}
.slide{grid-area:1/1;margin:0;font-size:clamp(1.02rem,2.1vw,1.45rem);line-height:1.4;
  color:var(--chalk);opacity:0;visibility:hidden;transform:translateY(7px);
  transition:opacity .42s ease,transform .42s cubic-bezier(.22,1,.36,1)}
.slide.is-active{opacity:1;visibility:visible;transform:none}
.slide b{color:var(--volt);font-weight:700}
.slide em{font-style:italic;color:var(--chalk)}
.slide-tag{display:inline-block;font-family:'Barlow',sans-serif;font-size:.6rem;font-weight:700;
  letter-spacing:.16em;text-transform:uppercase;color:var(--fog);border:1px solid var(--line);
  padding:.1rem .4rem;margin-right:.6rem;transform:translateY(-3px)}
.verdict-ctl{display:flex;align-items:center;gap:.7rem;margin-top:1.1rem}
.cbtn{width:30px;height:30px;flex:none;display:grid;place-items:center;background:transparent;
  border:1px solid var(--line);color:var(--fog);font-size:1.1rem;line-height:1;cursor:pointer;
  transition:color .18s,border-color .18s,background .18s}
.cbtn:hover,.cbtn:focus-visible{color:var(--volt);border-color:var(--volt);background:var(--turf)}
.dots{display:flex;gap:.4rem}
.vdot{width:9px;height:9px;padding:0;border:0;background:var(--line);cursor:pointer;
  transition:background .2s,transform .2s}
.vdot:hover{background:var(--fog)}
.vdot.on{background:var(--volt);transform:scale(1.25)}
.tick{flex:1;max-width:200px;height:2px;background:var(--line);position:relative;
  overflow:hidden;min-width:40px}
.tick::after{content:"";position:absolute;left:0;top:0;bottom:0;width:0;background:var(--volt)}
.verdict.running .tick::after{animation:tickfill 7.5s linear forwards}
.verdict.paused .tick::after{animation-play-state:paused}
@keyframes tickfill{to{width:100%}}
@media(max-width:560px){.verdict-ctl{gap:.5rem}.tick{min-width:24px}}

/* ---------- kpis ---------- */
.kpis{padding-bottom:1rem}
.kpi-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(215px,1fr));gap:1px;
  background:var(--line);border:1px solid var(--line)}
.kpi{background:var(--turf);padding:1.4rem 1.3rem 1.5rem;position:relative;overflow:hidden;
  transition:background .2s}
.kpi::after{content:"";position:absolute;inset:auto 0 0 0;height:3px;background:var(--fog)}
.kpi-good::after{background:var(--volt)}.kpi-bad::after{background:var(--flare)}
.kpi-warn::after{background:var(--amber)}.kpi-dim::after{background:var(--line)}
.kpi:hover{background:var(--turf-2)}
.kpi-label{display:block;font-size:.68rem;font-weight:700;letter-spacing:.17em;
  text-transform:uppercase;color:var(--fog)}
.kpi-num{display:block;font-family:'Bebas Neue',sans-serif;font-size:3.6rem;line-height:.95;
  margin:.35rem 0 .3rem;font-variant-numeric:tabular-nums}
.kpi-good .kpi-num{color:var(--volt)}.kpi-bad .kpi-num{color:var(--flare)}
.kpi-warn .kpi-num{color:var(--amber)}
.kpi-cap{display:block;font-size:.82rem;color:var(--fog);line-height:1.4}
.kpi-cap b{display:block;color:var(--chalk);font-weight:600}

/* ---------- sections ---------- */
.band{padding:clamp(2.4rem,5vw,4rem) 0;border-top:1px solid var(--line)}
.head{display:flex;align-items:baseline;justify-content:space-between;gap:1rem;
  margin-bottom:1.4rem;flex-wrap:wrap}
.head h2{font-size:clamp(1.35rem,3.2vw,2rem)}
.head-note,.legend{font-size:.7rem;font-weight:700;letter-spacing:.16em;text-transform:uppercase;
  color:var(--fog)}
.lede{color:var(--fog);font-size:.92rem;max-width:70ch;margin:-.5rem 0 1.5rem}
.lede b{color:var(--chalk)}
.sub-head{font-size:.9rem;letter-spacing:.12em;color:var(--fog);margin:2.2rem 0 .9rem;
  display:flex;align-items:center;justify-content:space-between;gap:1rem;flex-wrap:wrap}
.grid-2{display:grid;grid-template-columns:1.15fr .85fr;gap:2.5rem;align-items:start}
/* grid items default to min-width:auto, which lets nowrap tables push the
   column wider than the page -- pin them so .scroll does the scrolling */
.grid-2>*,.panel{min-width:0}
@media(max-width:900px){.grid-2{grid-template-columns:1fr;gap:2.8rem}}

/* ---------- fixtures ---------- */
.fixtures{display:grid;gap:1px;background:var(--line);border:1px solid var(--line)}
.fixture{display:grid;grid-template-columns:1fr auto 1fr;align-items:center;gap:1rem;
  background:var(--turf);padding:1rem 1.3rem;transition:background .2s}
.fixture:hover{background:var(--turf-2)}
.side{display:flex;flex-direction:column;min-width:0}
.side.right{text-align:right;align-items:flex-end}
.side-name{font-weight:700;font-size:1.02rem;white-space:nowrap;overflow:hidden;
  text-overflow:ellipsis;max-width:100%}
.side.win .side-name{color:var(--volt)}
.side.loss .side-name{color:var(--fog)}
.side-team{font-size:.74rem;color:var(--fog);white-space:nowrap;overflow:hidden;
  text-overflow:ellipsis;max-width:100%}
.side.me .side-name{text-decoration:underline;text-decoration-color:var(--amber);
  text-underline-offset:4px;text-decoration-thickness:2px}
.score{display:grid;grid-template-columns:auto auto auto;align-items:center;gap:.55rem;
  position:relative;padding-bottom:.9rem}
.score .s{font-family:'Bebas Neue',sans-serif;font-size:2.5rem;line-height:1;
  font-variant-numeric:tabular-nums;color:var(--fog)}
.score .s.win{color:var(--volt)}
.score .dash{width:14px;height:2px;background:var(--line)}
.margin{position:absolute;left:50%;bottom:0;transform:translateX(-50%);font-size:.62rem;
  letter-spacing:.14em;text-transform:uppercase;color:var(--fog);white-space:nowrap}
@media(max-width:560px){.fixture{grid-template-columns:1fr auto 1fr;padding:.9rem}
  .score .s{font-size:1.9rem}.side-name{font-size:.86rem}}

/* ---------- tables ---------- */
.scroll{overflow-x:auto;-webkit-overflow-scrolling:touch}
table{width:100%;border-collapse:collapse;font-size:.88rem}
th{text-align:left;font-size:.65rem;font-weight:700;letter-spacing:.15em;text-transform:uppercase;
  color:var(--fog);padding:.55rem .7rem;border-bottom:1px solid var(--line);white-space:nowrap}
td{padding:.62rem .7rem;border-bottom:1px solid rgba(28,58,44,.6);white-space:nowrap;
  vertical-align:middle}
tbody tr{transition:background .15s}
tbody tr:hover{background:var(--turf)}
tbody tr.me{background:rgba(255,176,32,.07)}
tbody tr.me:hover{background:rgba(255,176,32,.11)}
.sub{display:block;font-size:.72rem;color:var(--fog);font-weight:400;white-space:normal}
.pos{font-family:'Bebas Neue',sans-serif;font-size:1.3rem;color:var(--fog)}
tr.me .pos{color:var(--amber)}
.movement{font-size:.68rem;font-weight:700;letter-spacing:.08em}
.movement.good{color:var(--volt)}.movement.bad{color:var(--flare)}.movement.flat{color:var(--fog)}
.chip{display:inline-block;padding:.1rem .45rem;font-size:.72rem;font-weight:700;
  font-variant-numeric:tabular-nums;background:var(--line);color:var(--fog)}
.chip.good{background:var(--volt-deep);color:var(--volt)}
.chip.bad{background:var(--flare-deep);color:var(--flare)}
.chip.warn{background:var(--amber-deep);color:var(--amber)}
.formline{display:inline-flex;gap:3px}
.fp{width:19px;height:19px;display:grid;place-items:center;font-style:normal;font-size:.64rem;
  font-weight:700;background:var(--line);color:var(--fog)}
.fp-w{background:var(--volt);color:#0a1207}.fp-l{background:var(--flare);color:#fff}
.fp-d{background:var(--fog);color:#0a1207}

/* ---------- bars ---------- */
.bars,.bench{display:flex;flex-direction:column;gap:.55rem}
.bar-row,.bench-row{display:grid;align-items:center;gap:.8rem;font-size:.85rem}
.bar-row{grid-template-columns:8.5rem 1fr 2rem}
.bench-row{grid-template-columns:8.5rem 1fr 2rem 9rem}
.bar-name,.bench-name{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;color:var(--fog)}
.bar-row.me .bar-name,.bench-row.me .bench-name{color:var(--amber);font-weight:600}
.bar-track{height:14px;background:var(--turf);border:1px solid var(--line);overflow:hidden}
.bar-fill{display:block;height:100%;width:0;background:var(--volt);
  transition:width .9s cubic-bezier(.22,1,.36,1)}
.bar-track.warn .bar-fill{background:var(--amber)}
.is-in .bar-fill{width:var(--w)}
.bar-val,.bench-val{font-family:'Bebas Neue',sans-serif;font-size:1.25rem;text-align:right;
  font-variant-numeric:tabular-nums}
.bench-worst{font-size:.75rem;color:var(--fog);white-space:nowrap;overflow:hidden;
  text-overflow:ellipsis}
.bench-worst b{color:var(--amber)}
@media(max-width:700px){.bench-row{grid-template-columns:7rem 1fr 2rem}.bench-worst{display:none}
  .bar-row{grid-template-columns:7rem 1fr 2rem}}

/* ---------- slope ---------- */
.slope-head{display:flex;justify-content:space-between;font-size:.65rem;font-weight:700;
  letter-spacing:.16em;text-transform:uppercase;color:var(--fog);
  padding:0 0 .5rem;border-bottom:1px solid var(--line);margin-bottom:.6rem}
.slope{display:flex;flex-direction:column}
.slope-row{display:grid;grid-template-columns:2rem 1fr 5.5rem 2rem 7.5rem;align-items:center;
  gap:.85rem;padding:.5rem 0;border-bottom:1px solid rgba(28,58,44,.55)}
.slope-rank,.slope-real{font-family:'Bebas Neue',sans-serif;font-size:1.5rem;color:var(--fog);
  text-align:center}
.slope-row.me .slope-rank,.slope-row.me .slope-real{color:var(--amber)}
.slope-name b{font-weight:600}
.slope-line{position:relative;height:26px}
.slope-line::before{content:"";position:absolute;left:6px;right:6px;top:calc(var(--from) * 0px + 50%);
  height:2px;background:currentColor;opacity:.55;
  transform:rotate(calc((var(--to) - var(--from)) * 2.2deg));transform-origin:left center}
.slope-line.good{color:var(--volt)}.slope-line.bad{color:var(--flare)}.slope-line.flat{color:var(--fog)}
.dot{position:absolute;top:50%;width:8px;height:8px;border-radius:50%;background:currentColor;
  transform:translateY(-50%)}
.dot.a{left:0}.dot.b{right:0}
.swing{font-size:.76rem;font-weight:700;font-variant-numeric:tabular-nums}
.swing em{font-style:normal;font-weight:400;font-size:.68rem;letter-spacing:.1em;
  text-transform:uppercase;color:var(--fog);margin-left:.3rem}
.swing.good{color:var(--volt)}.swing.bad{color:var(--flare)}.swing.flat{color:var(--fog)}
@media(max-width:760px){.slope-row{grid-template-columns:1.6rem 1fr 1.6rem 5.5rem;gap:.6rem}
  .slope-line{display:none}}

/* ---------- trades ---------- */
.badge{display:inline-flex;align-items:center;gap:.7rem;background:var(--volt);color:#0a1207;
  padding:.5rem .9rem;margin-bottom:1.6rem;font-weight:700}
.badge-label{font-family:'Archivo Black',sans-serif;font-size:.72rem;letter-spacing:.12em;
  text-transform:uppercase}
.badge-name{font-size:.95rem}
.badge-val{font-variant-numeric:tabular-nums;opacity:.75}
.tornado{display:flex;flex-direction:column;gap:.5rem}
.tor-row{display:grid;grid-template-columns:10rem 1fr 3rem;align-items:center;gap:.9rem;
  font-size:.85rem}
.tor-name{text-align:right;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;color:var(--fog)}
.tor-row.me .tor-name{color:var(--amber);font-weight:600}
.tor-track{position:relative;height:14px;background:var(--turf);border:1px solid var(--line)}
.tor-zero{position:absolute;left:50%;top:-1px;bottom:-1px;width:1px;background:var(--line)}
.tor-fill{position:absolute;top:0;bottom:0;width:0;transition:width .9s cubic-bezier(.22,1,.36,1)}
.tor-fill.pos{left:50%;background:var(--volt)}
.tor-fill.neg{right:50%;background:var(--flare)}
.is-in .tor-fill{width:var(--w)}
.tor-val{font-weight:700;font-variant-numeric:tabular-nums;text-align:right}
.tor-val.good{color:var(--volt)}.tor-val.bad{color:var(--flare)}.tor-val.flat{color:var(--fog)}
@media(max-width:700px){.tor-row{grid-template-columns:7rem 1fr 2.4rem;gap:.6rem}}

/* ---------- predictions ---------- */
.preds{display:grid;gap:1px;background:var(--line);border:1px solid var(--line)}
.pred{display:grid;grid-template-columns:1fr minmax(120px,200px) 1fr;align-items:center;gap:1rem;
  background:var(--turf);padding:.9rem 1.3rem}
.pred.me{background:rgba(255,176,32,.06)}
.pred-side{display:flex;flex-direction:column;min-width:0}
.pred-side.right{text-align:right;align-items:flex-end}
.pred-side b{font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:100%}
.proj{font-family:'Bebas Neue',sans-serif;font-size:1.2rem;color:var(--fog)}
.proj.lead{color:var(--chalk)}
.odds-bar{display:flex;height:8px;background:var(--line);overflow:hidden}
.odds-bar i{background:var(--volt);width:var(--w)}
.odds-bar u{background:var(--fog);width:var(--w)}
.odds-bar em{background:var(--flare);width:var(--w)}
.odds-nums{display:flex;justify-content:space-between;align-items:baseline;font-size:.7rem;
  font-weight:700;color:var(--fog);margin-top:.3rem;gap:.5rem}
.odds-nums s{text-decoration:none;font-weight:600;font-size:.6rem;letter-spacing:.08em;
  text-transform:uppercase;opacity:.65}
.finish{font-family:'Bebas Neue',sans-serif;font-size:1.15rem}
@media(max-width:620px){.pred{grid-template-columns:1fr;gap:.5rem;text-align:center}
  .pred-side.right{align-items:center;text-align:center}}
.stack{display:flex;height:11px;min-width:110px;background:var(--turf);border:1px solid var(--line)}
.seg{width:var(--w);height:100%}
.seg+.seg{border-left:2px solid var(--pitch)}
.seg.top{background:var(--volt)}.seg.mid{background:var(--line)}.seg.bot{background:var(--flare)}
.legend{display:inline-flex;align-items:center;gap:.4rem}
.k{width:10px;height:10px;display:inline-block;margin-left:.7rem}
.k.top{background:var(--volt)}.k.mid{background:var(--line)}.k.bot{background:var(--flare)}

/* ---------- performers ---------- */
.perf-list{display:flex;flex-direction:column}
.perf{display:grid;grid-template-columns:2.2rem 1fr auto;align-items:center;gap:.9rem;
  padding:.6rem 0;border-bottom:1px solid rgba(28,58,44,.55)}
.perf-rank{font-family:'Bebas Neue',sans-serif;font-size:1.5rem;color:var(--volt)}
.perf-pts{font-family:'Bebas Neue',sans-serif;font-size:1.5rem;font-variant-numeric:tabular-nums}

/* ---------- drawers ---------- */
.drawers{display:flex;flex-direction:column;gap:.7rem}
.drawer{border:1px solid var(--line);background:var(--turf)}
.drawer+.drawer,.band .drawer{margin-top:.7rem}
.drawer-btn{cursor:pointer;list-style:none;display:flex;align-items:center;gap:.9rem;
  padding:.95rem 1.2rem;font-weight:700;font-size:.8rem;letter-spacing:.12em;
  text-transform:uppercase;transition:background .18s,color .18s}
.drawer-btn::-webkit-details-marker{display:none}
.drawer-btn:hover{background:var(--turf-2);color:var(--volt)}
.drawer-note{font-size:.68rem;font-weight:600;letter-spacing:.1em;color:var(--fog);
  text-transform:uppercase}
.chev{margin-left:auto;width:9px;height:9px;border-right:2px solid currentColor;
  border-bottom:2px solid currentColor;transform:rotate(45deg) translate(-2px,-2px);
  transition:transform .25s}
.drawer[open] .drawer-btn{color:var(--volt);border-bottom:1px solid var(--line)}
.drawer[open] .chev{transform:rotate(-135deg) translate(-3px,-3px)}
.drawer-body{padding:1rem 1.2rem 1.3rem;animation:reveal .32s cubic-bezier(.22,1,.36,1)}
@keyframes reveal{from{opacity:0;transform:translateY(-6px)}to{opacity:1;transform:none}}

/* ---------- footer ---------- */
.foot{border-top:1px solid var(--line);padding:2.6rem 0 3.4rem;text-align:center}
.foot-mark{display:block;font-family:'Archivo Black',sans-serif;text-transform:uppercase;
  letter-spacing:.2em;font-size:.85rem;color:var(--fog)}
.foot-note{display:block;margin-top:.6rem;font-size:.78rem;color:var(--fog)}

/* ---------- cursor ---------- */
@media(hover:hover) and (pointer:fine){
  body.kicker,body.kicker a,body.kicker summary{cursor:none}
  .kit{position:fixed;top:0;left:0;width:46px;height:46px;pointer-events:none;z-index:9999;
    transform:translate3d(-100px,-100px,0);will-change:transform}
  .kit svg{display:block;overflow:visible}
  .kit .leg-kick{transform-origin:19px 26px;transition:transform .12s ease-out}
  .kit.swing .leg-kick{transform:rotate(-38deg)}
  .kit.left{transform:scaleX(-1)}
  .ball-fly{position:fixed;top:0;left:0;width:20px;height:20px;pointer-events:none;z-index:9998}
  .shock{position:fixed;width:14px;height:14px;border:2px solid var(--volt);border-radius:50%;
    pointer-events:none;z-index:9997;animation:shock .5s ease-out forwards}
  @keyframes shock{to{width:80px;height:80px;opacity:0;margin:-33px 0 0 -33px}}
}
@media(prefers-reduced-motion:reduce){
  *{animation-duration:.01ms!important;animation-iteration-count:1!important;
    transition-duration:.01ms!important;scroll-behavior:auto!important}
  body.kicker,body.kicker a,body.kicker summary{cursor:auto}
  .kit,.ball-fly,.shock{display:none!important}
  .bar-fill,.tor-fill{width:var(--w)!important}
}
"""

JS = r"""
(function(){
  var reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  var fine = window.matchMedia('(hover: hover) and (pointer: fine)').matches;

  /* reveal bars + count numbers */
  var io = new IntersectionObserver(function(entries){
    entries.forEach(function(e){
      if(!e.isIntersecting) return;
      e.target.classList.add('is-in');
      io.unobserve(e.target);
      e.target.querySelectorAll('[data-count]').forEach(function(el){
        var target = parseFloat(el.getAttribute('data-count'));
        if(isNaN(target) || reduce) return;
        var pad = el.textContent.trim().length > String(Math.round(target)).length;
        var fmt = function(v){ return pad && v < 10 ? '0'+v : String(v); };
        var t0 = performance.now(), dur = 900;
        function step(now){
          /* clamp: a frame timestamp can precede t0 and would render a negative */
          var p = Math.max(0, Math.min(1,(now-t0)/dur));
          if(p >= 1){ el.textContent = fmt(target); return; }
          el.textContent = fmt(Math.round(target*(1-Math.pow(1-p,3))));
          requestAnimationFrame(step);
        }
        requestAnimationFrame(step);
      });
    });
  },{threshold:.18});
  document.querySelectorAll('.band,.kpis,.hero,.bars,.bench,.tornado').forEach(function(el){
    io.observe(el);
  });

  /* ---- the verdict carousel ---- */
  document.querySelectorAll('[data-carousel]').forEach(function(car){
    var slides = Array.prototype.slice.call(car.querySelectorAll('.slide'));
    var dots   = Array.prototype.slice.call(car.querySelectorAll('.vdot'));
    if(slides.length < 2) return;
    var i = 0, timer = null, DWELL = 7500;

    function show(n){
      i = (n + slides.length) % slides.length;
      slides.forEach(function(s,k){
        s.classList.toggle('is-active', k === i);
        s.setAttribute('aria-hidden', k === i ? 'false' : 'true');
      });
      dots.forEach(function(d,k){
        d.classList.toggle('on', k === i);
        d.setAttribute('aria-selected', k === i ? 'true' : 'false');
      });
      arm();
    }
    function arm(){
      clearTimeout(timer);
      if(reduce) return;                     /* no autoplay when motion is reduced */
      car.classList.remove('running');
      void car.offsetWidth;                  /* restart the progress bar animation */
      car.classList.add('running');
      timer = setTimeout(function(){ show(i+1); }, DWELL);
    }
    function pause(){ clearTimeout(timer); car.classList.add('paused'); }
    function resume(){ car.classList.remove('paused'); if(!reduce) arm(); }

    car.querySelector('[data-next]').addEventListener('click', function(){ show(i+1); });
    car.querySelector('[data-prev]').addEventListener('click', function(){ show(i-1); });
    dots.forEach(function(d,k){ d.addEventListener('click', function(){ show(k); }); });
    car.addEventListener('mouseenter', pause);
    car.addEventListener('mouseleave', resume);
    car.addEventListener('focusin', pause);
    car.addEventListener('focusout', function(e){
      if(!car.contains(e.relatedTarget)) resume();
    });
    car.addEventListener('keydown', function(e){
      if(e.key === 'ArrowRight'){ show(i+1); } else if(e.key === 'ArrowLeft'){ show(i-1); }
    });
    document.addEventListener('visibilitychange', function(){
      document.hidden ? pause() : resume();
    });
    arm();
  });

  if(!fine || reduce) return;

  /* ---- footballer cursor that kicks a ball on click ---- */
  var NS='http://www.w3.org/2000/svg';
  var kit=document.createElement('div');
  kit.className='kit';
  kit.innerHTML=
    '<svg viewBox="0 0 46 46" width="46" height="46" fill="none">'+
      '<g stroke="#d4ff2e" stroke-width="3.4" stroke-linecap="round">'+
        '<path d="M18 15 L10 21"/>'+
        '<path d="M25 15 L34 12"/>'+
        '<path d="M19 26 L16 39 L11 40"/>'+
        '<g class="leg-kick"><path d="M22 26 L31 31 L37 27"/></g>'+
      '</g>'+
      '<circle cx="22.5" cy="7.5" r="5.2" fill="#d4ff2e"/>'+
      '<path d="M18.5 12 h7 l2.5 14 h-12 z" fill="#d4ff2e"/>'+
      '<circle class="rest-ball" cx="41" cy="30" r="4.6" fill="#eefff5"/>'+
      '<path class="rest-ball" d="M41 26.6 l2.6 1.9 -1 3.1 h-3.2 l-1-3.1z" fill="#0d2018"/>'+
    '</svg>';
  document.body.appendChild(kit);
  document.body.classList.add('kicker');

  var x=innerWidth/2, y=innerHeight/2, tx=x, ty=y, lastX=x;
  addEventListener('mousemove',function(e){ tx=e.clientX; ty=e.clientY; },{passive:true});
  (function loop(){
    x += (tx-x)*0.22; y += (ty-y)*0.22;
    if(Math.abs(tx-lastX)>0.6){ kit.classList.toggle('left', tx < lastX); lastX=tx; }
    kit.style.transform='translate3d('+(x-14)+'px,'+(y-30)+'px,0)';
    requestAnimationFrame(loop);
  })();

  function ball(size){
    var s=document.createElementNS(NS,'svg');
    s.setAttribute('viewBox','0 0 20 20');
    s.setAttribute('width',size); s.setAttribute('height',size);
    s.innerHTML='<circle cx="10" cy="10" r="9" fill="#eefff5"/>'+
      '<path d="M10 3.6 l5 3.7 -1.9 6h-6.2l-1.9-6z" fill="#0d2018"/>'+
      '<path d="M10 3.6 V0 M15 7.3 l3.4-1.2 M13.1 13.3 l2.1 2.9 M6.9 13.3 l-2.1 2.9 M5 7.3 L1.6 6.1"'+
      ' stroke="#0d2018" stroke-width="1.5"/>';
    return s;
  }

  addEventListener('pointerdown',function(e){
    if(e.pointerType!=='mouse') return;
    kit.classList.add('swing');
    setTimeout(function(){ kit.classList.remove('swing'); },150);

    var ring=document.createElement('div');
    ring.className='shock';
    ring.style.left=e.clientX+'px'; ring.style.top=e.clientY+'px';
    ring.style.margin='-7px 0 0 -7px';
    document.body.appendChild(ring);
    setTimeout(function(){ ring.remove(); },520);

    var fly=document.createElement('div');
    fly.className='ball-fly';
    fly.appendChild(ball(20));
    document.body.appendChild(fly);

    var dir = kit.classList.contains('left') ? -1 : 1;
    var vx = dir*(9+Math.random()*5), vy = -(7+Math.random()*4);
    var bx=e.clientX+dir*16, by=e.clientY+4, rot=0, t=0;
    (function fling(){
      t++; vy+=0.55; bx+=vx; by+=vy; rot+=vx*2.2;
      fly.style.transform='translate3d('+(bx-10)+'px,'+(by-10)+'px,0) rotate('+rot+'deg)';
      fly.style.opacity = String(Math.max(0, 1 - t/48));
      if(t<48 && by<innerHeight+120) requestAnimationFrame(fling); else fly.remove();
    })();
  },{passive:true});
})();
"""


def main():
    stats_path = os.path.join(SEASON_DIR, "dashboard_stats.json")
    if not os.path.exists(stats_path):
        raise SystemExit("dashboard_stats.json missing -- run dashboard_stats.py first")
    with open(stats_path) as f:
        stats = json.load(f)

    print("Reading recap files...")
    summary_md = latest("weekly_summaries", r"GW\d+_summary\.md")
    power_md = latest("power_rankings", r"GW\d+_power_rankings\.md")
    trade_md = read("trade_impact.md")
    pred_md = read("h2h_predictions.md")
    proj_md = read("season_projection.md")
    lineup_md = read("lineup_impact.md")

    print("Inlining fonts...")
    fonts = font_faces()

    body = build(stats, summary_md, power_md, trade_md, pred_md, proj_md, lineup_md)
    html = (
        "<title>The Sharnbrook Wire</title>\n"
        f"<style>{fonts}\n{CSS}</style>\n"
        f"{body}\n"
        f"<script>{JS}</script>"
    )

    os.makedirs(OUT_DIR, exist_ok=True)
    with open(OUT_FILE, "w") as f:
        f.write(html)
    print(f"Wrote {OUT_FILE} ({len(html)/1024:.0f} KB)")


if __name__ == "__main__":
    main()
