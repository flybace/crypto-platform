"""RSS news ingestion: fetch -> analyze -> store.

Sources are public and keyless: CoinDesk / CoinTelegraph RSS plus the
alternative.me fear & greed index. Runs inside the scheduler process on a
fixed interval; never raises into the tick.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json
import logging
import time
from typing import Any
import urllib.request

import feedparser

from backend.app.services.news import NewsService
from backend.app.services import news_intel

LOGGER = logging.getLogger("crypto.news_ingest")

SOURCES: tuple[tuple[str, str], ...] = (
    ("coindesk", "https://www.coindesk.com/arc/outboundfeeds/rss/"),
    ("cointelegraph", "https://cointelegraph.com/rss"),
)
FEAR_GREED_URL = "https://api.alternative.me/fng/?limit=1"
INGEST_INTERVAL_SECONDS = 900
MAX_ITEMS_PER_SOURCE = 25


def _fetch_rss(source: str, url: str) -> list[dict[str, Any]]:
    parsed = feedparser.parse(url, request_headers={"User-Agent": "crypto-platform-news/1.0"})
    items: list[dict[str, Any]] = []
    for entry in parsed.entries[:MAX_ITEMS_PER_SOURCE]:
        title = str(getattr(entry, "title", "") or "").strip()
        if not title:
            continue
        summary = str(getattr(entry, "summary", "") or getattr(entry, "description", "") or "").strip()
        link = str(getattr(entry, "link", "") or "").strip() or None
        published = getattr(entry, "published_parsed", None) or getattr(entry, "updated_parsed", None)
        if published:
            published_at = datetime(*published[:6], tzinfo=UTC)
        else:
            published_at = datetime.now(UTC)
        items.append({
            "title": title,
            "summary": summary[:1000],
            "source": source,
            "source_url": link,
            "published_at": published_at,
        })
    return items


def _fetch_fear_greed() -> dict[str, Any] | None:
    try:
        request = urllib.request.Request(
            FEAR_GREED_URL, headers={"User-Agent": "crypto-platform-news/1.0"}
        )
        with urllib.request.urlopen(request, timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8"))
        data = (payload.get("data") or [{}])[0]
        value = int(data.get("value", 50))
        classification = str(data.get("value_classification", "Neutral"))
        timestamp = int(data.get("timestamp", time.time()))
        published_at = datetime.fromtimestamp(timestamp, tz=UTC)
        if value >= 55:
            sentiment, risk_level = "positive", "low"
        elif value <= 25:
            sentiment, risk_level = "risk", "high"
        elif value <= 40:
            sentiment, risk_level = "neutral", "medium"
        else:
            sentiment, risk_level = "neutral", "low"
        return {
            "title": f"加密恐惧贪婪指数 {value} ({classification})",
            "summary": f"Fear & Greed Index 读数 {value}，{classification}。",
            "source": "alternative.me",
            "source_url": "https://alternative.me/crypto/fear-and-greed-index/",
            "published_at": published_at,
            "symbols": [],
            "topics": ["macro", "sentiment"],
            "sentiment": sentiment,
            "risk_level": risk_level,
            "impact_score": 35,
        }
    except Exception as error:  # noqa: BLE001 - ingest must not break the tick
        LOGGER.warning("fear/greed fetch failed: %s", error)
        return None


class NewsIngestor:
    """Fetch public feeds, analyze with the rule engine, store new events."""

    def __init__(self, news_service: NewsService) -> None:
        self._news = news_service
        self._last_run_at: datetime | None = None

    def should_run(self, now: datetime | None = None) -> bool:
        current = now or datetime.now(UTC)
        if self._last_run_at is None:
            return True
        return (current - self._last_run_at).total_seconds() >= INGEST_INTERVAL_SECONDS

    def ingest_once(self) -> dict[str, Any]:
        started = datetime.now(UTC)
        ingested = 0
        skipped = 0
        errors: list[str] = []
        for source, url in SOURCES:
            try:
                items = _fetch_rss(source, url)
            except Exception as error:  # noqa: BLE001
                errors.append(f"{source}: {error}")
                LOGGER.warning("rss fetch failed for %s: %s", source, error)
                continue
            for item in items:
                try:
                    analysis = news_intel.analyze(item["title"], item["summary"])
                    event = self._news.create(
                        title=item["title"],
                        summary=item["summary"] or item["title"],
                        source=item["source"],
                        source_url=item["source_url"],
                        published_at=item["published_at"],
                        symbols=analysis["symbols"],
                        topics=analysis["topics"],
                        sentiment=str(analysis["sentiment"]),
                        risk_level=str(analysis["risk_level"]),
                        impact_score=int(analysis["impact_score"]),
                    )
                    if event.get("deduplicated"):
                        skipped += 1
                    else:
                        ingested += 1
                except Exception as error:  # noqa: BLE001
                    errors.append(f"{source}: {error}")
        fear_greed = _fetch_fear_greed()
        if fear_greed is not None:
            try:
                event = self._news.create(**fear_greed)  # type: ignore[arg-type]
                if event.get("deduplicated"):
                    skipped += 1
                else:
                    ingested += 1
            except Exception as error:  # noqa: BLE001
                errors.append(f"fear-greed: {error}")
        self._last_run_at = started
        # Major events seen in the recent window (for alerts/advice).
        recent = self._news.events(limit=100)
        recent = [
            event for event in recent
            if _parse_time(event.get("published_at")) >= started - timedelta(hours=24)
        ]
        majors = [
            cluster for cluster in news_intel.cluster_events(recent)
            if news_intel.is_major(cluster)
        ]
        return {
            "ingested": ingested,
            "skipped": skipped,
            "errors": errors[:5],
            "major_events": [
                {
                    "title": cluster.get("title"),
                    "symbols": cluster.get("symbols"),
                    "impact_score": cluster.get("impact_score"),
                    "heat": cluster.get("heat"),
                    "advice": news_intel.advise_cluster(cluster),
                }
                for cluster in majors[:10]
            ],
            "ran_at": started.isoformat(),
        }


def _parse_time(value: object) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return datetime.min.replace(tzinfo=UTC)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed
