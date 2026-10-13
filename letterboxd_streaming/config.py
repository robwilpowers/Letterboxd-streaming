"""User preferences: region, services you pay for, and price estimates."""

from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

# Rough US monthly prices (cheapest tier, ad-supported where one exists) as of late 2026.
# Streaming prices change often -- override them in config.toml. Keys are canonical
# provider names (see canonical_provider).
DEFAULT_PRICES = {
    "netflix": 7.99,
    "hulu": 11.99,
    "disney plus": 11.99,
    "hbo max": 10.99,
    "peacock": 10.99,
    "paramount plus": 8.99,
    "apple tv plus": 12.99,
    "amazon prime video": 8.99,
    "mubi": 14.99,
    "criterion channel": 10.99,
    "shudder": 8.99,
    "amc plus": 9.99,
    "starz": 10.99,
    "mgm plus": 7.99,
    "fubotv": 84.99,
    "youtube tv": 82.99,
    "sling tv": 45.99,
    "britbox": 8.99,
    "acorn tv": 7.99,
    "fandor": 3.99,
    "philo": 28.00,
    "crunchyroll": 7.99,
    "screambox": 6.99,
    "sundance now": 8.99,
}

# The services that count as free. Only these rank in the free tier; see count_other_free.
DEFAULT_FREE = ["Tubi TV", "Pluto TV", "Plex", "The Roku Channel"]

_SUFFIXES = re.compile(
    r"\s+(amazon channel|apple tv channel|roku premium channel|via prime video|"
    r"standard with ads|basic with ads|with ads|premium|essential)$"
)

_ALIASES = {
    "max": "hbo max",
    "hbo": "hbo max",
    "disney": "disney plus",
    "disney+": "disney plus",
    "paramount+": "paramount plus",
    "paramount+ with showtime": "paramount plus",
    "apple tv+": "apple tv plus",
    "amc+": "amc plus",
    "mgm+": "mgm plus",
    "prime video": "amazon prime video",
    "amazon prime": "amazon prime video",
    "amazon video": "amazon video",  # the rent/buy store, not Prime
    "tubi": "tubi tv",
    "roku channel": "the roku channel",
    "the criterion channel": "criterion channel",
    "criterion collection": "criterion channel",
    "the criterion collection": "criterion channel",
    "hbomax": "hbo max",
    "pluto": "pluto tv",
    "plex channel": "plex",
    "plex player": "plex",
    "roku": "the roku channel",
    "google play": "google play movies",
    "google tv": "google play movies",
    "fubo": "fubotv",
    "kanopy": "kanopy",
    "hoopla": "hoopla",
    "netflix kids": "netflix",
}


def canonical_provider(name: str) -> str:
    """Collapse JustWatch naming variants ("Max Amazon Channel", "Netflix Standard with Ads") to one key."""
    key = re.sub(r"\s+", " ", name.strip().lower())
    for _ in range(3):
        stripped = _SUFFIXES.sub("", key)
        if stripped == key:
            break
        key = stripped
    key = _ALIASES.get(key, key)
    return key.replace("+", " plus").replace("  ", " ").strip()


@dataclass
class Prefs:
    region: str = "US"
    my_services: set[str] = field(default_factory=set)
    free_services: set[str] = field(default_factory=lambda: {canonical_provider(s) for s in DEFAULT_FREE})
    prices: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_PRICES))
    unknown_subscription_price: float = 9.99
    # TMDB sometimes marks other services free (YouTube's free movies, Crackle, Kanopy...).
    # False: ignore those and only count free_services as free. True: count them as free too.
    count_other_free: bool = False
    # How long after a theatrical release a film still counts as "in theaters".
    theatrical_window_days: int = 120
    ttl_hours: float = 12
    letterboxd_user: str | None = None
    tmdb_api_key: str | None = None
    tmdb_read_token: str | None = None
    cache_path: str = "~/.cache/letterboxd-streaming/cache.sqlite3"


def load(path: str | None) -> Prefs:
    prefs = Prefs()
    data: dict = {}
    if path:
        with open(path, "rb") as fh:
            data = tomllib.load(fh)
    elif Path("config.toml").exists():
        with open("config.toml", "rb") as fh:
            data = tomllib.load(fh)

    prefs.region = str(data.get("region", prefs.region)).upper()
    prefs.my_services = {canonical_provider(s) for s in data.get("my_services", [])}
    if "free_services" in data:
        prefs.free_services = {canonical_provider(s) for s in data["free_services"]}
    for name, price in (data.get("prices") or {}).items():
        prefs.prices[canonical_provider(name)] = float(price)
    if "count_other_free" in data:
        prefs.count_other_free = bool(data["count_other_free"])
    for attr in ("unknown_subscription_price", "ttl_hours"):
        if attr in data:
            setattr(prefs, attr, float(data[attr]))
    if "theatrical_window_days" in data:
        prefs.theatrical_window_days = int(data["theatrical_window_days"])
    for attr in ("letterboxd_user", "tmdb_api_key", "tmdb_read_token", "cache_path"):
        if data.get(attr):
            setattr(prefs, attr, str(data[attr]))

    prefs.tmdb_api_key = os.environ.get("TMDB_API_KEY") or prefs.tmdb_api_key
    prefs.tmdb_read_token = os.environ.get("TMDB_READ_TOKEN") or prefs.tmdb_read_token
    return prefs
