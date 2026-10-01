"""Venue-scoped instrument registry."""

from .market import Instrument


class InstrumentRegistry:
    def __init__(self, instruments: tuple[Instrument, ...] = ()) -> None:
        self._instruments: dict[str, Instrument] = {}
        for instrument in instruments:
            self.register(instrument)

    def register(self, instrument: Instrument) -> None:
        storage_key = instrument.key.casefold()
        if storage_key in self._instruments:
            raise ValueError(f"instrument already registered: {instrument.key}")
        self._instruments[storage_key] = instrument

    def get(self, key: str) -> Instrument:
        try:
            return self._instruments[str(key).strip().casefold()]
        except KeyError as error:
            raise KeyError(f"unknown instrument: {key}") from error

    def for_venue(self, venue_id: str) -> tuple[Instrument, ...]:
        key = str(venue_id).strip().lower()
        return tuple(
            self._instruments[instrument_key]
            for instrument_key in sorted(self._instruments)
            if self._instruments[instrument_key].venue_id == key
        )

    def all(self) -> tuple[Instrument, ...]:
        return tuple(self._instruments[key] for key in sorted(self._instruments))
