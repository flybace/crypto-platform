"""Unit tests for the rule-based news intelligence layer."""

from datetime import UTC, datetime, timedelta

from backend.app.services import news_intel


def test_extract_symbols_maps_keywords_and_cashtags():
    symbols = news_intel.extract_symbols(
        "Bitcoin ETF inflows hit record as $BTC rallies",
        "Ethereum also gained while $ETH traders watched.",
    )
    assert "BTC/USDT" in symbols
    assert "ETH/USDT" in symbols


def test_extract_symbols_avoids_ambiguous_substrings():
    # "operation" must not map to OP/USDT; "eth" inside "ethereum" is fine.
    symbols = news_intel.extract_symbols("Operation update on the network", "")
    assert "OP/USDT" not in symbols
    assert symbols == []


def test_classify_hack_and_regulation():
    topics = news_intel.classify("SEC sues exchange after $200M hack drains funds")
    assert topics[0] == "hack"  # highest weight first
    assert "regulation" in topics


def test_sentiment_score_sign():
    assert news_intel.sentiment_score("Bitcoin surges to record high on ETF inflows") > 0.2
    assert news_intel.sentiment_score("Exchange hacked, $50M stolen in exploit") < -0.3
    assert news_intel.sentiment_label(0.5) == "positive"
    assert news_intel.sentiment_label(-0.6) == "risk"
    assert news_intel.sentiment_label(0.0) == "neutral"


def test_analyze_full_pipeline():
    result = news_intel.analyze(
        "SEC delays Bitcoin ETF decision amid market fear",
        "The SEC postponed its ruling; traders fear further downside.",
    )
    assert "BTC/USDT" in result["symbols"]
    assert "regulation" in result["topics"]
    assert result["sentiment"] in {"neutral", "risk"}
    assert 0 <= result["impact_score"] <= 100


def test_compute_impact_scales_with_heat_and_sentiment():
    low = news_intel.compute_impact(heat=1, sentiment=0.0, categories=["listing"])
    high = news_intel.compute_impact(heat=4, sentiment=-0.8, categories=["hack"])
    assert high > low
    assert 0 <= low <= 100 and 0 <= high <= 100


def test_cluster_events_groups_same_story():
    now = datetime.now(UTC).isoformat()
    events = [
        {"title": "Bitcoin ETF inflows hit record high", "published_at": now,
         "symbols": ["BTC/USDT"], "topics": ["etf"], "sentiment": "positive",
         "risk_level": "low", "impact_score": "70", "source": "coindesk"},
        {"title": "Record high Bitcoin ETF inflows reported", "published_at": now,
         "symbols": ["BTC/USDT"], "topics": ["etf"], "sentiment": "positive",
         "risk_level": "low", "impact_score": "65", "source": "cointelegraph"},
        {"title": "Solana network upgrade completed", "published_at": now,
         "symbols": ["SOL/USDT"], "topics": ["upgrade"], "sentiment": "neutral",
         "risk_level": "low", "impact_score": "40", "source": "coindesk"},
    ]
    clusters = news_intel.cluster_events(events)
    assert len(clusters) == 2
    etf_cluster = next(c for c in clusters if "BTC/USDT" in c["symbols"])
    assert etf_cluster["heat"] == 2
    assert etf_cluster["impact_score"] == 70
    assert set(etf_cluster["sources"]) == {"coindesk", "cointelegraph"}


def test_is_major_requires_heat_or_strong_risk():
    assert news_intel.is_major({"impact_score": 80, "heat": 2, "sentiment": "positive"})
    assert news_intel.is_major({"impact_score": 65, "heat": 1, "sentiment": "risk"})
    assert not news_intel.is_major({"impact_score": 40, "heat": 1, "sentiment": "neutral"})
    assert not news_intel.is_major({"impact_score": 90, "heat": 1, "sentiment": "positive"})


def test_advise_cluster_risk_blocks_entries():
    cluster = {
        "title": "Major exchange hacked, withdrawals halted",
        "symbols": ["BTC/USDT", "ETH/USDT"],
        "topics": ["hack"],
        "sentiment": "risk",
        "impact_score": 95,
        "heat": 3,
        "published_at": datetime.now(UTC).isoformat(),
    }
    advice = news_intel.advise_cluster(cluster)
    assert len(advice) == 2
    assert all(item["action"] == "avoid_new_entries" for item in advice)
    assert all(item["action_text"] == "暂停新开仓" for item in advice)


def test_rank_symbols_weights_recent_and_signed():
    now = datetime.now(UTC)
    events = [
        {"title": "Bitcoin rallies hard", "published_at": now.isoformat(),
         "symbols": ["BTC/USDT"], "topics": [], "sentiment": "positive",
         "risk_level": "low", "impact_score": "80", "source": "a"},
        {"title": "Ethereum hack confirmed", "published_at": now.isoformat(),
         "symbols": ["ETH/USDT"], "topics": ["hack"], "sentiment": "risk",
         "risk_level": "high", "impact_score": "90", "source": "a"},
        {"title": "Old bitcoin news", "published_at": (now - timedelta(hours=100)).isoformat(),
         "symbols": ["BTC/USDT"], "topics": [], "sentiment": "positive",
         "risk_level": "low", "impact_score": "80", "source": "a"},
    ]
    ranked = news_intel.rank_symbols(events, hours=72)
    by_symbol = {item["symbol"]: item for item in ranked}
    assert by_symbol["ETH/USDT"]["direction"] == "risk"
    assert by_symbol["BTC/USDT"]["direction"] == "positive"
    # Old event is outside the window: only the fresh positive counts for BTC.
    assert by_symbol["BTC/USDT"]["heat"] == 1
