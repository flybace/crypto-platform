"""Rule-based news intelligence (layer 1): analyze -> cluster -> score -> advise.

No LLM required. Deterministic, auditable, and safe to replay: every advice
carries the event it came from, and the replay gate only ever looks at events
whose ``published_at`` is not in the future relative to each candle.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
import re
from typing import Iterable


# ---------------------------------------------------------------------------
# Symbol mapping: keyword -> "BASE/USDT". Word-boundary matching; cashtags
# like $BTC are matched explicitly.
# ---------------------------------------------------------------------------
SYMBOL_KEYWORDS: dict[str, tuple[str, ...]] = {
    "BTC/USDT": ("bitcoin", "btc"),
    "ETH/USDT": ("ethereum", "eth", "ether"),
    "SOL/USDT": ("solana", "sol"),
    "BNB/USDT": ("binance coin", "bnb"),
    "XRP/USDT": ("xrp", "ripple"),
    "DOGE/USDT": ("dogecoin", "doge"),
    "ADA/USDT": ("cardano", "ada"),
    "AVAX/USDT": ("avalanche", "avax"),
    "LINK/USDT": ("chainlink", "link"),
    "TON/USDT": ("toncoin", "ton"),
    "TRX/USDT": ("tron", "trx"),
    "DOT/USDT": ("polkadot", "dot"),
    "LTC/USDT": ("litecoin", "ltc"),
    "NEAR/USDT": ("near protocol", "near"),
    "ARB/USDT": ("arbitrum", "arb"),
    "OP/USDT": ("optimism", "op"),
    "ATOM/USDT": ("cosmos", "atom"),
    "INJ/USDT": ("injective", "inj"),
}

# Ambiguous short keywords that must not match inside other words.
_AMBIGUOUS = frozenset({"eth", "sol", "ton", "op", "dot", "near", "atom", "link", "trx", "ada", "inj", "arb"})


def _keyword_pattern(keyword: str) -> "re.Pattern[str]":
    if keyword in _AMBIGUOUS:
        return re.compile(r"(?<![a-z0-9])" + re.escape(keyword) + r"(?![a-z0-9])")
    return re.compile(r"(?<![a-z0-9])" + re.escape(keyword) + r"(?![a-z0-9])")


_SYMBOL_PATTERNS: dict[str, tuple["re.Pattern[str]", ...]] = {
    symbol: tuple(_keyword_pattern(keyword) for keyword in keywords)
    for symbol, keywords in SYMBOL_KEYWORDS.items()
}
_CASHTAG_PATTERN = re.compile(r"\$([a-z]{2,6})\b")


def extract_symbols(title: str, summary: str) -> list[str]:
    """Map free text to a sorted list of ``BASE/USDT`` symbols."""
    text = f"{title or ''}\n{summary or ''}".lower()
    found: set[str] = set()
    for cashtag in _CASHTAG_PATTERN.findall(text):
        for symbol in SYMBOL_KEYWORDS:
            if symbol.split("/")[0].lower() == cashtag:
                found.add(symbol)
    for symbol, patterns in _SYMBOL_PATTERNS.items():
        if any(pattern.search(text) for pattern in patterns):
            found.add(symbol)
    return sorted(found)


# ---------------------------------------------------------------------------
# Category classification with impact weights.
# ---------------------------------------------------------------------------
CATEGORIES: dict[str, dict[str, object]] = {
    "hack": {
        "weight": 25,
        "keywords": ("hack", "hacked", "exploit", "exploited", "stolen", "drained", "breach", "attack"),
    },
    "regulation": {
        "weight": 20,
        "keywords": ("sec", "regulation", "regulatory", "lawsuit", "sued", "court", "ban", "banned",
                     "crackdown", "compliance", "doj", "settlement"),
    },
    "etf": {
        "weight": 12,
        "keywords": ("etf", "inflow", "inflows", "outflow", "outflows"),
    },
    "macro": {
        "weight": 12,
        "keywords": ("fed", "federal reserve", "rate hike", "rate cut", "interest rate", "cpi",
                     "inflation", "payroll", "nonfarm", "recession", "powell"),
    },
    "listing": {
        "weight": 8,
        "keywords": ("lists", "listing", "listed", "delist", "delisted", "delisting"),
    },
    "whale": {
        "weight": 6,
        "keywords": ("whale", "whales"),
    },
    "upgrade": {
        "weight": 8,
        "keywords": ("upgrade", "halving", "hard fork", "mainnet", "merge"),
    },
}


def classify(text: str) -> list[str]:
    """Return matching category names, highest weight first."""
    lowered = text.lower()
    matched = [
        name
        for name, spec in CATEGORIES.items()
        if any(keyword in lowered for keyword in spec["keywords"])  # type: ignore[union-attr]
    ]
    matched.sort(key=lambda name: CATEGORIES[name]["weight"], reverse=True)  # type: ignore[typeddict-item]
    return matched


# ---------------------------------------------------------------------------
# Sentiment lexicon: word -> weight. Crypto-tuned, deliberately small and
# auditable.
# ---------------------------------------------------------------------------
POSITIVE_WORDS: dict[str, int] = {
    "surge": 2, "surged": 2, "rally": 2, "rallied": 2, "bullish": 2, "breakout": 2,
    "approval": 2, "approved": 2, "inflow": 2, "inflows": 2, "record high": 2,
    "all-time high": 2, "soar": 2, "soared": 2, "adoption": 1, "optimistic": 1,
    "optimism": 1, "demand": 1, "accumulate": 1, "accumulation": 1, "rebound": 1,
    "recovery": 1, "gain": 1, "gains": 1, "jump": 1, "jumped": 1, "support": 1,
    "milestone": 1, "partnership": 1, "launch": 1, "launched": 1,
}

NEGATIVE_WORDS: dict[str, int] = {
    "hack": 3, "hacked": 3, "exploit": 3, "exploited": 3, "stolen": 2, "drained": 2,
    "crash": 2, "crashed": 2, "plunge": 2, "plunged": 2, "bearish": 2, "lawsuit": 2,
    "sued": 2, "ban": 2, "banned": 2, "crackdown": 2, "outflow": 2, "outflows": 2,
    "dump": 2, "dumped": 2, "fraud": 2, "scam": 2, "collapse": 2, "collapsed": 2,
    "fear": 1, "drop": 1, "dropped": 1, "fall": 1, "fell": 1, "decline": 1,
    "declined": 1, "warning": 1, "warned": 1, "risk": 1, "risks": 1, "risky": 1,
    "volatile": 1, "volatility": 1, "selloff": 1, "sell-off": 1, "concern": 1,
    "concerns": 1, "probe": 1, "investigation": 1, "fine": 1, "fined": 1,
}


def sentiment_score(text: str) -> float:
    """Lexicon sentiment in [-1, 1]; dampened so one word cannot dominate."""
    lowered = text.lower()
    positive = sum(weight for word, weight in POSITIVE_WORDS.items() if word in lowered)
    negative = sum(weight for word, weight in NEGATIVE_WORDS.items() if word in lowered)
    raw = positive - negative
    magnitude = positive + negative
    if magnitude == 0:
        return 0.0
    return max(-1.0, min(1.0, raw / (magnitude + 3.0)))


def sentiment_label(score: float) -> str:
    if score >= 0.25:
        return "positive"
    if score <= -0.35:
        return "risk"
    return "neutral"


# ---------------------------------------------------------------------------
# Impact scoring and analysis entry point.
# ---------------------------------------------------------------------------
def compute_impact(*, heat: int = 1, sentiment: float = 0.0, categories: Iterable[str] = ()) -> int:
    """Impact score 0-100. Deterministic; heat = covering outlet count."""
    category_bonus = max((int(CATEGORIES[name]["weight"]) for name in categories), default=5)  # type: ignore[typeddict-item]
    raw = 18 + 14 * min(max(int(heat), 1), 5) + 32 * abs(float(sentiment)) + category_bonus
    return max(0, min(100, round(raw)))


def analyze(title: str, summary: str, *, heat: int = 1) -> dict[str, object]:
    """Full rule-based analysis of one news item."""
    text = f"{title or ''}\n{summary or ''}"
    score = sentiment_score(text)
    label = sentiment_label(score)
    categories = classify(text)
    symbols = extract_symbols(title or "", summary or "")
    impact = compute_impact(heat=heat, sentiment=score, categories=categories)
    if label == "risk" and impact >= 60:
        risk_level = "high"
    elif label == "risk" or impact >= 70:
        risk_level = "medium"
    else:
        risk_level = "low"
    return {
        "symbols": symbols,
        "topics": categories,
        "sentiment": label,
        "sentiment_score": round(score, 3),
        "risk_level": risk_level,
        "impact_score": impact,
        "heat": heat,
    }


# ---------------------------------------------------------------------------
# Clustering: group near-duplicate coverage of the same story (read-time, no
# schema change). Signature = sorted significant title words.
# ---------------------------------------------------------------------------
_STOP_WORDS = frozenset({
    "the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "with", "as",
    "by", "at", "from", "is", "are", "was", "were", "be", "has", "have", "had",
    "will", "would", "could", "should", "its", "it", "this", "that", "these",
    "after", "amid", "over", "under", "up", "down", "out", "about", "into",
})


def cluster_signature(title: str) -> frozenset[str]:
    words = re.findall(r"[a-z0-9$]{3,}", (title or "").lower())
    return frozenset(word for word in words if word not in _STOP_WORDS)


def cluster_events(events: Iterable[dict[str, object]]) -> list[dict[str, object]]:
    """Group events whose title signatures overlap >= 0.5 into clusters.

    Each cluster carries merged symbols/topics, max impact, heat (member
    count), sources, and the member events newest-first.
    """
    clusters: list[dict[str, object]] = []
    for event in events:
        signature = cluster_signature(str(event.get("title", "")))
        placed = False
        for cluster in clusters:
            members = cluster["members"]
            assert isinstance(members, list)
            other = cluster_signature(str(members[0].get("title", "")))
            union = signature | other
            overlap = len(signature & other) / len(union) if union else 0.0
            if overlap >= 0.5:
                members.append(event)
                placed = True
                break
        if not placed:
            clusters.append({"members": [event]})
    result: list[dict[str, object]] = []
    for cluster in clusters:
        members = sorted(
            cluster["members"],  # type: ignore[union-attr]
            key=lambda item: str(item.get("published_at", "")),
            reverse=True,
        )
        symbols: set[str] = set()
        topics: set[str] = set()
        for member in members:
            symbols.update(str(value) for value in member.get("symbols", []))  # type: ignore[union-attr]
            topics.update(str(value) for value in member.get("topics", []))  # type: ignore[union-attr]
        impacts = [int(float(str(member.get("impact_score", "0")))) for member in members]
        sentiments = [str(member.get("sentiment", "neutral")) for member in members]
        sentiment = "risk" if "risk" in sentiments else ("positive" if "positive" in sentiments else "neutral")
        risk_levels = [str(member.get("risk_level", "low")) for member in members]
        risk_level = "high" if "high" in risk_levels else ("medium" if "medium" in risk_levels else "low")
        result.append({
            "title": members[0].get("title"),
            "summary": members[0].get("summary"),
            "published_at": members[0].get("published_at"),
            "symbols": sorted(symbols),
            "topics": sorted(topics),
            "sentiment": sentiment,
            "risk_level": risk_level,
            "impact_score": max(impacts) if impacts else 0,
            "heat": len(members),
            "sources": sorted({str(member.get("source", "")) for member in members}),
            "members": members,
        })
    result.sort(key=lambda cluster: int(cluster["impact_score"]), reverse=True)  # type: ignore[arg-type]
    return result


# ---------------------------------------------------------------------------
# Advice: machine-readable per-symbol suggestions derived from clusters.
# ---------------------------------------------------------------------------
_ADVICE_TEXT = {
    "avoid_new_entries": "暂停新开仓",
    "watch_long_signals": "关注做多信号",
    "observe": "继续观察",
}

# 策略原型: strategy_id -> archetype
STRATEGY_ARCHETYPES: dict[str, str] = {
    "sma_cross": "trend", "macd_reversal": "trend", "trend_breakout": "trend",
    "momentum": "momentum", "volume_momentum": "momentum", "volatility_breakout": "momentum",
    "rsi_rebound": "mean_reversion", "bollinger_breakout": "mean_reversion",
    "buy_and_hold": "passive", "inventory_exit": "passive",
}

_ARCHETYPE_LABEL = {
    "trend": "趋势跟踪",
    "momentum": "动量",
    "mean_reversion": "均值回归",
    "passive": "被动持有",
}

# 分策略建议话术: action -> archetype -> note
_STRATEGY_NOTES: dict[str, dict[str, str]] = {
    "avoid_new_entries": {
        "trend": "消息冲击期趋势信号失真率高,假突破多;已有仓位按原止损纪律执行,不加仓。",
        "momentum": "暂停追涨追跌;消息驱动的跳空行情滑点大,动量信号滞后容易买在情绪顶点。",
        "mean_reversion": "谨慎抄底;风险事件后波动率放大,均值回归的入场点位可能继续下移,等波动率收敛再看。",
        "passive": "持有观察;事件影响消退前不做调仓,避免在恐慌中卖出。",
    },
    "watch_long_signals": {
        "trend": "可关注做多信号,但等第一波情绪释放、价格站稳后再入场,不追第一根大阳线。",
        "momentum": "动量确认后再跟随;利好兑现时常有'买预期卖事实'的回踩,分批为宜。",
        "mean_reversion": "利好环境下超卖反弹胜率较高,可按原计划执行,但仓位不宜超过常规。",
        "passive": "维持定投/持有节奏;重大利好不是加仓理由,按既定计划执行。",
    },
    "observe": {
        "trend": "维持原信号体系;无明确方向时不为消息加戏。",
        "momentum": "维持原信号体系;中性消息不改变动量结构。",
        "mean_reversion": "维持原信号体系;按技术位执行即可。",
        "passive": "无操作;继续持有。",
    },
}

_POSITION_GUIDANCE = {
    "avoid_new_entries": "不开新仓;已有仓位收紧止损,不加仓不抄底",
    "watch_long_signals": "可按计划建仓,单笔仓位不超常规,分批入场",
    "observe": "维持原计划,不因该消息调整仓位",
}

# 建议持续时长(小时):按事件类别
_CATEGORY_DURATION_HOURS: dict[str, int] = {
    "hack": 72, "regulation": 72, "macro": 48, "etf": 48,
    "listing": 24, "whale": 24, "upgrade": 24,
}


def suggested_duration_hours(topics: Iterable[str]) -> int:
    """建议的行动持续时长(小时);取类别中最长的一个,默认 48。"""
    durations = [_CATEGORY_DURATION_HOURS.get(str(topic).lower(), 0) for topic in topics]
    return max(durations) if any(durations) else 48


def urgency_for(sentiment: str, impact: int) -> str:
    if sentiment == "risk" and impact >= 80:
        return "high"
    if sentiment == "risk" or impact >= 65:
        return "medium"
    return "low"


_URGENCY_TEXT = {"high": "高", "medium": "中", "low": "低"}


def is_major(cluster: dict[str, object]) -> bool:
    impact = int(cluster.get("impact_score", 0))  # type: ignore[arg-type]
    heat = int(cluster.get("heat", 1))  # type: ignore[arg-type]
    sentiment = str(cluster.get("sentiment", "neutral"))
    return (impact >= 65 and heat >= 2) or (sentiment == "risk" and impact >= 60)


def advise_cluster(cluster: dict[str, object]) -> list[dict[str, object]]:
    """One detailed advice record per affected symbol (or MARKET when unmapped)."""
    sentiment = str(cluster.get("sentiment", "neutral"))
    impact = int(cluster.get("impact_score", 0))  # type: ignore[arg-type]
    title = str(cluster.get("title", ""))
    topics = [str(topic) for topic in (cluster.get("topics", []) or [])]
    category = topics[0] if topics else "general"
    symbols = cluster.get("symbols") or ["MARKET"]  # type: ignore[assignment]
    if sentiment == "risk":
        action = "avoid_new_entries"
    elif sentiment == "positive" and impact >= 60:
        action = "watch_long_signals"
    else:
        action = "observe"
    urgency = urgency_for(sentiment, impact)
    duration_hours = suggested_duration_hours(topics)
    strategy_notes = {
        _ARCHETYPE_LABEL[archetype]: note
        for archetype, note in _STRATEGY_NOTES[action].items()
    }
    evidence = [
        str(member.get("title", ""))
        for member in (cluster.get("members", []) or [])  # type: ignore[union-attr]
    ][:5]
    advice = []
    for symbol in symbols:  # type: ignore[union-attr]
        advice.append({
            "symbol": symbol,
            "action": action,
            "action_text": _ADVICE_TEXT[action],
            "urgency": urgency,
            "urgency_text": _URGENCY_TEXT[urgency],
            "suggested_duration_hours": duration_hours,
            "position_guidance": _POSITION_GUIDANCE[action],
            "strategy_notes": strategy_notes,
            "reason": f"[{category}] {title[:90]}",
            "evidence": evidence,
            "impact_score": impact,
            "heat": int(cluster.get("heat", 1)),  # type: ignore[arg-type]
            "published_at": cluster.get("published_at"),
        })
    return advice


# ---------------------------------------------------------------------------
# Symbol ranking (选币榜): aggregate recent clusters per symbol.
# ---------------------------------------------------------------------------
def rank_symbols(events: Iterable[dict[str, object]], *, hours: int = 72) -> list[dict[str, object]]:
    """Rank symbols by recent news attention. Newest-weighted impact."""
    cutoff = datetime.now(UTC) - timedelta(hours=hours)
    clusters = cluster_events(events)
    per_symbol: dict[str, dict[str, object]] = {}
    for cluster in clusters:
        try:
            published = datetime.fromisoformat(str(cluster.get("published_at", "")).replace("Z", "+00:00"))
        except ValueError:
            continue
        if published < cutoff:
            continue
        age_hours = max(0.0, (datetime.now(UTC) - published).total_seconds() / 3600.0)
        decay = max(0.3, 1.0 - age_hours / float(hours))
        impact = int(cluster.get("impact_score", 0)) * decay  # type: ignore[arg-type]
        sentiment = str(cluster.get("sentiment", "neutral"))
        signed = impact * (1 if sentiment == "positive" else (-1 if sentiment == "risk" else 0))
        for symbol in cluster.get("symbols", []) or ["MARKET"]:  # type: ignore[union-attr]
            entry = per_symbol.setdefault(str(symbol), {
                "symbol": symbol, "score": 0.0, "heat": 0,
                "positive": 0, "risk": 0, "top_title": cluster.get("title"),
                "top_impact": 0.0,
            })
            entry["score"] = float(entry["score"]) + signed  # type: ignore[arg-type]
            entry["heat"] = int(entry["heat"]) + 1  # type: ignore[arg-type]
            if sentiment == "positive":
                entry["positive"] = int(entry["positive"]) + 1  # type: ignore[arg-type]
            elif sentiment == "risk":
                entry["risk"] = int(entry["risk"]) + 1  # type: ignore[arg-type]
            if impact > float(entry["top_impact"]):  # type: ignore[arg-type]
                entry["top_impact"] = impact
                entry["top_title"] = cluster.get("title")
    ranked = sorted(per_symbol.values(), key=lambda e: abs(float(e["score"])), reverse=True)  # type: ignore[arg-type]
    for entry in ranked:
        score = float(entry["score"])  # type: ignore[arg-type]
        entry["score"] = round(score, 1)
        entry["direction"] = "positive" if score > 5 else ("risk" if score < -5 else "neutral")
    return ranked
