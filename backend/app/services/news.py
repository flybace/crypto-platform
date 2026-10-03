"""Durable crypto news events and research-only market resonance."""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
import hashlib
from pathlib import Path
from threading import RLock
from typing import Iterable

from adapters.standalone.state_store import JsonStateError, JsonStateStore, StateStore
from application.history_storage import HistoryStorage, HistoryStorageError
from backend.app.services.market_summary import build_market_summary


SENTIMENTS = frozenset({"positive", "neutral", "risk"})
RISK_LEVELS = frozenset({"low", "medium", "high"})


def _parse_time(value: object) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return datetime.min.replace(tzinfo=UTC)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


class NewsError(ValueError):
    """Raised when a news event or resonance request is invalid."""


class NewsService:
    """Keep source-labelled events separate from price and execution state.

    External search connectors are deliberately not hidden behind this
    service. A connector may ingest normalized events later, while the core
    remains auditable and does not claim that a search result is real-time.
    """

    def __init__(
        self,
        storage: HistoryStorage,
        *,
        state_path: str | Path | None = None,
        state_store: StateStore | None = None,
    ) -> None:
        self._storage = storage
        self._state = state_store or (JsonStateStore(state_path) if state_path else None)
        self._events: dict[str, dict[str, object]] = {}
        self._lock = RLock()
        self._load()

    def create(
        self,
        *,
        title: str,
        summary: str,
        source: str,
        published_at: datetime,
        symbols: Iterable[str] = (),
        topics: Iterable[str] = (),
        sentiment: str = "neutral",
        risk_level: str = "low",
        impact_score: Decimal | str | int = Decimal("50"),
        source_url: str | None = None,
    ) -> dict[str, object]:
        normalized_title = str(title).strip()
        normalized_summary = str(summary).strip()
        normalized_source = str(source).strip()
        if not normalized_title or not normalized_summary or not normalized_source:
            raise NewsError("title, summary, and source must not be empty")
        timestamp = self._timestamp(published_at)
        normalized_sentiment = str(sentiment).strip().lower()
        normalized_risk = str(risk_level).strip().lower()
        if normalized_sentiment not in SENTIMENTS:
            raise NewsError("sentiment must be positive, neutral, or risk")
        if normalized_risk not in RISK_LEVELS:
            raise NewsError("risk_level must be low, medium, or high")
        score = self._score(impact_score)
        normalized_symbols = self._values(symbols, maximum=20, uppercase=True)
        normalized_topics = self._values(topics, maximum=12, uppercase=False)
        event_key = self._event_key(normalized_source, normalized_title, timestamp, normalized_symbols)
        with self._lock:
            self._load()
            existing = self._events.get(event_key)
            if existing is not None:
                return {**deepcopy(existing), "deduplicated": True}
            now = datetime.now(UTC).isoformat()
            event = {
                "event_id": f"news-{event_key[:20]}",
                "event_key": event_key,
                "title": normalized_title[:200],
                "summary": normalized_summary[:1000],
                "source": normalized_source[:120],
                "source_url": str(source_url).strip()[:500] if source_url else None,
                "source_type": "ingested",
                "published_at": timestamp.isoformat(),
                "created_at": now,
                "updated_at": now,
                "symbols": normalized_symbols,
                "topics": normalized_topics,
                "sentiment": normalized_sentiment,
                "risk_level": normalized_risk,
                "impact_score": str(score),
            }
            self._events[event_key] = event
            self._trim_locked()
            self._persist_locked()
            return deepcopy(event)

    def events(
        self,
        *,
        symbol: str | None = None,
        topic: str | None = None,
        sentiment: str = "all",
        limit: int = 50,
        start_at: datetime | None = None,
        end_at: datetime | None = None,
    ) -> list[dict[str, object]]:
        normalized_symbol = str(symbol or "").strip().upper()
        normalized_topic = str(topic or "").strip().lower()
        normalized_sentiment = str(sentiment).strip().lower()
        if normalized_sentiment not in {"all", *SENTIMENTS}:
            raise NewsError("sentiment must be all, positive, neutral, or risk")
        with self._lock:
            self._load()
            records = sorted(self._events.values(), key=lambda item: str(item.get("published_at", "")), reverse=True)
            filtered = []
            for item in records:
                symbols = {str(value).upper() for value in item.get("symbols", [])}
                topics = {str(value).lower() for value in item.get("topics", [])}
                if normalized_symbol and normalized_symbol not in symbols:
                    continue
                if normalized_topic and normalized_topic not in topics:
                    continue
                if normalized_sentiment != "all" and item.get("sentiment") != normalized_sentiment:
                    continue
                if start_at is not None or end_at is not None:
                    try:
                        published = datetime.fromisoformat(str(item.get("published_at", "")).replace("Z", "+00:00"))
                    except ValueError:
                        continue
                    if start_at is not None and published < start_at:
                        continue
                    if end_at is not None and published > end_at:
                        continue
                filtered.append(deepcopy(item))
            return filtered[: max(1, min(int(limit), 200))]

    def get(self, event_id: str) -> dict[str, object] | None:
        with self._lock:
            self._load()
            item = next((value for value in self._events.values() if value.get("event_id") == str(event_id)), None)
            return deepcopy(item) if item is not None else None

    def summary(self, *, limit: int = 20) -> dict[str, object]:
        items = self.events(limit=limit)
        return {
            "module": "news",
            "status": "ready" if items else "empty",
            "event_count": len(self._events),
            "returned_count": len(items),
            "latest": deepcopy(items[0]) if items else None,
            "sentiment_counts": {
                sentiment: sum(1 for item in self._events.values() if item.get("sentiment") == sentiment)
                for sentiment in sorted(SENTIMENTS)
            },
            "research_only": True,
            "execution_eligible": False,
            "source_mode": "rss_and_manual",
            "note": "事件必须带来源；没有外部新闻连接器时不会伪造实时消息。",
        }

    def resonance(self, *, interval: str = "1h", limit: int = 100) -> dict[str, object]:
        normalized_interval = str(interval).strip().lower()
        if normalized_interval not in {"1d", "1h", "5m"}:
            raise NewsError("interval must be 1d, 1h, or 5m")
        try:
            market = build_market_summary(self._storage, interval=normalized_interval)
        except (HistoryStorageError, ValueError) as error:
            raise NewsError("verified market history is unavailable") from error
        event_items = self.events(limit=200)
        candidates: list[dict[str, object]] = []
        for quote in market.get("quotes", []):
            if not isinstance(quote, dict):
                continue
            symbol = str(quote.get("symbol", "")).upper()
            related = [event for event in event_items if not event.get("symbols") or symbol in {str(value).upper() for value in event.get("symbols", [])}]
            if not related:
                continue
            score = max((self._score(event.get("impact_score", "0")) for event in related), default=Decimal("0"))
            risk_event = next((event for event in related if event.get("risk_level") == "high" or event.get("sentiment") == "risk"), None)
            price_change = self._number(quote.get("change_pct", "0"))
            resonance_score = max(Decimal("0"), min(Decimal("100"), score + min(Decimal("20"), abs(price_change))))
            if risk_event is not None:
                state = "RISK_BLOCK"
                action = "消息风险拦截"
            elif resonance_score >= Decimal("70") and ((related[0].get("sentiment") == "positive" and price_change >= 0) or price_change > 0):
                state = "RESONANCE"
                action = "进入回测复核"
            else:
                state = "OBSERVED"
                action = "继续观察"
            candidates.append(
                {
                    "symbol": symbol,
                    "venue_id": quote.get("venue_id"),
                    "dataset_id": quote.get("dataset_id"),
                    "close": quote.get("close"),
                    "change_pct": quote.get("change_pct"),
                    "news_score": str(resonance_score.quantize(Decimal("0.01"))),
                    "state": state,
                    "action": action,
                    "risk_level": "high" if risk_event else "medium" if resonance_score >= 60 else "low",
                    "event_ids": [event.get("event_id") for event in related[:8]],
                    "event_titles": [event.get("title") for event in related[:8]],
                    "research_only": True,
                    "execution_eligible": False,
                }
            )
        candidates.sort(key=lambda item: self._score(item.get("news_score", "0")), reverse=True)
        return {
            "module": "news_resonance",
            "status": "ready" if candidates else "empty",
            "interval": normalized_interval,
            "as_of": market.get("as_of"),
            "events_considered": len(event_items),
            "items": candidates[: max(1, min(int(limit), 200))],
            "count": len(candidates),
            "research_only": True,
            "execution_eligible": False,
            "note": "消息共振只用于候选排序；必须经过历史回测、模拟盘和风控，不能直接下单。",
        }

    def advice(self, *, limit: int = 50, hours: int = 72) -> list[dict[str, object]]:
        """Machine-readable strategy advice derived from major news clusters."""
        from backend.app.services import news_intel

        cutoff = datetime.now(UTC) - timedelta(hours=hours)
        events = [
            event for event in self.events(limit=200)
            if _parse_time(event.get("published_at")) >= cutoff
        ]
        advice: list[dict[str, object]] = []
        for cluster in news_intel.cluster_events(events):
            if not news_intel.is_major(cluster):
                continue
            for item in news_intel.advise_cluster(cluster):
                advice.append({
                    **item,
                    "title": cluster.get("title"),
                    "topics": cluster.get("topics"),
                    "sentiment": cluster.get("sentiment"),
                    "sources": cluster.get("sources"),
                })
        advice.sort(key=lambda item: int(item.get("impact_score", 0)), reverse=True)  # type: ignore[arg-type]
        return advice[: max(1, min(int(limit), 200))]

    def ranking(self, *, hours: int = 72) -> list[dict[str, object]]:
        """消息面选币榜: per-symbol news attention ranking (选币辅助)."""
        from backend.app.services import news_intel

        return news_intel.rank_symbols(self.events(limit=200), hours=hours)

    def _load(self) -> None:
        if self._state is None:
            return
        self._events.clear()
        try:
            payload = self._state.load({"version": 1, "events": []})
        except JsonStateError as error:
            raise RuntimeError("news state is unreadable") from error
        if not isinstance(payload, dict) or not isinstance(payload.get("events", []), list):
            raise RuntimeError("news state must contain an events array")
        for item in payload["events"]:
            if isinstance(item, dict) and str(item.get("event_key", "")).strip():
                self._events[str(item["event_key"])] = deepcopy(item)

    def _persist_locked(self) -> None:
        if self._state is None:
            return
        try:
            self._state.save({"version": 1, "events": list(self._events.values())})
        except JsonStateError as error:
            raise RuntimeError("news state cannot be saved") from error

    def _trim_locked(self) -> None:
        records = sorted(self._events.items(), key=lambda pair: str(pair[1].get("published_at", "")), reverse=True)
        self._events = dict(records[:500])

    @staticmethod
    def _timestamp(value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise NewsError("published_at must contain timezone information")
        return value.astimezone(UTC)

    @staticmethod
    def _values(values: Iterable[str], *, maximum: int, uppercase: bool) -> list[str]:
        normalized: list[str] = []
        for value in values:
            item = str(value).strip()
            if not item:
                continue
            item = item.upper() if uppercase else item[:80]
            if item not in normalized:
                normalized.append(item)
        if len(normalized) > maximum:
            raise NewsError("news event contains too many symbols or topics")
        return normalized

    @staticmethod
    def _score(value: object, *, maximum: Decimal = Decimal("100")) -> Decimal:
        try:
            parsed = Decimal(str(value))
        except (InvalidOperation, ValueError, TypeError) as error:
            raise NewsError("impact and score values must be finite numbers") from error
        if not parsed.is_finite():
            raise NewsError("impact and score values must be finite numbers")
        return max(Decimal("0"), min(maximum, parsed))

    @staticmethod
    def _number(value: object) -> Decimal:
        try:
            parsed = Decimal(str(value))
        except (InvalidOperation, ValueError, TypeError) as error:
            raise NewsError("market change values must be finite numbers") from error
        if not parsed.is_finite():
            raise NewsError("market change values must be finite numbers")
        return parsed

    @staticmethod
    def _event_key(source: str, title: str, published_at: datetime, symbols: list[str]) -> str:
        raw = "|".join((source.lower(), title.lower(), published_at.isoformat(), ",".join(symbols)))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()
