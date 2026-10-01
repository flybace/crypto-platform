"""In-memory manifest catalog with idempotent dataset registration."""

from domain.history import DatasetManifest


class ManifestCatalog:
    def __init__(self) -> None:
        self._manifests: dict[str, DatasetManifest] = {}

    def register(self, manifest: DatasetManifest) -> DatasetManifest:
        existing = self._manifests.get(manifest.dataset_id)
        if existing is not None and existing != manifest:
            raise ValueError(f"dataset id already points to different content: {manifest.dataset_id}")
        self._manifests[manifest.dataset_id] = manifest
        return manifest

    def get(self, dataset_id: str) -> DatasetManifest:
        try:
            return self._manifests[str(dataset_id).strip()]
        except KeyError as error:
            raise KeyError(f"unknown dataset: {dataset_id}") from error

    def all(self) -> tuple[DatasetManifest, ...]:
        return tuple(self._manifests[key] for key in sorted(self._manifests))
