"""Render results as terminal text, JSON, or a standalone HTML page."""

from __future__ import annotations

import html
import json
from collections import defaultdict
from dataclasses import asdict
from datetime import date

from .config import Prefs
from .models import Result, Status

SECTIONS = [
    ("free", "Watch free"),
    ("mine", "On your services"),
    ("subscription", "Needs a subscription you don't have"),
    ("rent", "Pay per view (rent or buy)"),
    ("in_theaters", "In theaters"),
    ("unreleased", "Not released yet"),
    ("not_available", "Released, but nowhere to watch"),
    ("not_found", "Couldn't match on TMDB"),
]


def section_of(r: Result) -> str:
    if r.status != Status.AVAILABLE:
        return r.status.value
    best = r.best
    if best.tier == 0:
        return "free"
    if best.subscribed:
        return "mine"
    if best.tier == 1:
        return "subscription"
    return "rent"


def group(results: list[Result]) -> dict[str, list[Result]]:
    groups: dict[str, list[Result]] = defaultdict(list)
    for r in results:
        groups[section_of(r)].append(r)
    for key, items in groups.items():
        if key == "unreleased":
            items.sort(key=lambda r: (r.expected_date or "9999", r.film.title.lower()))
        else:
            items.sort(key=lambda r: r.film.title.lower())
    return groups


def subscription_value(results: list[Result], prefs: Prefs) -> list[tuple[str, float, int, list[str]]]:
    """For services you don't have: how many watchlist films would they unlock that you can't already watch free
    or on a service you pay for?"""
    unlock: dict[str, list[str]] = defaultdict(list)
    names: dict[str, str] = {}
    for r in results:
        if r.status != Status.AVAILABLE or r.best.tier == 0 or r.best.subscribed:
            continue
        for o in r.offers:
            if o.tier == 1 and not o.subscribed:
                unlock[o.key].append(r.film.label)
                names.setdefault(o.key, o.provider)
    rows = []
    for key, films in unlock.items():
        price = prefs.prices.get(key, prefs.unknown_subscription_price)
        rows.append((names[key], price, len(films), sorted(films)))
    rows.sort(key=lambda row: (-row[2], row[1]))
    return rows


def _alts(r: Result, limit: int = 4) -> str:
    alts = r.offers[1:]
    if any(o.kind != "buy" for o in r.offers):
        alts = [o for o in alts if o.kind != "buy"]  # buying is only worth listing when it's the only option
    out = [f"{o.provider} ({o.cost_label.lower()})" for o in alts]
    return ", ".join(out[:limit]) + (" ..." if len(out) > limit else "")


def render_text(results: list[Result], prefs: Prefs) -> str:
    groups = group(results)
    lines = [f"Letterboxd watchlist availability ({prefs.region}) - {date.today().isoformat()}", ""]

    changed = [r for r in results if r.changes]
    if changed:
        lines.append("CHANGES SINCE LAST RUN")
        for r in sorted(changed, key=lambda r: r.film.title.lower()):
            for c in r.changes:
                lines.append(f"  * {r.film.label}: {c}")
        lines.append("")

    for key, title in SECTIONS:
        items = groups.get(key)
        if not items:
            continue
        lines.append(f"{title.upper()} ({len(items)})")
        for r in items:
            if r.best:
                line = f"  {r.film.label} -> {r.best.provider} [{r.best.cost_label}]"
                alts = _alts(r)
                if alts:
                    line += f"\n      also: {alts}"
            else:
                line = f"  {r.film.label}" + (f" - {r.note}" if r.note else "")
            lines.append(line)
        lines.append("")

    value = subscription_value(results, prefs)
    if value:
        lines.append("SUBSCRIPTIONS WORTH CONSIDERING (films you can't already watch free or on your services)")
        for name, price, count, _ in value[:8]:
            lines.append(f"  {name}: {count} film(s) for ~${price:.2f}/mo (~${price / count:.2f} per film)")
        lines.append("")

    lines.append("Availability data: JustWatch via TMDB. Prices are estimates; edit them in config.toml.")
    return "\n".join(lines)


def render_json(results: list[Result]) -> str:
    out = []
    for r in results:
        d = asdict(r)
        d["status"] = r.status.value
        d["section"] = section_of(r)
        out.append(d)
    return json.dumps(out, indent=2)


def render_html(results: list[Result], prefs: Prefs) -> str:
    e = html.escape
    groups = group(results)
    parts = []

    changed = [r for r in results if r.changes]
    if changed:
        items = "".join(
            f"<li><b>{e(r.film.label)}</b>: {e('; '.join(r.changes))}</li>"
            for r in sorted(changed, key=lambda r: r.film.title.lower())
        )
        parts.append(f'<section class="changes"><h2>Changes since last run</h2><ul>{items}</ul></section>')

    nav = []
    for key, title in SECTIONS:
        items = groups.get(key)
        if not items:
            continue
        nav.append(f'<a href="#{key}">{e(title)} <span>{len(items)}</span></a>')
        cards = []
        for r in items:
            film = e(r.film.label)
            if r.film.slug:
                film = f'<a href="https://letterboxd.com/film/{e(r.film.slug)}/">{film}</a>'
            if r.best:
                body = f'<div class="best"><b>{e(r.best.provider)}</b> <span class="tag t{r.best.tier}">{e(r.best.cost_label)}</span></div>'
                alts = _alts(r, limit=6)
                if alts:
                    body += f'<div class="alts">Also: {e(alts)}</div>'
                if r.note:
                    body += f'<div class="note">{e(r.note)}</div>'
            else:
                body = f'<div class="note">{e(r.note)}</div>' if r.note else ""
            if r.changes:
                body += f'<div class="chg">{e("; ".join(r.changes))}</div>'
            if r.link:
                body += f'<div class="src"><a href="{e(r.link)}">All options</a></div>'
            cards.append(f'<li class="card"><div class="title">{film}</div>{body}</li>')
        parts.append(f'<section id="{key}"><h2>{e(title)} <span>{len(items)}</span></h2><ul class="grid">{"".join(cards)}</ul></section>')

    value = subscription_value(results, prefs)
    if value:
        rows = "".join(
            f"<tr><td>{e(n)}</td><td>{c}</td><td>${p:.2f}</td><td>${p / c:.2f}</td><td>{e(', '.join(f[:6]))}{' ...' if len(f) > 6 else ''}</td></tr>"
            for n, p, c, f in value[:10]
        )
        parts.append(
            '<section id="value"><h2>Subscriptions worth considering</h2>'
            "<p>Services you don't have, ranked by how many watchlist films they'd unlock that you can't already watch free or on a service you pay for.</p>"
            "<table><thead><tr><th>Service</th><th>Films</th><th>~Monthly</th><th>~Per film</th><th>Includes</th></tr></thead>"
            f"<tbody>{rows}</tbody></table></section>"
        )

    return HTML_TEMPLATE.format(
        region=e(prefs.region),
        today=date.today().isoformat(),
        total=len(results),
        nav="".join(nav),
        body="".join(parts),
    )


HTML_TEMPLATE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Watchlist Streaming</title>
<style>
:root {{ --bg:#f7f7f5; --fg:#1d1f21; --muted:#666; --card:#fff; --line:#e3e3df; --accent:#00a35c;
  --free:#0b7a43; --sub:#2457c5; --rent:#9a5b00; --chg:#8a2be2; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg:#14181c; --fg:#e8e8e8; --muted:#9aa3ab; --card:#1e252c;
  --line:#2c3440; --accent:#40bc6e; --free:#4fd18b; --sub:#7aa2ff; --rent:#f0b45a; --chg:#c79bff; }} }}
* {{ box-sizing:border-box; }}
body {{ margin:0; background:var(--bg); color:var(--fg); font:15px/1.45 system-ui,-apple-system,Segoe UI,sans-serif; }}
main {{ max-width:1100px; margin:0 auto; padding:24px 16px 48px; }}
h1 {{ margin:0 0 4px; font-size:24px; }} h2 {{ font-size:18px; margin:32px 0 12px; }}
h2 span, nav span {{ color:var(--muted); font-weight:400; }}
.meta {{ color:var(--muted); margin:0 0 16px; }}
nav {{ display:flex; flex-wrap:wrap; gap:8px; }}
nav a {{ text-decoration:none; color:var(--fg); border:1px solid var(--line); background:var(--card); border-radius:999px; padding:4px 12px; font-size:13px; }}
a {{ color:var(--accent); }}
ul {{ padding:0; }} .grid {{ list-style:none; display:grid; grid-template-columns:repeat(auto-fill,minmax(260px,1fr)); gap:10px; }}
.card {{ background:var(--card); border:1px solid var(--line); border-radius:8px; padding:12px; }}
.title {{ font-weight:600; margin-bottom:6px; }} .title a {{ color:var(--fg); text-decoration:none; }}
.tag {{ font-size:12px; border-radius:4px; padding:1px 6px; border:1px solid currentColor; white-space:nowrap; }}
.tag.t0 {{ color:var(--free); }} .tag.t1 {{ color:var(--sub); }} .tag.t2,.tag.t3 {{ color:var(--rent); }}
.alts,.note,.src {{ color:var(--muted); font-size:13px; margin-top:4px; }}
.chg {{ color:var(--chg); font-size:13px; margin-top:4px; }}
.changes {{ border-left:3px solid var(--chg); padding-left:12px; }} .changes li {{ list-style:none; margin:4px 0; }}
table {{ border-collapse:collapse; width:100%; font-size:14px; display:block; overflow-x:auto; }}
th,td {{ text-align:left; padding:6px 10px; border-bottom:1px solid var(--line); vertical-align:top; }}
td:nth-child(2),td:nth-child(3),td:nth-child(4) {{ font-variant-numeric:tabular-nums; white-space:nowrap; }}
footer {{ color:var(--muted); font-size:12px; margin-top:40px; }}
</style></head><body><main>
<h1>Where to watch your watchlist</h1>
<p class="meta">{total} films &middot; region {region} &middot; checked {today}</p>
<nav>{nav}</nav>
{body}
<footer>Availability data from JustWatch via TMDB. This product uses the TMDB API but is not endorsed or certified by TMDB.
Subscription prices are estimates from config.toml. Availability changes often &mdash; re-run to refresh.</footer>
</main></body></html>
"""
