"""Render the daily dashboard as one self-contained HTML page."""
import html
import json
import math

import pandas as pd

import config

VERDICT = {  # icon + words; color never carries the meaning alone
    "edge": ("✓", "Proven edge", "good"),
    "unproven": ("○", "Unproven", "warn"),
    "no edge": ("✕", "No edge", "bad"),
}


def esc(x):
    return html.escape(str(x))


def pct(x, digits=0):
    return "–" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{x * 100:.{digits}f}%"


def money(x):
    return "–" if x is None or (isinstance(x, float) and math.isnan(x)) else (
        f"−${abs(x):,.2f}" if x < 0 else f"${x:,.2f}")


def verdict_badge(v):
    icon, words, cls = VERDICT.get(v, VERDICT["unproven"])
    return f'<span class="badge {cls}"><span class="ico" aria-hidden="true">{icon}</span>{words}</span>'


# --- charts ----------------------------------------------------------------------

def _bar(x, y0, w, h, r=3):
    """Bar with rounded top corners, anchored to the baseline."""
    if h <= 0:
        return ""
    r = min(r, w / 2, h)
    return (f'M{x:.1f},{y0:.1f}V{y0 - h + r:.1f}Q{x:.1f},{y0 - h:.1f} {x + r:.1f},{y0 - h:.1f}'
            f'H{x + w - r:.1f}Q{x + w:.1f},{y0 - h:.1f} {x + w:.1f},{y0 - h + r:.1f}V{y0:.1f}Z')


def bracket_chart(panel, picked):
    """Paired bars per bracket: market price (blue) vs AI probability (orange)."""
    b = panel["brackets"]
    W, H, top, bottom = 320, 132, 14, 34
    base = H - bottom
    vals = [x["model"] for x in b] + [x["market"] or 0 for x in b]
    ymax = max(0.4, max(vals) * 1.1)
    scale = (base - top) / ymax
    gw = W / len(b)
    bw = min(16, (gw - 10) / 2)
    parts = [f'<line class="grid" x1="0" x2="{W}" y1="{base - 0.25 * scale:.1f}" y2="{base - 0.25 * scale:.1f}"/>']
    tick = f'<text class="tick halo" x="0" y="{base - 0.25 * scale - 3:.1f}">25%</text>'
    for i, br in enumerate(b):
        cx = gw * i + gw / 2
        mk = br["market"]
        tip = f'{br["label"]}: AI {pct(br["model"])}, market {pct(mk) if mk is not None else "no quote"}'
        if mk is not None:
            parts.append(f'<path class="s-market" d="{_bar(cx - bw - 1, base, bw, mk * scale)}"/>')
        parts.append(f'<path class="s-model" d="{_bar(cx + 1, base, bw, br["model"] * scale)}"/>')
        parts.append(f'<text class="xlab" x="{cx:.1f}" y="{base + 14}" text-anchor="middle">{esc(br["label"])}</text>')
        if br["label"] in picked:
            parts.append(f'<text class="pickmark" x="{cx:.1f}" y="{base + 28}" text-anchor="middle">'
                         f'{esc(picked[br["label"]])}</text>')
        parts.append(f'<rect class="hit" x="{gw * i:.1f}" y="0" width="{gw:.1f}" height="{base}" '
                     f'tabindex="0" data-tip="{esc(tip)}"/>')
    parts.append(f'<line class="axis" x1="0" x2="{W}" y1="{base}" y2="{base}"/>')
    parts.append(tick)   # drawn last so bars never hide it
    return (f'<svg viewBox="0 0 {W} {H}" class="bars" role="img" '
            f'aria-label="AI probability versus market price by bracket">{"".join(parts)}</svg>')


def equity_chart(series):
    """Cumulative P&L lines on one dollar axis, with a hover crosshair."""
    series = [(name, cls, pts) for name, cls, pts in series if pts]
    if not series:
        return '<p class="empty">No completed bets to chart yet.</p>'
    W, H, L, R, T, B = 760, 230, 52, 12, 12, 26
    dates = sorted({d for _, _, pts in series for d, _ in pts})
    t0, t1 = pd.Timestamp(dates[0]), pd.Timestamp(dates[-1])
    span = max((t1 - t0).days, 1)
    ys = [v for _, _, pts in series for _, v in pts] + [0]
    lo, hi = min(ys), max(ys)
    pad = (hi - lo) * 0.08 or 1
    lo, hi = lo - pad, hi + pad

    def x(d):
        return L + (pd.Timestamp(d) - t0).days / span * (W - L - R)

    def y(v):
        return T + (hi - v) / (hi - lo) * (H - T - B)

    step = 10 ** math.floor(math.log10(max(hi - lo, 1) / 3))
    step *= next(m for m in (1, 2, 5, 10) if (hi - lo) / (step * m) <= 5)
    parts = []
    v = math.ceil(lo / step) * step
    while v <= hi:
        parts.append(f'<line class="grid" x1="{L}" x2="{W - R}" y1="{y(v):.1f}" y2="{y(v):.1f}"/>'
                     f'<text class="tick" x="{L - 6}" y="{y(v) + 4:.1f}" text-anchor="end">{money(v)}</text>')
        v += step
    parts.append(f'<line class="axis" x1="{L}" x2="{W - R}" y1="{y(0):.1f}" y2="{y(0):.1f}"/>')
    for m in pd.date_range(t0, t1, freq="MS"):
        if (m - t0).days > 10:
            parts.append(f'<text class="tick" x="{x(m):.1f}" y="{H - 6}" text-anchor="middle">{m:%b %y}</text>')
    for name, cls, pts in series:
        d = " ".join(f'{"M" if i == 0 else "L"}{x(dd):.1f},{y(vv):.1f}' for i, (dd, vv) in enumerate(pts))
        parts.append(f'<path class="line {cls}" d="{d}"/>')
        last_d, last_v = pts[-1]
        parts.append(f'<circle class="dot {cls}" cx="{x(last_d):.1f}" cy="{y(last_v):.1f}" r="4"/>')
    data = {name: [[d, v] for d, v in pts] for name, _, pts in series}
    meta = {"L": L, "R": R, "W": W, "t0": f"{t0:%Y-%m-%d}", "span": span}
    return (f'<div class="eq-wrap"><svg viewBox="0 0 {W} {H}" class="equity" role="img" '
            f'aria-label="Cumulative profit and loss">{"".join(parts)}'
            f'<line class="xhair" x1="0" x2="0" y1="{T}" y2="{H - B}" visibility="hidden"/>'
            f'<rect class="eq-hit" x="{L}" y="{T}" width="{W - L - R}" height="{H - T - B}"/></svg>'
            f'<script type="application/json" class="eq-data">{json.dumps({"series": data, "meta": meta})}</script></div>')


# --- page sections (shared by the dashboard and the website) ---------------------

ORDER = {"edge": 0, "unproven": 1, "no edge": 2}


def eastern_now():
    return pd.Timestamp.now(tz="America/New_York")


def sort_picks(picks):
    picks = picks.copy() if picks is not None else pd.DataFrame()
    if not picks.empty:
        picks["vrank"] = picks["verdict"].map(ORDER).fillna(1)
        picks = picks.sort_values(["vrank", "ev"], ascending=[True, False])
    return picks


def header_html(panels, picks, generated, title="High-temp picks"):
    open_cities = [p for p in panels if p["brackets"]]
    n = len(picks)
    lede = (f"{n} {'bet' if n == 1 else 'bets'} cleared the filters across "
            f"{len(open_cities)} cities with open markets." if n else
            f"No bets cleared the filters today across {len(open_cities)} cities with open markets.")
    return (f'<header class="top"><h1>{esc(title)} <span class="date">{generated:%A, %B %-d}</span></h1>'
            f'<p class="lede">{esc(lede)} Updated {generated:%-I:%M %p} ET.</p></header>')


def notices_html(summary, demo):
    out = []
    if demo:
        out.append('<p class="notice demo">Demo data. Every number on this page is simulated. '
                   'Run <code>python hightemp.py setup</code> to load real markets.</p>')
    any_edge = summary and any(c["verdict"] == "edge" for c in summary.get("cities", {}).values())
    if not any_edge:
        out.append('<p class="notice">No city has a proven edge in the backtest yet, so treat every '
                   'pick as a paper trade.</p>')
    return "".join(out)


def picks_section(picks, record_href="#record"):
    out = ['<section><h2>Today’s bets</h2>']
    if picks.empty:
        out.append(f'<p class="empty">Nothing today. The AI recommends a bracket only when its expected '
                   f'profit beats {config.EDGE_THRESHOLD * 100:.0f}¢ a contract after fees, the spread is '
                   f'at most {config.MAX_SPREAD * 100:.0f}¢, and the price is between '
                   f'{config.MIN_PRICE * 100:.0f}¢ and {config.MAX_PRICE * 100:.0f}¢.</p>')
    else:
        rows = "".join(
            f'<tr><td class="city">{esc(r.city)}</td><td class="num bracket">{esc(r.label)}</td>'
            f'<td class="bet"><span class="side {r.side.lower()}">Buy {r.side}</span></td>'
            f'<td class="num" data-label="Price">{r.price * 100:.0f}¢</td>'
            f'<td class="num" data-label="AI chance">{pct(r.prob)}</td>'
            f'<td class="num strong" data-label="Edge">+{r.ev * 100:.1f}¢</td>'
            f'<td class="num" data-label="Stake">{pct(r.stake_pct, 1)}</td>'
            f'<td class="rec">{verdict_badge(r.verdict)}</td></tr>' for r in picks.itertuples())
        out.append('<div class="scroll picks-wrap"><table class="picks"><thead><tr><th>City</th><th>Bracket</th>'
                   '<th>Bet</th><th class="num">Price</th><th class="num">AI chance</th>'
                   '<th class="num">Edge</th><th class="num">Stake</th><th>Track record</th></tr></thead>'
                   f'<tbody>{rows}</tbody></table></div>'
                   f'<p class="fine">Edge is expected profit per $1 contract after Kalshi’s taker fee. '
                   f'Stake is a share of bankroll ({config.KELLY_FRACTION:g}× Kelly, capped at '
                   f'{config.MAX_STAKE_PCT:.0%}). Every pick is logged at these prices and scored in the '
                   f'<a href="{record_href}">track record</a> once its market settles.</p>')
    out.append('</section>')
    return "".join(out)


def boards_section(panels, picks):
    picked = {}
    for r in picks.itertuples():
        picked.setdefault(r.series, {})[r.label] = f"Buy {r.side}"
    has_pick = set(picked)
    boards = sorted(panels, key=lambda p: (p["series"] not in has_pick, not p["brackets"],
                                           ORDER.get(p["verdict"], 1), p["city"]))
    out = ['<section><div class="sec-head"><h2>Forecast vs market</h2>'
           '<ul class="legend"><li><span class="key s-market-bg"></span>Market price</li>'
           '<li><span class="key s-model-bg"></span>AI probability</li>'
           '</ul></div><div class="boards"><div class="boards-grid">']
    for p in boards:
        head = f'<div class="b-head"><h3>{esc(p["city"])}</h3>{verdict_badge(p["verdict"])}</div>'
        temp = ""
        if "mu" in p:
            lo, hi = p["mu"] - 1.28 * p["sigma"], p["mu"] + 1.28 * p["sigma"]
            mkt = f'<span class="vs">market {p["market_mean"]:.0f}°</span>' if "market_mean" in p else ""
            temp = (f'<div class="temp"><span class="deg">{p["mu"]:.0f}°</span>'
                    f'<span class="range">likely {lo:.0f}–{hi:.0f}°F {mkt}</span></div>')
        chart = bracket_chart(p, picked.get(p["series"], {})) if p["brackets"] else \
            f'<p class="empty small">{esc(p["note"])}</p>'
        cls = "board picked" if p["series"] in has_pick else "board"
        out.append(f'<article class="{cls}">{head}{temp}{chart}'
                   f'<p class="station">Settles on {esc(p["station"])}</p></article>')
    out.append('</div></div></section>')
    return "".join(out)


def record_section(log, summary, heading="Track record", anchor="record"):
    out = [f'<section id="{anchor}"><h2>{esc(heading)}</h2>']
    settled = log[log["won"].notna()] if log is not None and not log.empty else pd.DataFrame()
    pending = 0 if log is None or log.empty else int(log["won"].isna().sum())
    if not settled.empty:
        out.append(f'<p class="stats"><span><b>{len(settled)}</b> logged bets settled</span>'
                   f'<span><b>{pct(settled["won"].mean())}</b> won</span>'
                   f'<span><b>{money(settled["pnl"].sum())}</b> paper profit on $1 contracts</span>'
                   f'<span><b>{pending}</b> waiting to settle</span></p>')
    else:
        out.append(f'<p class="stats"><span>No logged bets have settled yet'
                   f'{f", {pending} waiting" if pending else ""}.</span></p>')
    series = []
    if summary and summary.get("equity"):
        series.append(("Backtest", "s-market", summary["equity"]))
    if not settled.empty:
        cum = settled.groupby("date")["pnl"].sum().sort_index().cumsum()
        series.append(("Logged picks", "s-model", [[f"{d:%Y-%m-%d}", round(float(v), 2)] for d, v in cum.items()]))
    if series:
        keys = "".join(f'<li><span class="key {c}-bg"></span>{esc(nm)}</li>' for nm, c, _ in series)
        out.append(f'<div class="sec-head"><h3>Cumulative profit, $1 per bet</h3>'
                   f'<ul class="legend">{keys}</ul></div>{equity_chart(series)}')
    out.append(backtest_table(summary))
    out.append('</section>')
    return "".join(out)


def backtest_table(summary):
    if not summary:
        return '<p class="empty">No backtest yet. Run <code>python hightemp.py backtest</code>.</p>'
    o = summary["overall"]

    def ci_text(ci):
        return f"{money(ci[0])} to {money(ci[1])}" if ci else "–"

    trs = []
    for _, c in sorted(summary["cities"].items(),
                       key=lambda kv: (ORDER.get(kv[1]["verdict"], 1), -(kv[1].get("pnl") or 0))):
        trs.append(f'<tr><td class="city">{esc(c["city"])}</td><td>{verdict_badge(c["verdict"])}</td>'
                   f'<td class="num">{c.get("rmse_model", float("nan")):.1f}°</td>'
                   f'<td class="num">{c.get("bss_blend", float("nan")):+.3f}</td>'
                   f'<td class="num">{c["trades"]}</td><td class="num">{pct(c.get("win_rate"))}</td>'
                   f'<td class="num strong">{money(c.get("pnl", 0.0))}</td>'
                   f'<td class="num">{ci_text(c.get("ci90"))}</td></tr>')
    trs.append(f'<tr class="total"><td class="city">All cities</td><td></td>'
               f'<td class="num">{o.get("rmse_model", float("nan")):.1f}°</td>'
               f'<td class="num">{o.get("bss_blend", float("nan")):+.3f}</td><td class="num">{o["trades"]}</td>'
               f'<td class="num">{pct(o.get("win_rate"))}</td><td class="num strong">{money(o.get("pnl", 0.0))}</td>'
               f'<td class="num">{ci_text(o.get("ci90"))}</td></tr>')
    return (f'<h3 class="sub">Backtest by city, {summary["period"][0]} to {summary["period"][1]}</h3>'
            '<p class="fine">Each month was predicted using only earlier data. Skill above 0 means '
            'more accurate than the market price. The range is a 90% interval on total profit; '
            'if it includes $0 the result could be luck.</p>'
            '<div class="scroll"><table class="cities"><thead><tr><th>City</th><th>Verdict</th>'
            '<th class="num">Forecast error</th><th class="num">Skill vs market</th><th class="num">Bets</th>'
            '<th class="num">Won</th><th class="num">Profit</th><th class="num">90% range</th></tr></thead>'
            f'<tbody>{"".join(trs)}</tbody></table></div>')


FOOTER = ('<footer><p>Four weather models’ forecasts for each settlement station are bias-corrected '
          'into a temperature distribution, which prices every bracket. That price is blended with the '
          'market’s, weighted by how well each predicted past outcomes. Weather data from Open-Meteo '
          '(CC BY 4.0) and the NWS Daily Climate Report. A research tool, not financial advice.</p></footer>')


def page(body, title, head_extra=""):
    return (PAGE.replace("{{HEAD}}", head_extra).replace("{{TITLE}}", esc(title))
            .replace("{{BODY}}", body))


def render(panels, picks, log, summary, demo=False, generated=None):
    """The single-page local dashboard."""
    generated = generated or eastern_now()
    picks = sort_picks(picks)
    body = "".join([header_html(panels, picks, generated), notices_html(summary, demo),
                    picks_section(picks), boards_section(panels, picks),
                    record_section(log, summary), FOOTER])
    return page(body, "High-temp picks")


PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{{TITLE}}</title>
{{HEAD}}
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Barlow:wght@400;500;600&family=Barlow+Condensed:wght@500;600&display=swap">
<style>
:root{color-scheme:light;
 --plane:#eef1f4;--surface:#fbfcfd;--ink:#13202f;--ink-2:#4a5566;--muted:#7d8794;
 --hair:#dde2e8;--axis:#b9c1cb;--grid:#e6e9ee;
 --model:#eb6834;--market:#2a78d6;
 --good:#0ca30c;--good-ink:#006300;--warn:#c98500;--bad:#d03b3b;
 --tint:#fff4ee;--demo:#fff7db}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){color-scheme:dark;
 --plane:#0d1117;--surface:#151b23;--ink:#e8edf3;--ink-2:#b3bdc9;--muted:#7d8794;
 --hair:#253040;--axis:#3a4656;--grid:#1f2833;--model:#d95926;--market:#3987e5;
 --good-ink:#0ca30c;--warn:#fab219;--tint:#2a1a12;--demo:#2b2510}}
:root[data-theme="dark"]{color-scheme:dark;
 --plane:#0d1117;--surface:#151b23;--ink:#e8edf3;--ink-2:#b3bdc9;--muted:#7d8794;
 --hair:#253040;--axis:#3a4656;--grid:#1f2833;--model:#d95926;--market:#3987e5;
 --good-ink:#0ca30c;--warn:#fab219;--tint:#2a1a12;--demo:#2b2510}
*{box-sizing:border-box}
body{margin:0;background:var(--plane);color:var(--ink);font:16px/1.5 Barlow,system-ui,sans-serif;
 -webkit-font-smoothing:antialiased}
main{max-width:1120px;margin:0 auto;padding:40px 16px 64px}
h1{font:600 clamp(2.4rem,6vw,3.6rem)/1 "Barlow Condensed",Barlow,sans-serif;margin:0;letter-spacing:-.01em}
h1 .date{display:block;font:500 1.05rem/1.4 Barlow,sans-serif;color:var(--ink-2);margin-top:10px;letter-spacing:0}
h2{font:600 1.7rem/1.1 "Barlow Condensed",Barlow,sans-serif;margin:0 0 14px}
h3{font-size:1rem;font-weight:600;margin:0}
h3.sub{margin:28px 0 4px}
.lede{color:var(--ink-2);max-width:62ch;margin:12px 0 0}
section{margin-top:44px}
.notice{margin:18px 0 0;padding:10px 14px;border-left:3px solid var(--warn);background:var(--surface);
 color:var(--ink-2);max-width:78ch}
.notice.demo{border-color:var(--model);background:var(--demo);color:var(--ink)}
code{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:.9em}
.scroll{overflow-x:auto;background:var(--surface);border:1px solid var(--hair);border-radius:10px}
table{border-collapse:collapse;width:100%;min-width:640px}
th,td{padding:11px 14px;text-align:left;border-bottom:1px solid var(--hair);white-space:nowrap}
th{font-size:.82rem;font-weight:500;color:var(--muted)}
tbody tr:last-child td{border-bottom:0}
tr.total td{font-weight:600;border-top:1px solid var(--axis)}
.num{text-align:right;font-variant-numeric:tabular-nums}
.strong{font-weight:600}
td.city{font-weight:500}
.side{display:inline-block;white-space:nowrap;padding:2px 9px;border-radius:999px;font-size:.85rem;font-weight:600;
 border:1px solid var(--axis)}
.side.yes{border-color:var(--model);color:var(--ink)}
.badge{display:inline-flex;align-items:center;gap:6px;font-size:.85rem;color:var(--ink-2);white-space:nowrap}
.badge .ico{display:inline-grid;place-items:center;width:18px;height:18px;border-radius:50%;
 font-size:.7rem;font-weight:700;color:#fff}
.badge.good .ico{background:var(--good)}.badge.warn .ico{background:var(--warn);color:#13202f}
.badge.bad .ico{background:var(--bad)}
.fine{font-size:.86rem;color:var(--muted);max-width:78ch;margin:10px 0 0}
.empty{color:var(--ink-2);background:var(--surface);border:1px dashed var(--axis);border-radius:10px;
 padding:16px;max-width:78ch;margin:0}
.empty.small{font-size:.9rem;padding:12px;margin-top:12px}
.sec-head{display:flex;flex-wrap:wrap;align-items:baseline;justify-content:space-between;gap:8px 20px;margin-bottom:14px}
.sec-head h2,.sec-head h3{margin:0}
.legend{display:flex;gap:16px;list-style:none;margin:0;padding:0;font-size:.88rem;color:var(--ink-2)}
.legend li{display:flex;align-items:center;gap:6px}
.key{width:12px;height:12px;border-radius:3px;display:inline-block}
.s-model-bg{background:var(--model)}.s-market-bg{background:var(--market)}
.boards{background:var(--surface);border:1px solid var(--hair);border-radius:10px;overflow:hidden}
.boards-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(290px,1fr));margin:0 -1px -1px 0}
.board{background:var(--surface);padding:16px 16px 12px;border-right:1px solid var(--hair);
 border-bottom:1px solid var(--hair)}
.board.picked{background:linear-gradient(var(--tint),var(--surface) 70%)}
.b-head{display:flex;justify-content:space-between;align-items:center;gap:8px}
.temp{display:flex;align-items:baseline;gap:10px;margin:6px 0 2px}
.deg{font:600 3.1rem/1 "Barlow Condensed",Barlow,sans-serif;font-variant-numeric:tabular-nums}
.range{color:var(--ink-2);font-size:.88rem}
.vs{color:var(--muted);margin-left:4px}
.final{margin:0 0 4px;font-size:.88rem;color:var(--ink-2)}
.station{font-size:.78rem;color:var(--muted);margin:2px 0 0}
svg{display:block;width:100%;height:auto;overflow:visible}
svg .grid{stroke:var(--grid);stroke-width:1}
svg .axis{stroke:var(--axis);stroke-width:1}
svg .tick{fill:var(--muted);font-size:10px;font-family:Barlow,sans-serif}
svg .halo{paint-order:stroke;stroke:var(--surface);stroke-width:3px}
svg .xlab{fill:var(--ink-2);font-size:10.5px;font-family:Barlow,sans-serif;font-variant-numeric:tabular-nums}
svg .pickmark{fill:var(--ink);font-size:10px;font-weight:600;font-family:Barlow,sans-serif}
svg .s-model{fill:var(--model)} svg .s-market{fill:var(--market)}
svg .hit{fill:transparent;cursor:default;outline:none}
svg .hit:hover,svg .hit:focus-visible{fill:var(--ink);fill-opacity:.05}
svg .line{fill:none;stroke-width:2;stroke-linejoin:round;stroke-linecap:round}
svg .line.s-model{stroke:var(--model)} svg .line.s-market{stroke:var(--market)}
svg .dot{stroke:var(--surface);stroke-width:2}
svg .dot.s-model{fill:var(--model)} svg .dot.s-market{fill:var(--market)}
svg .xhair{stroke:var(--axis);stroke-width:1}
svg .eq-hit{fill:transparent}
.eq-wrap{background:var(--surface);border:1px solid var(--hair);border-radius:10px;padding:14px 12px 8px}
.stats{display:flex;flex-wrap:wrap;gap:6px 28px;margin:0 0 22px;color:var(--ink-2)}
.stats b{font:600 1.35rem/1 "Barlow Condensed",Barlow,sans-serif;color:var(--ink);margin-right:4px}
.site-nav{display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;gap:12px 24px;
 padding-bottom:16px;margin-bottom:36px;border-bottom:1px solid var(--hair)}
.brand{display:flex;align-items:center;gap:10px;color:var(--ink);text-decoration:none;
 font:600 1.4rem/1 "Barlow Condensed",Barlow,sans-serif}
.brand svg{width:28px;height:28px;flex:none}
.brand .mk-bg{fill:var(--ink)}.brand .mk-stem{fill:var(--surface)}
.site-nav ul{display:flex;flex-wrap:wrap;gap:4px 22px;list-style:none;margin:0;padding:0}
.site-nav a.nav{display:inline-block;color:var(--ink-2);text-decoration:none;font-weight:500;padding:6px 0;
 border-bottom:2px solid transparent}
.site-nav a.nav:hover{color:var(--ink)}
.site-nav a.nav[aria-current="page"]{color:var(--ink);border-bottom-color:var(--model)}
a{color:var(--market);text-underline-offset:2px}
a:focus-visible,.hit:focus-visible{outline:2px solid var(--market);outline-offset:2px;border-radius:2px}
.prose{max-width:68ch}
.prose h2{margin:36px 0 10px}
.prose p,.prose li{color:var(--ink-2)}
.prose li{margin:4px 0}
.prose strong{color:var(--ink);font-weight:600}
#tip{position:fixed;pointer-events:none;z-index:10;background:var(--ink);color:var(--plane);
 font-size:.82rem;padding:6px 9px;border-radius:6px;max-width:260px;opacity:0;transition:opacity .08s}
footer{margin-top:56px;padding-top:18px;border-top:1px solid var(--hair);color:var(--muted);
 font-size:.86rem;max-width:78ch}
@media (max-width:640px){
 .scroll.picks-wrap{overflow:visible}
 table.picks{min-width:0}
 table.picks thead{display:none}
 table.picks tr{display:grid;grid-template-columns:repeat(4,1fr);gap:6px 10px;padding:14px;
  border-bottom:1px solid var(--hair)}
 table.picks tbody tr:last-child{border-bottom:0}
 table.picks td{padding:0;border:0;text-align:left;white-space:normal}
 table.picks td.city{grid-column:1/3;font-size:1.05rem}
 table.picks td.bracket{grid-column:3/4;text-align:right}
 table.picks td.bet{grid-column:4/5;text-align:right}
 table.picks td[data-label]::before{content:attr(data-label);display:block;font-size:.75rem;
  font-weight:400;color:var(--muted)}
 table.picks td.rec{grid-column:1/-1}}
@media (prefers-reduced-motion:reduce){#tip{transition:none}}
</style></head>
<body><main>
{{BODY}}
</main><div id="tip" role="tooltip"></div>
<script>
const tip=document.getElementById('tip');
function show(text,x,y){tip.textContent=text;tip.style.opacity=1;
 const w=tip.offsetWidth;tip.style.left=Math.min(x+14,innerWidth-w-8)+'px';tip.style.top=(y+14)+'px';}
function hide(){tip.style.opacity=0;}
document.querySelectorAll('[data-tip]').forEach(el=>{
 el.addEventListener('mousemove',e=>show(el.dataset.tip,e.clientX,e.clientY));
 el.addEventListener('mouseleave',hide);
 el.addEventListener('focus',()=>{const r=el.getBoundingClientRect();show(el.dataset.tip,r.left,r.top);});
 el.addEventListener('blur',hide);});
document.querySelectorAll('.eq-wrap').forEach(wrap=>{
 const svg=wrap.querySelector('svg'),hit=wrap.querySelector('.eq-hit'),xh=wrap.querySelector('.xhair');
 const {series,meta}=JSON.parse(wrap.querySelector('.eq-data').textContent);
 const t0=new Date(meta.t0),day=864e5;
 const fmt=v=>(v<0?'−$':'$')+Math.abs(v).toFixed(2);
 hit.addEventListener('mousemove',e=>{
  const p=svg.createSVGPoint();p.x=e.clientX;p.y=e.clientY;const q=p.matrixTransform(svg.getScreenCTM().inverse());
  const days=(q.x-meta.L)/(meta.W-meta.L-meta.R)*meta.span;const target=new Date(+t0+days*day);
  const lines=[];let label='';
  for(const [name,pts] of Object.entries(series)){let best=null;
   for(const [d,v] of pts){if(new Date(d)<=target)best=[d,v];}
   if(best){lines.push(name+': '+fmt(best[1]));label=label||best[0];}}
  if(!lines.length){hide();return;}
  xh.setAttribute('x1',q.x);xh.setAttribute('x2',q.x);xh.setAttribute('visibility','visible');
  show(target.toISOString().slice(0,10)+'  '+lines.join(', '),e.clientX,e.clientY);});
 hit.addEventListener('mouseleave',()=>{xh.setAttribute('visibility','hidden');hide();});
});
</script></body></html>"""
