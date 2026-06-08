"""Tiny MLP that maps a feature vector to a 0-1 confidence score.

Architecture: 28 -> 32 -> 16 -> 1, ReLU + dropout, sigmoid output.
Trained with MSE on continuous 0-1 labels (judge-similarity proxy for Source A
and 0.0 for Source B adversarials). Inference is single-threaded CPU and well
under a millisecond — fine for the live router.
"""

from __future__ import annotations

from pathlib import Path

import torch
from torch import nn

from vizuara.confidence.features import FEATURE_DIM, FEATURE_NAMES


class ConfidenceMLP(nn.Module):
    def __init__(self, in_dim: int = FEATURE_DIM, h1: int = 32, h2: int = 16, p_drop: float = 0.2):
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(in_dim, h1),
            nn.ReLU(),
            nn.Dropout(p_drop),
            nn.Linear(h1, h2),
            nn.ReLU(),
            nn.Dropout(p_drop),
            nn.Linear(h2, 1),
            nn.Sigmoid(),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.net(x).squeeze(-1)


def save(model: ConfidenceMLP, path: Path, *, meta: dict | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    torch.save(
        {
            "state_dict": model.state_dict(),
            "feature_dim": FEATURE_DIM,
            "feature_names": FEATURE_NAMES,
            "meta": meta or {},
        },
        path,
    )


def load(path: Path) -> tuple[ConfidenceMLP, dict]:
    ckpt = torch.load(path, map_location="cpu", weights_only=False)
    if ckpt.get("feature_dim") != FEATURE_DIM:
        raise RuntimeError(
            f"checkpoint feature_dim={ckpt.get('feature_dim')} != current FEATURE_DIM={FEATURE_DIM}. "
            "Re-bootstrap and retrain."
        )
    model = ConfidenceMLP(in_dim=FEATURE_DIM)
    model.load_state_dict(ckpt["state_dict"])
    model.eval()
    return model, ckpt.get("meta", {})


def infer(model: ConfidenceMLP, features: list[float]) -> float:
    with torch.no_grad():
        x = torch.tensor([features], dtype=torch.float32)
        return float(model(x).item())
