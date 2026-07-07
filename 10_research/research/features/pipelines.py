from typing import Any

from research.features.definitions import FeatureDefinition


class FeaturePipeline:
    def __init__(self) -> None:
        self._features: dict[str, FeatureDefinition] = {}

    def add(self, feature: FeatureDefinition) -> None:
        self._features[feature.name] = feature

    def compute_all(self, data: Any) -> dict[str, float]:
        return {name: feat.compute(data) for name, feat in self._features.items()}

    def feature_names(self) -> list[str]:
        return list(self._features.keys())
