"""Read a Letterboxd watchlist, either by scraping the public page or from a CSV export.

Letterboxd has no public API. Scraping works for public watchlists but can be
blocked by Letterboxd's bot protection; the CSV export (Settings -> Data ->
Export Your Data -> watchlist.csv) always works.
"""

from __future__ import annotations

import csv
import html
import re
from html.parser import HTMLParser
from typing import Callable

from . import http
from .cache import Cache
from .models import Film

BASE = "https://letterboxd.com"
MAX_PAGES = 200

Fetcher = Callable[[str], str]


class _PosterParser(HTMLParser):
    """Collect films from watchlist poster markup.

    Letterboxd has shipped at least two variants:
      * older:  <div class="film-poster" data-film-slug="..." data-film-name="..."> <img alt="Title">
      * newer:  <div data-component-class="LazyPoster" data-item-slug="..." data-item-name="Title (2024)">
    Both are handled; anything carrying a film slug counts.
    """

    def __init__(self):
        super().__init__()
        self.films: list[Film] = []
        self._pending: Film | None = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        slug = a.get("data-film-slug") or a.get("data-item-slug")
        if not slug:
            link = a.get("data-target-link") or a.get("data-item-link") or ""
            m = re.fullmatch(r"/film/([^/]+)/?", link)
            slug = m.group(1) if m and "poster" in (a.get("class") or "").lower() else None
        if slug:
            name = a.get("data-item-full-display-name") or a.get("data-item-name") or a.get("data-film-name")
            title, year = _split_title_year(name) if name else (None, None)
            if a.get("data-film-release-year", "").isdigit():
                year = int(a["data-film-release-year"])
            film = Film(title=title or "", year=year, slug=slug)
            self.films.append(film)
            self._pending = film if not title else None
            return
        if tag == "img" and self._pending is not None and a.get("alt"):
            self._pending.title, yr = _split_title_year(a["alt"])
            self._pending.year = self._pending.year or yr
            self._pending = None


def _split_title_year(name: str) -> tuple[str, int | None]:
    name = html.unescape(name).strip()
    m = re.fullmatch(r"(.*\S)\s+\((\d{4})\)", name)
    if m:
        return m.group(1), int(m.group(2))
    return name, None


def parse_watchlist_page(page_html: str) -> list[Film]:
    p = _PosterParser()
    p.feed(page_html)
    films, seen = [], set()
    for f in p.films:
        if f.slug in seen:
            continue
        seen.add(f.slug)
        if not f.title:
            f.title = f.slug.replace("-", " ").title()
        films.append(f)
    return films


def fetch_watchlist(username: str, fetch: Fetcher = http.get) -> list[Film]:
    films: list[Film] = []
    seen: set[str] = set()
    for page in range(1, MAX_PAGES + 1):
        url = f"{BASE}/{username}/watchlist/" + (f"page/{page}/" if page > 1 else "")
        try:
            page_html = fetch(url)
        except http.HTTPError as e:
            if e.status == 404 and page == 1:
                raise SystemExit(f"Letterboxd user '{username}' not found (or watchlist is private).")
            if e.status in (403, 429, 503):
                raise SystemExit(
                    f"Letterboxd blocked the request (HTTP {e.status}). Export your data instead "
                    "(letterboxd.com -> Settings -> Data -> Export Your Data) and run with "
                    "--csv path/to/watchlist.csv"
                )
            raise
        new = [f for f in parse_watchlist_page(page_html) if f.slug not in seen]
        if not new:
            break
        seen.update(f.slug for f in new)
        films.extend(new)
    return films


def read_watchlist_csv(path: str) -> list[Film]:
    """Parse watchlist.csv from a Letterboxd export (columns: Date, Name, Year, Letterboxd URI)."""
    films = []
    with open(path, newline="", encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            name = (row.get("Name") or "").strip()
            if not name:
                continue
            year = (row.get("Year") or "").strip()
            films.append(Film(title=name, year=int(year) if year.isdigit() else None))
    return films


_TMDB_RE = [
    re.compile(r'data-tmdb-id="(\d+)"[^>]*?data-tmdb-type="(movie|tv)"'),
    re.compile(r'data-tmdb-type="(movie|tv)"[^>]*?data-tmdb-id="(\d+)"'),
    re.compile(r'themoviedb\.org/(movie|tv)/(\d+)'),
]


def parse_tmdb_link(film_html: str) -> tuple[int, str] | None:
    """Pull (tmdb_id, media_type) out of a Letterboxd film page."""
    m = _TMDB_RE[0].search(film_html)
    if m:
        return int(m.group(1)), m.group(2)
    for rx in _TMDB_RE[1:]:
        m = rx.search(film_html)
        if m:
            return int(m.group(2)), m.group(1)
    return None


def resolve_tmdb_id(film: Film, cache: Cache, fetch: Fetcher = http.get) -> bool:
    """Fill film.tmdb_id from its Letterboxd page. The mapping never changes, so it's cached forever."""
    if film.tmdb_id or not film.slug:
        return bool(film.tmdb_id)
    key = f"lb-tmdb:{film.slug}"
    hit = cache.get(key)
    if hit is None:
        try:
            found = parse_tmdb_link(fetch(f"{BASE}/film/{film.slug}/"))
        except (http.HTTPError, OSError):
            return False
        if not found:
            return False
        hit = list(found)
        cache.set(key, hit)
    film.tmdb_id, film.media_type = int(hit[0]), hit[1]
    return True
