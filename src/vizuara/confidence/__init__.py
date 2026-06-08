"""Confidence Net: tiny MLP that scores a draft's safety for auto-send."""

from vizuara.confidence.features import FEATURE_NAMES, FEATURE_DIM, extract_features

__all__ = ["FEATURE_NAMES", "FEATURE_DIM", "extract_features"]
