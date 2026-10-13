"""Render results as terminal text, JSON, or a standalone HTML page."""

from __future__ import annotations

import html
import json
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import date

from .config import Prefs, canonical_provider
from .models import Offer, Result, Status

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


@dataclass
class ServiceValue:
    key: str
    name: str
    price: float
    films: list[str]   # films this service would unlock
    only_here: int     # of those, films no other subscription you lack carries

    @property
    def count(self) -> int:
        return len(self.films)


def _unowned_subs(r: Result) -> list[Offer]:
    return [o for o in r.offers if o.tier == 1 and not o.subscribed]


def subscription_value(results: list[Result], prefs: Prefs) -> list[ServiceValue]:
    """For services you don't have: how many watchlist films would each unlock that you can't already
    watch free or on a service you pay for? Sorted by most films, then most exclusives, then cheapest."""
    rows: dict[str, ServiceValue] = {}
    for r in results:
        if section_of(r) != "subscription":
            continue
        subs = _unowned_subs(r)
        for o in subs:
            row = rows.get(o.key)
            if row is None:
                price = prefs.prices.get(o.key, prefs.unknown_subscription_price)
                row = rows[o.key] = ServiceValue(o.key, o.provider, price, [], 0)
            row.films.append(r.film.label)
            if len(subs) == 1:
                row.only_here += 1
    for row in rows.values():
        row.films.sort(key=str.lower)
    return sorted(rows.values(), key=lambda v: (-v.count, -v.only_here, v.price, v.name))


def _alts(r: Result, limit: int = 4) -> str:
    alts = r.offers[1:]
    if any(o.kind != "buy" for o in r.offers):
        alts = [o for o in alts if o.kind != "buy"]  # buying is only worth listing when it's the only option
    out = [f"{o.provider} ({o.cost_label.lower()})" for o in alts]
    return ", ".join(out[:limit]) + (" ..." if len(out) > limit else "")


def _value_line(v: ServiceValue) -> str:
    only = f", {v.only_here} only here" if v.only_here else ""
    return f"{v.name}: {v.count} film(s){only} - ~${v.price:.2f}/mo"


def render_text(results: list[Result], prefs: Prefs, service: str | None = None) -> str:
    """service: only list films in the subscription section that this service carries."""
    groups = group(results)
    value = subscription_value(results, prefs)
    service_key = canonical_provider(service) if service else None
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
        heading = f"{title.upper()} ({len(items)})"
        if key == "subscription":
            if service_key:
                items = [r for r in items if any(o.key == service_key for o in _unowned_subs(r))]
                heading += f" - showing {len(items)} on {service}"
            lines.append(heading)
            lines.append("  By service (films it would unlock):")
            for v in value:
                lines.append(f"    {_value_line(v)}")
            lines.append("")
        else:
            lines.append(heading)
        if key == "subscription" and service_key and not items:
            lines.append(f"  (none of these films are on {service})")
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

    value = subscription_value(results, prefs)
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
            attrs = ""
            if key == "subscription":
                subs = _unowned_subs(r)
                attrs = f' data-svc="{e("|".join(o.key for o in subs))}"'
                pills = "".join(f'<span class="svc" data-k="{e(o.key)}">{e(o.provider)}</span>' for o in subs)
                rent = [o.provider for o in r.offers if o.kind == "rent"]
                body = f'<div class="svcs">{pills}</div>'
                if rent:
                    body += f'<div class="alts">Or rent: {e(", ".join(rent[:4]))}</div>'
            elif r.best:
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
            cards.append(f'<li class="card"{attrs}><div class="title">{film}</div>{body}</li>')
        intro = ""
        if key == "subscription" and value:
            chips = [f'<button class="chip on" data-k="">All <span>{len(items)}</span></button>']
            for v in value:
                only = f" &middot; {v.only_here} only here" if v.only_here else ""
                chips.append(
                    f'<button class="chip" data-k="{e(v.key)}">{e(v.name)} <span>{v.count}{only} &middot; ~${v.price:.2f}/mo</span></button>'
                )
            intro = (
                '<p class="hint">Films each service would unlock that you can&rsquo;t already watch free or on a service you pay for. '
                '"Only here" means no other subscription carries it. Click a service to filter.</p>'
                f'<div class="chips">{"".join(chips)}</div>'
            )
        parts.append(f'<section id="{key}"><h2>{e(title)} <span>{len(items)}</span></h2>{intro}<ul class="grid">{"".join(cards)}</ul></section>')

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
footer {{ color:var(--muted); font-size:12px; margin-top:40px; }}
.hint {{ color:var(--muted); font-size:13px; margin:0 0 10px; }}
.chips {{ display:flex; flex-wrap:wrap; gap:8px; margin-bottom:14px; }}
.chip {{ font:inherit; font-size:13px; cursor:pointer; color:var(--fg); background:var(--card); border:1px solid var(--line); border-radius:999px; padding:4px 12px; }}
.chip span {{ color:var(--muted); }}
.chip.on {{ border-color:var(--sub); color:var(--sub); box-shadow:inset 0 0 0 1px var(--sub); }}
.svcs {{ display:flex; flex-wrap:wrap; gap:6px; }}
.svc {{ font-size:12px; border:1px solid var(--line); border-radius:4px; padding:1px 6px; }}
.svc.on {{ color:var(--sub); border-color:var(--sub); font-weight:600; }}
.card[hidden] {{ display:none; }}
</style></head><body><main>
<h1>Where to watch your watchlist</h1>
<p class="meta">{total} films &middot; region {region} &middot; checked {today}</p>
<nav>{nav}</nav>
{body}
<footer>Availability data from JustWatch via TMDB. This product uses the TMDB API but is not endorsed or certified by TMDB.
Subscription prices are estimates from config.toml. Availability changes often &mdash; re-run to refresh.</footer>
</main>
<script>
document.querySelectorAll("#subscription .chip").forEach(function (chip) {{
  chip.addEventListener("click", function () {{
    var k = chip.dataset.k;
    document.querySelectorAll("#subscription .chip").forEach(function (c) {{ c.classList.toggle("on", c === chip); }});
    document.querySelectorAll("#subscription .card").forEach(function (card) {{
      card.hidden = !!k && card.dataset.svc.split("|").indexOf(k) < 0;
    }});
    document.querySelectorAll("#subscription .svc").forEach(function (p) {{ p.classList.toggle("on", !!k && p.dataset.k === k); }});
  }});
}});
</script>
</body></html>
"""
