from dataclasses import replace
from datetime import timedelta

import pytest

from application.history import ManifestCatalog
from domain.history import DataLevel, DatasetManifest
from domain.market import MarketType

from tests.helpers import NOW


def _manifest(content: bytes = b"snapshot-1") -> DatasetManifest:
    return DatasetManifest.build(
        venue_id="binance",
        market_type=MarketType.SPOT,
        instrument_key="binance:spot:BTC/USDT",
        data_level=DataLevel.L2,
        start_at=NOW,
        end_at=NOW + timedelta(minutes=1),
        file_format="parquet",
        source="fake-replay",
        row_count=2,
        content=content,
    )


def test_manifest_hash_and_id_are_deterministic() -> None:
    first = _manifest()
    second = _manifest()

    assert first == second
    assert len(first.content_sha256) == 64
    assert first.dataset_id.endswith(first.content_sha256[:16])


def test_manifest_catalog_is_idempotent_but_rejects_conflicting_content() -> None:
    catalog = ManifestCatalog()
    manifest = _manifest()

    assert catalog.register(manifest) == manifest
    assert catalog.register(manifest) == manifest
    assert catalog.all() == (manifest,)

    conflict = replace(manifest, content_sha256="0" * 64)
    with pytest.raises(ValueError, match="different content"):
        catalog.register(conflict)
