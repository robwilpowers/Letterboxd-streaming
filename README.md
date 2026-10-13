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

CHEAPEST TO RENT OR BUY (1)
  Dune: Part Two (2024) -> Apple TV [Rent]
      also: Amazon Video (rent), Max (subscription ~$10.99/mo)

IN THEATERS (1)
  New Thing (2026) - In theaters since 2026-09-26; streaming date not announced

NOT RELEASED YET (1)
  Untitled Project (2026) - Expected release 2026-12-18

SUBSCRIPTIONS WORTH CONSIDERING (films you can't already watch free)
  Peacock: 4 film(s) for ~$10.99/mo (~$2.75 per film)
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
```

If you prefer, `pip install .` installs it as a `letterboxd-streaming` command.

### Getting the watchlist

* **Scraping (`--user`)** reads your public watchlist pages. Letterboxd has no public API and sometimes blocks automated requests. If you get a 403, use the CSV export instead.
* **CSV (`--csv`)**: on letterboxd.com, go to Settings → Data → Export Your Data and use `watchlist.csv` from the zip. Films are matched to TMDB by title and year. This is a little less exact than scraping, which reads the TMDB ID straight from each film's Letterboxd page.

## How it decides

**Where to watch:** availability comes from TMDB's watch-provider data, which is supplied by JustWatch. Every option is given an estimated cost for watching *this one film*:

| Option | Cost used for ranking |
|---|---|
| Free / free with ads (Tubi, Pluto, Roku Channel, Kanopy, Hoopla…) | $0 |
| A subscription listed in `my_services` | $0 (you already pay for it) |
| A subscription you don't have | its monthly price (editable in `[prices]`) |
| Rent | `rental_estimate` (default $4.99) |
| Buy | `purchase_estimate` (default $14.99) |

The cheapest option is listed first and the others appear under "also". This means a $4.99 rental can rank above a $14.99/mo MUBI subscription for a single film. To balance that, the **Subscriptions worth considering** section counts how many watchlist films each service you don't have would unlock, which shows when a month of a service is the better deal.

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

* **Prices are estimates.** TMDB doesn't publish rental or purchase prices, and subscription prices change often. Check `DEFAULT_PRICES` in `letterboxd_streaming/config.py` and override them in `config.toml`. New-release premium rentals ($19.99+) are ranked as if they cost `rental_estimate`.
* **No "leaving soon" dates.** The data shows where a film is today, not when it leaves. The change log only catches a move after it happens.
* **Data lag.** JustWatch/TMDB data can trail real catalog changes by a day or two. The "All options" link in the HTML report opens the TMDB/JustWatch page so you can check.
* **Scraper fragility.** If Letterboxd changes its page markup, `--user` may return nothing. `--csv` will still work.

## Development

```sh
python -m unittest discover -s tests
```

Availability data is provided by [JustWatch](https://www.justwatch.com) via [TMDB](https://www.themoviedb.org). This product uses the TMDB API but is not endorsed or certified by TMDB.
