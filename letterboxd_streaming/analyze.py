"""Turn raw TMDB data into a recommendation: where to watch, or why you can't yet."""

from __future__ import annotations

from datetime import date, timedelta

from .config import Prefs, canonical_provider
from .models import Film, Offer, Result, Status

FREE, SUBSCRIPTION, RENT, BUY = 0, 1, 2, 3
KIND_LABEL = {"free": "Free", "ads": "Free with ads", "flatrate": "Subscription", "rent": "Rent", "buy": "Buy"}

# TMDB release_dates types
PREMIERE, THEATRICAL_LIMITED, THEATRICAL, DIGITAL, PHYSICAL, TV = 1, 2, 3, 4, 5, 6
PREPRODUCTION_STATUSES = {"Rumored", "Planned", "In Production", "Post Production"}


def build_offers(region_providers: dict, prefs: Prefs) -> list[Offer]:
    """All ways to watch in the region, best first.

    Ranking is strictly by tier, then within a tier:
      1. Free: the services in free_services (Tubi, Pluto TV, Plex, The Roku Channel by default)
      2. Subscription: services you pay for first, then other subscriptions, cheapest first
      3. Rent (pay per view: Apple TV, Amazon Video, Google Play Movies, ...)
      4. Buy
    """
    offers: dict[tuple[str, int], Offer] = {}
    for kind in ("free", "ads", "flatrate", "rent", "buy"):
        for p in region_providers.get(kind) or []:
            name = p.get("provider_name") or "Unknown"
            key = canonical_provider(name)
            if kind == "flatrate" and key == "apple tv":
                key = "apple tv plus"  # Apple renamed TV+ to "Apple TV"; the store never offers flatrate
            offer = _make_offer(name, key, kind, prefs, _priority(p))
            if offer is None:
                continue
            # Collapse variants of the same service ("Max" and "Max Amazon Channel"): keep the better one.
            dedupe = (key, offer.tier)
            prev = offers.get(dedupe)
            if prev is None or _sort_key(offer) < _sort_key(prev):
                offers[dedupe] = offer
    return sorted(offers.values(), key=_sort_key)


def _make_offer(name: str, key: str, kind: str, prefs: Prefs, priority: int) -> Offer | None:
    if kind in ("rent", "buy"):
        tier = RENT if kind == "rent" else BUY
        return Offer(name, key, kind, tier, 0.0, KIND_LABEL[kind], priority=priority)
    if key in prefs.free_services:
        label = "Free with ads" if kind == "ads" else "Free"
        return Offer(name, key, kind, FREE, 0.0, label, priority=priority)
    if kind in ("free", "ads"):
        # TMDB calls it free but it isn't one of your free services: either an ad tier of a paid
        # service ("Amazon Prime Video with Ads") or a free service you've chosen not to count.
        if key not in prefs.prices and key not in prefs.my_services:
            return None if not prefs.count_other_free else Offer(
                name, key, kind, FREE, 0.0, KIND_LABEL[kind], priority=priority)
        kind = "flatrate"
    if key in prefs.my_services:
        return Offer(name, key, kind, SUBSCRIPTION, 0.0, "Your subscription", subscribed=True, priority=priority)
    price = prefs.prices.get(key, prefs.unknown_subscription_price)
    label = f"Subscription ~${price:.2f}/mo" + ("" if key in prefs.prices else " (price unknown)")
    return Offer(name, key, kind, SUBSCRIPTION, price, label, priority=priority)


def _priority(p: dict) -> int:
    value = p.get("display_priority")
    return 999 if value is None else int(value)


def _sort_key(o: Offer):
    return (o.tier, not o.subscribed, o.cost, o.priority, o.provider)


def _parse_date(s: str | None) -> date | None:
    if not s:
        return None
    try:
        return date.fromisoformat(s[:10])
    except ValueError:
        return None


def _release_dates(details: dict, region: str) -> tuple[dict[int, list[date]], bool]:
    """Release dates by type for the region; falls back to worldwide if the region has none."""
    entries = (details.get("release_dates") or {}).get("results") or []
    regional = [e for e in entries if e.get("iso_3166_1") == region]
    used_fallback = not regional and bool(entries)
    by_type: dict[int, list[date]] = {}
    for e in regional or entries:
        for rd in e.get("release_dates") or []:
            d = _parse_date(rd.get("release_date"))
            if d:
                by_type.setdefault(int(rd.get("type") or 0), []).append(d)
    return by_type, used_fallback


def classify(film: Film, details: dict, prefs: Prefs, today: date | None = None) -> Result:
    today = today or date.today()
    providers = ((details.get("watch/providers") or {}).get("results") or {}).get(prefs.region) or {}
    offers = build_offers(providers, prefs)
    link = providers.get("link")

    if offers:
        note = ""
        if not any(o.is_streaming for o in offers):
            note = "Rent/buy only right now"
        return Result(film, Status.AVAILABLE, offers, note=note, link=link)

    tmdb_status = details.get("status") or ""
    if film.media_type == "tv":
        first = _parse_date(details.get("first_air_date"))
        if not first or first > today:
            return Result(film, Status.UNRELEASED, note="Series hasn't premiered",
                          expected_date=first.isoformat() if first else None, link=link)
        return Result(film, Status.NOT_AVAILABLE, note=f"Not streaming in {prefs.region}", link=link)

    if tmdb_status == "Canceled":
        return Result(film, Status.UNRELEASED, note="Production canceled", link=link)

    by_type, fallback = _release_dates(details, prefs.region)
    where = "worldwide" if fallback else prefs.region

    def first_of(*types):
        ds = [d for t in types for d in by_type.get(t, [])]
        return min(ds) if ds else None

    def next_of(*types):
        ds = [d for t in types for d in by_type.get(t, []) if d > today]
        return min(ds) if ds else None

    theatrical = first_of(THEATRICAL_LIMITED, THEATRICAL)
    home = first_of(DIGITAL, PHYSICAL, TV)
    premiere = first_of(PREMIERE)
    if theatrical is None and home is None and premiere is None:
        theatrical = _parse_date(details.get("release_date"))

    # Released at home already, but no provider listed: licensing gap or data lag.
    if home and home <= today:
        return Result(film, Status.NOT_AVAILABLE, link=link,
                      note=f"Released at home {home.isoformat()} but no service lists it in {prefs.region}")

    if theatrical and theatrical <= today:
        upcoming_home = next_of(DIGITAL, PHYSICAL, TV)
        if upcoming_home:
            return Result(film, Status.IN_THEATERS, link=link, expected_date=upcoming_home.isoformat(),
                          note=f"In theaters since {theatrical.isoformat()}; home release {upcoming_home.isoformat()}")
        if today - theatrical <= timedelta(days=prefs.theatrical_window_days):
            return Result(film, Status.IN_THEATERS, link=link,
                          note=f"In theaters since {theatrical.isoformat()}; streaming date not announced")
        return Result(film, Status.NOT_AVAILABLE, link=link,
                      note=f"Released {theatrical.isoformat()} but not streaming, rentable or for sale in {prefs.region}")

    # Nothing has been released in theaters or at home yet.
    upcoming = next_of(THEATRICAL_LIMITED, THEATRICAL, DIGITAL, PHYSICAL, TV) or (
        theatrical if theatrical and theatrical > today else None
    )
    if upcoming:
        kind = "digital/streaming" if upcoming in by_type.get(DIGITAL, []) or upcoming in by_type.get(TV, []) else "release"
        note = f"Expected {kind} {upcoming.isoformat()}"
        if fallback:
            note += f" ({where} date; none listed for {prefs.region})"
    elif premiere and premiere <= today:
        note = f"Festival premiere {premiere.isoformat()}; no {prefs.region} release scheduled"
    elif tmdb_status in PREPRODUCTION_STATUSES:
        note = f"{tmdb_status}; no release date announced"
    else:
        note = "No release date announced"
    return Result(film, Status.UNRELEASED, link=link,
                  expected_date=upcoming.isoformat() if upcoming else None, note=note)


def snapshot(result: Result) -> dict:
    return {
        "status": result.status.value,
        # Keyed by canonical service so "Max" vs "Max Amazon Channel" isn't reported as a move.
        "streaming": {o.key: o.provider for o in result.offers if o.is_streaming},
        "best": result.best.provider if result.best else None,
    }


def diff_snapshots(prev: dict | None, cur: dict) -> list[str]:
    """Human-readable changes since the previous run."""
    if not prev:
        return []
    changes = []
    if prev.get("status") != cur["status"]:
        changes.append(f"Status changed: {prev.get('status', '?').replace('_', ' ')} -> {cur['status'].replace('_', ' ')}")
    before, after = prev.get("streaming") or {}, cur["streaming"]
    for key in sorted(after.keys() - before.keys()):
        changes.append(f"Now streaming on {after[key]}")
    for key in sorted(before.keys() - after.keys()):
        changes.append(f"Left {before[key]}")
    return changes
