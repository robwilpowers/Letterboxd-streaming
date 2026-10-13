"""TMDB client: film lookup, release dates and watch providers.

TMDB's watch-provider data comes from JustWatch. It says *which* services
carry a film in a region and how (free / ads / flatrate / rent / buy), but not
rental prices or "leaving soon" dates.
"""

from __future__ import annotations

import re
import unicodedata
import urllib.parse
from typing import Callable

from . import http
from .cache import Cache
from .models import Film

BASE = "https://api.themoviedb.org/3"

JsonFetcher = Callable[[str, dict], dict]


class TMDB:
    def __init__(
        self,
        api_key: str | None = None,
        read_token: str | None = None,
        cache: Cache | None = None,
        ttl_hours: float = 12,
        fetch_json: JsonFetcher | None = None,
    ):
        if not api_key and not read_token:
            raise SystemExit(
                "A TMDB API key is required (free: https://www.themoviedb.org/settings/api). "
                "Set TMDB_API_KEY or TMDB_READ_TOKEN, or put tmdb_api_key in config.toml."
            )
        self.api_key = api_key
        self.read_token = read_token
        self.cache = cache
        self.ttl = ttl_hours * 3600
        self._fetch_json = fetch_json or (lambda url, headers: http.get_json(url, headers=headers))

    def _get(self, path: str, params: dict | None = None, max_age: float | None = None):
        params = dict(params or {})
        headers = {"Accept": "application/json"}
        if self.read_token:
            headers["Authorization"] = f"Bearer {self.read_token}"
        cache_key = f"tmdb:{path}?{urllib.parse.urlencode(sorted(params.items()))}"
        if self.cache and max_age != 0:
            hit = self.cache.get(cache_key, max_age=max_age)
            if hit is not None:
                return hit
        if self.api_key and not self.read_token:
            params["api_key"] = self.api_key
        url = f"{BASE}{path}" + (f"?{urllib.parse.urlencode(params)}" if params else "")
        data = self._fetch_json(url, headers)
        if self.cache:
            self.cache.set(cache_key, data)
        return data

    def search(self, film: Film) -> bool:
        """Fill film.tmdb_id by title/year search. Returns True on a match."""
        if film.tmdb_id:
            return True
        attempts = []
        if film.year:
            attempts += [{"primary_release_year": film.year}, {"year": film.year}]
        attempts.append({})
        for extra in attempts:
            # Search results don't go stale like availability does; cache them for 30 days.
            data = self._get("/search/movie", {"query": film.title, **extra}, max_age=30 * 86400)
            match = pick_match(film, data.get("results") or [])
            if match:
                film.tmdb_id = match["id"]
                film.media_type = "movie"
                return True
        return False

    def details(self, film: Film, refresh: bool = False) -> dict:
        """Movie/TV details with release dates and watch providers appended (one request)."""
        if film.media_type == "tv":
            append = "watch/providers,content_ratings"
        else:
            append = "release_dates,watch/providers"
        return self._get(
            f"/{film.media_type}/{film.tmdb_id}",
            {"append_to_response": append},
            max_age=0 if refresh else self.ttl,
        )


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    s = s.replace("&", "and")
    return re.sub(r"[^a-z0-9]+", "", s)


def pick_match(film: Film, results: list[dict]) -> dict | None:
    if not results:
        return None
    want = _norm(film.title)

    def year_of(r):
        d = r.get("release_date") or ""
        return int(d[:4]) if d[:4].isdigit() else None

    def score(r):
        titles = {_norm(r.get("title") or ""), _norm(r.get("original_title") or "")}
        title_ok = want in titles
        y = year_of(r)
        year_ok = film.year is None or (y is not None and abs(y - film.year) <= 1)
        exact_year = film.year is not None and y == film.year
        return (title_ok and year_ok, title_ok, exact_year, year_ok)

    best = max(results, key=score)  # max() is stable for ties -> keeps TMDB's popularity order
    s = score(best)
    if s[0] or (s[1] and film.year is None):
        return best
    # Title differs (e.g. punctuation, translated title) but year lines up: accept the top hit.
    if s[3] and film.year is not None:
        return best
    return None
