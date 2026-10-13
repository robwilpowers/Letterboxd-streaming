# Letterboxd Streaming

Looks at your Letterboxd watchlist and tells you where each film can be watched in your country, cheapest option first. For films you can't watch at home yet, it says why: still in theaters, not released yet, or released with no service carrying it.

```
CHANGES SINCE LAST RUN
  * Old Thing (2019): Now streaming on Netflix
  * Old Thing (2019): Left Peacock

WATCH FREE (1)
  Crouching Tiger, Hidden Dragon (2000) -> Tubi TV [Free with ads]

ON YOUR SERVICES (1)
  Old Thing (2019) -> Netflix [Your subscription]

NEEDS A SUBSCRIPTION YOU DON'T HAVE (5)
  By service (films it would unlock):
    Max: 4 film(s), 2 only here - ~$10.99/mo
    Peacock: 2 film(s), 1 only here - ~$10.99/mo
    Criterion Channel: 1 film(s) - ~$10.99/mo

  Barbie (2023) -> Max [Subscription ~$10.99/mo]
  Oppenheimer (2023) -> Peacock [Subscription ~$10.99/mo]
  Stalker (1979) -> Criterion Channel [Subscription ~$10.99/mo]
      also: Max (subscription ~$10.99/mo)
  ...

PAY PER VIEW (RENT OR BUY) (1)
  Dune: Part Two (2024) -> Apple TV [Rent]
      also: Amazon Video (rent)

IN THEATERS (1)
  New Thing (2026) - In theaters since 2026-09-26; streaming date not announced

NOT RELEASED YET (1)
  Untitled Project (2026) - Expected release 2026-12-18

```

## Setup

Requires Python 3.11+. It has no third-party dependencies.

1. Get a free TMDB API key: https://www.themoviedb.org/settings/api
2. `cp config.example.toml config.toml`, then fill in your Letterboxd username, the API key, and the services you pay for.
3. Run it:

```sh
python -m letterboxd_streaming                       # uses config.toml
python -m letterboxd_streaming --user yourname --html report.html
python -m letterboxd_streaming --csv watchlist.csv   # from a Letterboxd data export
python -m letterboxd_streaming --service Peacock     # only Peacock films in the "needs a subscription" section
```

If you prefer, `pip install .` installs it as a `letterboxd-streaming` command.

### Getting the watchlist

* **Scraping (`--user`)** reads your public watchlist pages. Letterboxd has no public API and sometimes blocks automated requests. If you get a 403, use the CSV export instead.
* **CSV (`--csv`)**: on letterboxd.com, go to Settings → Data → Export Your Data and use `watchlist.csv` from the zip. Films are matched to TMDB by title and year. This is a little less exact than scraping, which reads the TMDB ID straight from each film's Letterboxd page.

## How it decides

**Where to watch:** availability comes from TMDB's watch-provider data, which is supplied by JustWatch. Options are ranked strictly by tier, so a lower tier never beats a higher one:

| Tier | Services | Order within the tier |
|---|---|---|
| 1. Free | Tubi, Pluto TV, Plex, The Roku Channel, Kanopy (`free_services`) | JustWatch's display order |
| 2. Subscription | Netflix, HBO Max, Paramount+, Hulu, Disney+, MUBI, Criterion Channel, Prime Video, etc. | services in `my_services` first, then cheapest monthly price |
| 3. Rent (pay per view) | Apple TV, Amazon Video, Google Play Movies, etc. | JustWatch's display order |
| 4. Buy | same stores | shown only when there's nothing else |

The best option is listed first and the others appear under "also".

**Which subscription to get next:** the "Needs a subscription you don't have" section starts with a breakdown by service. For each one it shows how many of those films it would unlock, and how many are **only here**, meaning no other subscription you lack carries them. Services are sorted by films unlocked, then by exclusives, then by price. A service with fewer films but more exclusives can be the better pick, because the others may be reachable through a different service. In the HTML report, click a service to show only its films. In the terminal, use `--service NAME`.

TMDB sometimes marks other services as free (YouTube's free movies, Crackle, Hoopla). By default they're left out because they aren't in your free list. Set `count_other_free = true` to count them. Ad-supported tiers of paid services ("Amazon Prime Video with Ads") count as that subscription, not as free.

Variants of the same service are merged ("Max", "HBO Max", "Max Amazon Channel"; "Netflix Standard with Ads").

**Unreleased films:** the app uses TMDB release dates for your region (theatrical, digital, physical, TV):

* Theatrical release still in the future, or the film is "In Production" or "Planned" → **Not released yet**, with the expected date when one is known
* In theaters, no home release yet → **In theaters**, with the digital date when announced (for up to `theatrical_window_days` after release)
* Premium rental available while still in theaters → counts as available (rent)
* Released a while ago, but no service lists it → **Released, but nowhere to watch**
* No dates for your region → falls back to worldwide dates and says so

**Films that switch services:** nothing is hard-coded, so every run reads live data, which is cached for `ttl_hours` (default 12). Each run also saves a snapshot of where every film was streaming. The next run compares against it and lists what moved ("Now streaming on Netflix", "Left Peacock", "in theaters → available"). To keep up with changes, run it on a schedule, e.g. weekly with cron:

```
0 9 * * 1  cd ~/Letterboxd-streaming && python3 -m letterboxd_streaming --html ~/watchlist.html
```

Use `--refresh` to skip the cache.

## Limitations

* **Subscription prices are estimates.** They only order subscriptions you don't have, and they change often. Check `DEFAULT_PRICES` in `letterboxd_streaming/config.py` and override them in `config.toml`. TMDB doesn't publish rental prices, so rentals aren't priced.
* **No "leaving soon" dates.** The data shows where a film is today, not when it leaves. The change log only catches a move after it happens.
* **Data lag.** JustWatch/TMDB data can trail real catalog changes by a day or two. The "All options" link in the HTML report opens the TMDB/JustWatch page so you can check.
* **Scraper fragility.** If Letterboxd changes its page markup, `--user` may return nothing. `--csv` will still work.

## Development

```sh
python -m unittest discover -s tests
```

Availability data is provided by [JustWatch](https://www.justwatch.com) via [TMDB](https://www.themoviedb.org). This product uses the TMDB API but is not endorsed or certified by TMDB.
