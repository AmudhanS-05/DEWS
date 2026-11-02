"""Model containers that must unpickle from any entry point (kept out of ml/s3_model.py, which runs as __main__)."""
from __future__ import annotations

import numpy as np


def _logit(p):
    p = np.clip(p, 1e-6, 1 - 1e-6)
    return np.log(p / (1 - p))


def stack_X(p0: np.ndarray, rain: np.ndarray) -> np.ndarray:
    return np.column_stack([_logit(p0), np.log1p(np.clip(rain, 0, None))])


class FoldEnsemble:
    """v1 final model = mean probability of the leave-one-year-out fold models whose out-of-fold scores set tau
    (so the threshold is applied on the same probability scale it was chosen on)."""
    def __init__(self, design: str, models: list):
        self.design, self.models = design, models

    def predict_proba_from(self, p0: np.ndarray, X_rain: np.ndarray, X_joint=None) -> np.ndarray:
        if self.design == "stack":
            return np.mean([m.predict_proba(stack_X(p0, X_rain))[:, 1] for m in self.models], axis=0)
        return np.mean([m.predict_proba(X_joint)[:, 1] for m in self.models], axis=0)
