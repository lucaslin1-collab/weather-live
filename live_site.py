"""The live bot's website: Live board, Track record, How it works."""
import os

import numpy as np
import pandas as pd

import config
from dashboard import ORDER, bracket_chart, equity_chart, esc, money, page, pct, verdict_badge

MARK = ('<svg viewBox="0 0 32 32" aria-hidden="true"><rect class="mk-bg" width="32" height="32" rx="8" fill="#13202f"/>'
        '<rect class="mk-stem" x="13" y="6" width="6" height="15" rx="3" fill="#fbfcfd"/>'
        '<rect x="14.5" y="11" width="3" height="10" rx="1.5" fill="#eb6834"/>'
        '<circle cx="16" cy="22.5" r="4.5" fill="#eb6834"/></svg>')
PAGES = [("index.html", "Live board"), ("record.html", "Track record"), ("method.html", "How it works")]


def nav(current):
    cur = ' aria-current="page"'
    links = "".join(f'<li><a class="nav" href="{h}"{cur if h == current else ""}>{l}</a></li>' for h, l in PAGES)
    links += f'<li><a class="nav" href="{config.HUB_URL}">All bots</a></li>'
    return (f'<nav class="site-nav" aria-label="Site"><a class="brand" href="index.html">{MARK}'
            f'<span>{esc(config.SITE_NAME)}</span></a><ul>{links}</ul></nav>')


def head(path, desc, refresh=False):
    base = f"https://{config.SITE_DOMAIN}" if config.SITE_DOMAIN else config.SITE_URL
    url = f"{base}/{'' if path == 'index.html' else path}" if base else ""
    return (f'<meta name="description" content="{esc(desc)}">'
            + (f'<link rel="canonical" href="{url}">' if url else "")
            + ('<meta http-equiv="refresh" content="300">' if refresh else "")
            + '<link rel="icon" href="favicon.svg" type="image/svg+xml">')


FOOTER = ('<footer><p>The live bot combines each station’s readings so far with the morning forecast to '
          'price today’s brackets, and paper-trades the ones the market looks wrong on. Observations from '
          'aviationweather.gov and the Iowa Environmental Mesonet; forecasts from Open-Meteo (CC BY 4.0). '
          'A research project, not financial advice. Not affiliated with Kalshi. '
          '<a href="method.html">How it works</a>.</p></footer>')


def mode_notice(demo, real_rows):
    if demo:
        return ('<p class="notice demo">Demo data. Every number on this page is simulated.</p>')
    if config.LIVE_TRADING:
        env = "real money" if config.KALSHI_ENV == "prod" else "Kalshi’s demo exchange"
        return f'<p class="notice">Real orders are switched on ({env}), within the limits on the How it works page.</p>'
    return '<p class="notice">Paper trading: positions are simulated at real prices. No real orders are placed.</p>'


def mark_price(pos, markets):
    """What a position could be sold for now: YES at the bid, NO at 1 - ask."""
    q = {r["ticker"]: r for r in markets}
    m = q.get(pos.ticker)
    if not m or m.get("yes_bid") is None:
        return None
    return m["yes_bid"] if pos.side == "YES" else 1 - m["yes_ask"]


def positions_table(today_pos, quotes):
    if today_pos.empty:
        return ('<p class="empty">No positions yet today. The bot buys only when its edge clears '
                f'{config.EDGE_THRESHOLD * 100:.0f}¢ a contract after fees, between '
                f'{config.DECISION_HOURS[0]}:00 and {config.DECISION_HOURS[-1]}:00 local time in each city.</p>')
    rows = []
    for r in today_pos.sort_values("opened_at").itertuples():
        now = mark_price(r, quotes)
        unreal = None if now is None else r.contracts * (now - r.price)
        opened = pd.Timestamp(r.opened_at).tz_localize("UTC").tz_convert("America/New_York")
        rows.append(f'<tr><td class="city">{esc(r.city)}</td><td class="num bracket">{esc(r.label)}</td>'
                    f'<td class="bet"><span class="side {r.side.lower()}">Buy {r.side}</span></td>'
                    f'<td class="num" data-label="Paid">{r.price * 100:.0f}¢</td>'
                    f'<td class="num" data-label="Now">{"–" if now is None else f"{now * 100:.0f}¢"}</td>'
                    f'<td class="num" data-label="Contracts">{int(r.contracts)}</td>'
                    f'<td class="num strong" data-label="Open P&L">{"–" if unreal is None else money(unreal)}</td>'
                    f'<td class="rec">{opened:%-I:%M %p} ET</td></tr>')
    return ('<div class="scroll picks-wrap"><table class="picks"><thead><tr><th>City</th><th>Bracket</th><th>Bet</th>'
            '<th class="num">Paid</th><th class="num">Now</th><th class="num">Contracts</th>'
            '<th class="num">Open P&amp;L</th><th>Opened</th></tr></thead>'
            f'<tbody>{"".join(rows)}</tbody></table></div>'
            '<p class="fine">“Now” is what the position could be sold for at this moment. Paper positions are '
            'held to settlement and scored in the track record.</p>')


def boards(state, today_pos):
    held = {}
    for r in today_pos.itertuples():
        held.setdefault(r.series, {})[r.label] = r.side
    bs = sorted(state["boards"], key=lambda b: (b["series"] not in held, not b["brackets"],
                                                  ORDER.get(b["verdict"], 1), b["city"]))
    out = ['<section><div class="sec-head"><h2>Every city right now</h2><ul class="legend">'
           '<li><span class="key s-market-bg"></span>Market price</li>'
           '<li><span class="key s-model-bg"></span>AI probability</li></ul></div>'
           '<div class="boards"><div class="boards-grid">']
    for b in bs:
        top = f'<div class="b-head"><h3>{esc(b["city"])}</h3>{verdict_badge(b["verdict"])}</div>'
        temp = ""
        if "obs_max" in b:
            final = (f'<span class="range">final high likely {b["mu"] - 1.28 * b["sigma"]:.0f}–'
                     f'{b["mu"] + 1.28 * b["sigma"]:.0f}°F</span>' if "mu" in b else "")
            temp = (f'<div class="temp"><span class="deg">{b["obs_max"]:.0f}°</span>'
                    f'<span class="range">high so far · now {b["obs_now"]:.0f}° at {esc(b["local_time"])}</span></div>'
                    f'<p class="final">{final}</p>')
        chart = bracket_chart(b, held.get(b["series"], {})) if b["brackets"] else \
            f'<p class="empty small">{esc(b["note"])}</p>'
        cls = "board picked" if b["series"] in held else "board"
        out.append(f'<article class="{cls}">{top}{temp}{chart}<p class="station">Settles on {esc(b["station"])}</p></article>')
    out.append('</div></div></section>')
    return "".join(out)


def by_hour_table(summary):
    if not summary or not summary.get("hours"):
        return ""
    rows = []
    for h, c in sorted(summary["hours"].items(), key=lambda kv: int(kv[0])):
        ci = c.get("ci90")
        rows.append(f'<tr><td class="city">{int(h) % 12 or 12}:00 {"AM" if int(h) < 12 else "PM"}</td>'
                    f'<td>{verdict_badge(c["verdict"])}</td><td class="num">{c.get("bss_blend", float("nan")):+.3f}</td>'
                    f'<td class="num">{c["trades"]}</td><td class="num">{pct(c.get("win_rate"))}</td>'
                    f'<td class="num strong">{money(c.get("pnl", 0.0))}</td>'
                    f'<td class="num">{(money(ci[0]) + " to " + money(ci[1])) if ci else "–"}</td></tr>')
    return ('<h3 class="sub">Backtest by time of day</h3><p class="fine">Each row is a separate test that '
            'only trades at that hour, local to each city, so you can see which part of the day has an edge. '
            'Profit is per $1 contract, fees included.</p>'
            '<div class="scroll"><table class="cities"><thead><tr><th>Entered at</th><th>Verdict</th>'
            '<th class="num">Skill vs market</th><th class="num">Bets</th><th class="num">Won</th>'
            f'<th class="num">Profit</th><th class="num">90% range</th></tr></thead><tbody>{"".join(rows)}</tbody></table></div>')


def by_city_table(summary):
    if not summary:
        return '<p class="empty">No backtest yet.</p>'
    rows = []
    for s, c in sorted(summary["cities"].items(), key=lambda kv: (ORDER.get(kv[1]["verdict"], 1), -(kv[1].get("pnl") or 0))):
        ci = c.get("ci90")
        rows.append(f'<tr><td class="city">{esc(c["city"])}</td><td>{verdict_badge(c["verdict"])}</td>'
                    f'<td class="num">{c.get("bss_blend", float("nan")):+.3f}</td><td class="num">{c["trades"]}</td>'
                    f'<td class="num">{pct(c.get("win_rate"))}</td><td class="num strong">{money(c.get("pnl", 0.0))}</td>'
                    f'<td class="num">{(money(ci[0]) + " to " + money(ci[1])) if ci else "–"}</td></tr>')
    return (f'<h3 class="sub">Backtest by city, {summary["period"][0]} to {summary["period"][1]}</h3>'
            '<div class="scroll"><table class="cities"><thead><tr><th>City</th><th>Verdict</th>'
            '<th class="num">Skill vs market</th><th class="num">Bets</th><th class="num">Won</th>'
            f'<th class="num">Profit</th><th class="num">90% range</th></tr></thead><tbody>{"".join(rows)}</tbody></table></div>')


def record_body(state, notice=""):
    pos = state["positions"]
    settled = pos[pos["won"].notna()] if len(pos) else pos
    out = ['<header class="top"><h1>Track record</h1><p class="lede">Two different things live on this page: '
           'the bot’s own paper trades since it went live, and a backtest that replays past days to estimate '
           'how it would have done. Only the first is trades it actually made. No real money is involved in '
           'either.</p></header>', notice,
           '<section><h2>Paper trades since going live</h2><p class="fine">Every paper position at the price the '
           f'bot paid, held to settlement, sized from a {money(config.PAPER_BANKROLL)} paper bankroll.</p>']
    if len(settled):
        out.append(f'<p class="stats"><span><b>{len(settled)}</b> positions settled</span>'
                   f'<span><b>{pct(settled["won"].mean())}</b> won</span>'
                   f'<span><b>{money(settled["pnl"].sum())}</b> paper profit</span>'
                   f'<span><b>{int(pos["won"].isna().sum())}</b> open</span></p>')
        cum = settled.groupby("date")["pnl"].sum().sort_index().cumsum()
        series = [("Paper trading", "s-model", [[f"{d:%Y-%m-%d}", round(float(v), 2)] for d, v in cum.items()])]
        out.append('<div class="sec-head"><h3>Cumulative paper profit</h3></div>' + equity_chart(series))
    else:
        out.append('<p class="stats"><span>No positions have settled yet.</span></p>')
    out.append('</section>')
    sm = state["summary"]
    if sm:
        o = sm.get("overall", {})
        ci = o.get("ci90")
        intro = (f'<p class="fine">Not trades the bot made: a simulation that replays {sm["period"][0]} to '
                 f'{sm["period"][1]} as if it had been trading then, with $1 contracts and Kalshi’s fees. Over that '
                 f'stretch it would have placed {o.get("trades", 0)} bets for {money(o.get("pnl", 0.0))}'
                 + (f' (90% range {money(ci[0])} to {money(ci[1])})' if ci else "") + '. '
                 + ('A loss here means this approach hasn’t beaten the market on past days, which is why the '
                    'bot only paper trades.' if o.get("pnl", 0.0) < 0 else
                    'Past results still need live paper trades to confirm them.') + '</p>')
    else:
        intro = ""
    out.append('<section><h2>Backtest: how it would have done on past days</h2>' + intro
               + by_hour_table(sm) + by_city_table(sm) + '</section>')
    if len(pos):
        rows = []
        for r in pos.sort_values(["date", "opened_at"], ascending=False).head(80).itertuples():
            res = ('<span class="badge warn"><span class="ico">○</span>Open</span>' if pd.isna(r.won) else
                   '<span class="badge good"><span class="ico">✓</span>Won</span>' if r.won else
                   '<span class="badge bad"><span class="ico">✕</span>Lost</span>')
            rows.append(f'<tr><td>{pd.Timestamp(r.date):%b %-d}</td><td class="city">{esc(r.city)}</td>'
                        f'<td class="num">{esc(r.label)}</td><td><span class="side {r.side.lower()}">Buy {r.side}</span></td>'
                        f'<td class="num">{r.price * 100:.0f}¢</td><td class="num">{int(r.contracts)}</td>'
                        f'<td>{res}</td><td class="num strong">{"–" if pd.isna(r.pnl) else money(r.pnl)}</td></tr>')
        out.append('<section><h2>Every paper position</h2><div class="scroll"><table class="log"><thead><tr>'
                   '<th>Date</th><th>City</th><th>Bracket</th><th>Bet</th><th class="num">Paid</th>'
                   '<th class="num">Contracts</th><th>Result</th><th class="num">Profit</th></tr></thead>'
                   f'<tbody>{"".join(rows)}</tbody></table></div></section>')
    return "".join(out)


def method_body():
    h0, h1 = config.DECISION_HOURS[0], config.DECISION_HOURS[-1]
    return f'''<header class="top"><h1>How the live bot works</h1><p class="lede">It trades during the day,
using what each weather station has already recorded.</p></header>
<article class="prose">
<h2>What it knows that the morning forecast doesn’t</h2>
<p>By late morning the question changes from “what will the high be?” to “how much higher will it get?”
Every few minutes the bot reads the latest reports from each settlement station. It uses
temperatures to a tenth of a degree, and the true six-hour maximum that stations report four times a
day. That gives the highest temperature so far. Kalshi settles on the NWS Daily Climate Report, whose
day runs midnight to midnight in local standard time, so the bot uses that window too.</p>
<h2>The model</h2>
<p>For each city it predicts how much further the temperature will rise. It uses the morning forecast
for the rest of the day, corrected by how far off that forecast is right now, plus the time of day. Every
bracket below the high already reached gets zero; the rest share the remaining probability. The model is
trained on two years of station observations and official highs. Its probability is then blended with
the market price, weighted by how well each predicted past outcomes.</p>
<h2>When it trades</h2>
<p>Between {h0}:00 and {h1}:00 local time in each city, about every 10 minutes. It buys a bracket when
the expected profit beats {config.EDGE_THRESHOLD * 100:.0f}¢ per contract after fees, the spread is
{config.MAX_SPREAD * 100:.0f}¢ or less, and the price is between {config.MIN_PRICE * 100:.0f}¢ and
{config.MAX_PRICE * 100:.0f}¢. It holds at most one position per bracket and
{config.MAX_PICKS_PER_CITY} per city per day, and holds each one until the market settles. GitHub’s
scheduler can start a run several minutes late, so this setup is too slow to compete on speed. Its
edge, if any, comes from modeling the rest of the day better.</p>
<h2>Paper first</h2>
<p>All positions are paper trades at the real prices shown when the bot acted, sized from a
{money(config.PAPER_BANKROLL)} paper bankroll. The backtest scores each hour of the day separately,
so the <a href="record.html">track record</a> shows which hours, if any, have an edge.</p>
<p>Real orders exist in the code but are switched off. Turning them on takes deliberate steps by the
owner, and they then stay within hard limits: only cities with a proven edge,
{money(config.MAX_ORDER_DOLLARS)} per order and {money(config.MAX_DAILY_DOLLARS)} per day.</p>
<h2>Limits</h2>
<p>Intraday weather markets are watched by fast automated traders. Late in the day most brackets are
nearly decided, so prices sit near 1¢ or 99¢ and little edge is left after fees. This is a research
project, not financial advice, and it isn’t affiliated with Kalshi.</p>
</article>'''


def build(state, demo=False):
    out = config.SITE_DIR
    os.makedirs(out, exist_ok=True)
    now_et = state["now"].tz_localize("UTC").tz_convert("America/New_York")
    pos = state["positions"]
    today_pos = pos[pos["date"] == pos["date"].max()] if len(pos) else pos
    today_pos = today_pos[today_pos["won"].isna()] if len(today_pos) else today_pos
    all_q = state.get("quotes", [])
    n_open = int(len(today_pos))
    header = (f'<header class="top"><h1>Live high-temperature board <span class="date">{now_et:%A, %B %-d}</span></h1>'
              f'<p class="lede">{n_open} open {"position" if n_open == 1 else "positions"} today. '
              f'Updated {now_et:%-I:%M %p} ET; this page reloads every 5 minutes.</p></header>')
    index = (nav("index.html") + header + mode_notice(demo, state.get("real"))
             + '<section><h2>Open positions</h2>' + positions_table(today_pos, all_q) + '</section>'
             + boards(state, today_pos) + FOOTER)
    pages = {
        "index.html": page(index, f"{config.SITE_NAME}", head("index.html", "Live Kalshi high-temperature board and paper trades.", True)),
        "record.html": page(nav("record.html") + record_body(state, mode_notice(demo, None) if demo else "") + FOOTER,
                            f"Track record: {config.SITE_NAME}", head("record.html", "Paper trading record and backtest by hour and city.")),
        "method.html": page(nav("method.html") + method_body() + FOOTER, f"How it works: {config.SITE_NAME}",
                            head("method.html", "How the live high-temperature bot works.")),
        "favicon.svg": MARK.replace('aria-hidden="true"', 'xmlns="http://www.w3.org/2000/svg"'),
        ".nojekyll": "",
    }
    for name, content in pages.items():
        with open(os.path.join(out, name), "w", encoding="utf-8") as fh:
            fh.write(content)
