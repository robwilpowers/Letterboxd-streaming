"""Command-line entry point."""

from __future__ import annotations

import argparse
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import analyze, config, letterboxd, report
from .cache import Cache
from .models import Film, Result, Status
from .tmdb import TMDB


def check_film(film: Film, tmdb: TMDB, cache: Cache, prefs: config.Prefs, refresh: bool,
               use_letterboxd_pages: bool) -> Result:
    if use_letterboxd_pages and film.slug and not film.tmdb_id:
        letterboxd.resolve_tmdb_id(film, cache)
    if not film.tmdb_id and not tmdb.search(film):
        return Result(film, Status.NOT_FOUND, note="No TMDB match for this title/year")
    details = tmdb.details(film, refresh=refresh)
    if film.year is None:
        d = details.get("release_date") or details.get("first_air_date") or ""
        film.year = int(d[:4]) if d[:4].isdigit() else None
    result = analyze.classify(film, details, prefs)
    snap = analyze.snapshot(result)
    prev = cache.last_snapshot(film.key, prefs.region)
    result.changes = analyze.diff_snapshots(prev, snap)
    if prev is None or {k: v for k, v in prev.items() if k != "_taken_at"} != snap:
        cache.add_snapshot(film.key, prefs.region, snap)
    return result


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="letterboxd-streaming",
        description="Find where to stream the films on your Letterboxd watchlist, cheapest option first.",
    )
    src = ap.add_mutually_exclusive_group()
    src.add_argument("-u", "--user", help="Letterboxd username (public watchlist is scraped)")
    src.add_argument("--csv", help="watchlist.csv from a Letterboxd data export")
    ap.add_argument("-c", "--config", help="Path to config.toml (default: ./config.toml if present)")
    ap.add_argument("-r", "--region", help="Two-letter country code, e.g. US, GB, CA (overrides config)")
    ap.add_argument("--refresh", action="store_true", help="Ignore cached availability and re-fetch everything")
    ap.add_argument("--html", metavar="PATH", help="Also write an HTML report")
    ap.add_argument("--json", metavar="PATH", help="Also write results as JSON")
    ap.add_argument("--service", metavar="NAME",
                    help='Only list films on this service in the "needs a subscription" section, e.g. --service Peacock')
    ap.add_argument("--limit", type=int, help="Only check the first N films (useful for a quick test)")
    ap.add_argument("--workers", type=int, default=6, help="Parallel requests (default 6)")
    args = ap.parse_args(argv)

    prefs = config.load(args.config)
    if args.region:
        prefs.region = args.region.upper()
    cache = Cache(prefs.cache_path)
    tmdb = TMDB(prefs.tmdb_api_key, prefs.tmdb_read_token, cache=cache, ttl_hours=prefs.ttl_hours)

    if args.csv:
        films = letterboxd.read_watchlist_csv(args.csv)
    else:
        user = args.user or prefs.letterboxd_user
        if not user:
            ap.error("give --user USERNAME or --csv watchlist.csv (or set letterboxd_user in config.toml)")
        print(f"Reading {user}'s watchlist from Letterboxd...", file=sys.stderr)
        films = letterboxd.fetch_watchlist(user)
    if args.limit:
        films = films[: args.limit]
    if not films:
        print("Watchlist is empty.", file=sys.stderr)
        return 0
    print(f"Checking {len(films)} films in {prefs.region}...", file=sys.stderr)

    def run(film: Film) -> Result:
        try:
            return check_film(film, tmdb, cache, prefs, args.refresh, use_letterboxd_pages=not args.csv)
        except SystemExit:
            raise
        except Exception as exc:  # one bad film shouldn't sink the whole report
            return Result(film, Status.NOT_FOUND, note=f"Lookup failed: {exc}")

    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        results = list(pool.map(run, films))

    print(report.render_text(results, prefs, service=args.service))
    if args.html:
        Path(args.html).write_text(report.render_html(results, prefs), encoding="utf-8")
        print(f"\nWrote {args.html}", file=sys.stderr)
    if args.json:
        Path(args.json).write_text(report.render_json(results), encoding="utf-8")
        print(f"Wrote {args.json}", file=sys.stderr)
    cache.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
