"""Plain data types shared across the app."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


@dataclass
class Film:
    title: str
    year: int | None = None
    slug: str | None = None  # Letterboxd slug, e.g. "dune-part-two"
    tmdb_id: int | None = None
    media_type: str = "movie"  # "movie" or "tv" (Letterboxd lists some miniseries)

    @property
    def key(self) -> str:
        """Stable identifier used for caching and history."""
        if self.tmdb_id:
            return f"tmdb:{self.media_type}:{self.tmdb_id}"
        if self.slug:
            return f"lb:{self.slug}"
        return f"title:{self.title.lower()}:{self.year or ''}"

    @property
    def label(self) -> str:
        return f"{self.title} ({self.year})" if self.year else self.title


class Status(str, Enum):
    AVAILABLE = "available"          # at least one way to watch at home
    IN_THEATERS = "in_theaters"      # theatrical release happened, home release not yet
    UNRELEASED = "unreleased"        # not out anywhere in the region yet
    NOT_AVAILABLE = "not_available"  # released, but nobody streams/rents it in the region
    NOT_FOUND = "not_found"          # couldn't match the film on TMDB


@dataclass
class Offer:
    provider: str          # display name as TMDB/JustWatch reports it
    key: str               # canonical provider key used for matching and prices
    kind: str              # free | ads | flatrate | rent | buy
    cost: float            # estimated marginal cost to watch this one film
    cost_label: str
    subscribed: bool = False
    priority: int = 999    # JustWatch display priority, used as a tie-breaker

    @property
    def is_streaming(self) -> bool:
        return self.kind in ("free", "ads", "flatrate")


@dataclass
class Result:
    film: Film
    status: Status
    offers: list[Offer] = field(default_factory=list)  # sorted best-first
    note: str = ""
    expected_date: str | None = None  # ISO date of the next known release step
    link: str | None = None           # JustWatch/TMDB page for the region
    changes: list[str] = field(default_factory=list)

    @property
    def best(self) -> Offer | None:
        return self.offers[0] if self.offers else None
