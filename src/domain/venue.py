"""Venue identity and registry objects."""

from dataclasses import dataclass
from urllib.parse import urlparse

from .market import MarketType


def _url(value: str, field: str, schemes: frozenset[str]) -> str:
    result = str(value).strip().rstrip("/")
    parsed = urlparse(result)
    if parsed.scheme not in schemes or not parsed.netloc:
        raise ValueError(f"{field} must be an HTTP(S) URL")
    return result


@dataclass(frozen=True, slots=True)
class Venue:
    venue_id: str
    display_name: str
    market_types: frozenset[MarketType]
    public_rest_base_url: str
    public_ws_base_url: str

    def __post_init__(self) -> None:
        venue_id = str(self.venue_id).strip().lower()
        display_name = str(self.display_name).strip()
        if not venue_id or not display_name:
            raise ValueError("venue_id and display_name must not be empty")
        market_types = frozenset(
            value if isinstance(value, MarketType) else MarketType(value)
            for value in self.market_types
        )
        if not market_types:
            raise ValueError("venue must support at least one market type")
        object.__setattr__(self, "venue_id", venue_id)
        object.__setattr__(self, "display_name", display_name)
        object.__setattr__(self, "market_types", market_types)
        object.__setattr__(self, "public_rest_base_url", _url(self.public_rest_base_url, "public_rest_base_url", frozenset({"http", "https"})))
        object.__setattr__(self, "public_ws_base_url", _url(self.public_ws_base_url, "public_ws_base_url", frozenset({"ws", "wss"})))


class VenueRegistry:
    """In-memory registry used by the application until persistence is added."""

    def __init__(self, venues: tuple[Venue, ...] = ()) -> None:
        self._venues: dict[str, Venue] = {}
        for venue in venues:
            self.register(venue)

    def register(self, venue: Venue) -> None:
        if venue.venue_id in self._venues:
            raise ValueError(f"venue already registered: {venue.venue_id}")
        self._venues[venue.venue_id] = venue

    def get(self, venue_id: str) -> Venue:
        key = str(venue_id).strip().lower()
        try:
            return self._venues[key]
        except KeyError as error:
            raise KeyError(f"unknown venue: {key}") from error

    def all(self) -> tuple[Venue, ...]:
        return tuple(self._venues[key] for key in sorted(self._venues))
