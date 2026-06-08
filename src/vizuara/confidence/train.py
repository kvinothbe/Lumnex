"""Train the confidence MLP on bootstrap samples.

Reads data/confidence_train.jsonl, 80/20 split, trains with MSE + Adam, reports:
- val MSE
- precision / recall / F1 at threshold 0.5 (label and prediction both binarized at 0.5)
- ROC points (TPR/FPR at 21 thresholds)
- Best F1 across thresholds + the threshold that achieved it

Saves the model to data/confidence.pt and the evaluation report to data/confidence_eval.json.
"""

from __future__ import annotations

import json
import random
from pathlib import Path

import numpy as np
import torch
from torch import nn, optim

from vizuara import config
from vizuara.confidence.bootstrap import TRAIN_DATA_PATH, load_samples
from vizuara.confidence.features import FEATURE_DIM
from vizuara.confidence.mlp import ConfidenceMLP, save


CHECKPOINT_PATH = config.DATA_DIR / "confidence.pt"
REPORT_PATH = config.DATA_DIR / "confidence_eval.json"

VAL_FRAC = 0.20
EPOCHS = 400
BATCH = 16
LR = 1e-3
EARLY_STOP_PATIENCE = 60
SEED = 17


def _split(samples, val_frac: float, seed: int):
    rng = random.Random(seed)
    idx = list(range(len(samples)))
    rng.shuffle(idx)
    n_val = max(1, int(len(samples) * val_frac))
    val = idx[:n_val]
    train = idx[n_val:]
    return [samples[i] for i in train], [samples[i] for i in val]


def _to_tensors(samples):
    X = torch.tensor([s.features for s in samples], dtype=torch.float32)
    y = torch.tensor([s.label for s in samples], dtype=torch.float32)
    return X, y


def _binary_metrics(preds: np.ndarray, labels: np.ndarray, thr: float = 0.5):
    p = preds >= thr
    l = labels >= thr
    tp = int((p & l).sum())
    fp = int((p & ~l).sum())
    fn = int((~p & l).sum())
    tn = int((~p & ~l).sum())
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    return {"tp": tp, "fp": fp, "fn": fn, "tn": tn,
            "precision": precision, "recall": recall, "f1": f1}


def _roc(preds: np.ndarray, labels: np.ndarray, n_thresholds: int = 21):
    l = labels >= 0.5
    points = []
    best_f1 = 0.0
    best_thr = 0.5
    for t in np.linspace(0.0, 1.0, n_thresholds):
        p = preds >= t
        tp = int((p & l).sum())
        fp = int((p & ~l).sum())
        fn = int((~p & l).sum())
        tn = int((~p & ~l).sum())
        tpr = tp / (tp + fn) if (tp + fn) else 0.0
        fpr = fp / (fp + tn) if (fp + tn) else 0.0
        prec = tp / (tp + fp) if (tp + fp) else 0.0
        rec = tpr
        f1 = 2 * prec * rec / (prec + rec) if (prec + rec) else 0.0
        if f1 > best_f1:
            best_f1, best_thr = f1, float(t)
        points.append({"threshold": float(t), "tpr": tpr, "fpr": fpr,
                       "precision": prec, "recall": rec, "f1": f1})
    return points, best_f1, best_thr


def train() -> dict:
    samples = load_samples(TRAIN_DATA_PATH)
    print(f"Loaded {len(samples)} samples ({sum(1 for s in samples if s.source=='A')} A + "
          f"{sum(1 for s in samples if s.source=='B')} B)")
    train_s, val_s = _split(samples, VAL_FRAC, SEED)
    print(f"Split: train={len(train_s)}  val={len(val_s)}")

    X_tr, y_tr = _to_tensors(train_s)
    X_va, y_va = _to_tensors(val_s)

    torch.manual_seed(SEED)
    np.random.seed(SEED)
    random.seed(SEED)

    model = ConfidenceMLP(in_dim=FEATURE_DIM)
    opt = optim.Adam(model.parameters(), lr=LR, weight_decay=1e-5)
    loss_fn = nn.MSELoss()

    best_val = float("inf")
    best_state = None
    patience = 0
    n_train = len(train_s)

    for epoch in range(1, EPOCHS + 1):
        model.train()
        perm = torch.randperm(n_train)
        epoch_loss = 0.0
        for i in range(0, n_train, BATCH):
            idx = perm[i:i + BATCH]
            xb, yb = X_tr[idx], y_tr[idx]
            opt.zero_grad()
            pred = model(xb)
            loss = loss_fn(pred, yb)
            loss.backward()
            opt.step()
            epoch_loss += loss.item() * xb.size(0)
        epoch_loss /= n_train

        model.eval()
        with torch.no_grad():
            val_pred = model(X_va)
            val_loss = loss_fn(val_pred, y_va).item()

        if val_loss < best_val - 1e-4:
            best_val = val_loss
            best_state = {k: v.clone() for k, v in model.state_dict().items()}
            patience = 0
        else:
            patience += 1

        if epoch % 25 == 0 or epoch == 1:
            print(f"  epoch {epoch:3d}  train_mse={epoch_loss:.4f}  val_mse={val_loss:.4f}  "
                  f"best={best_val:.4f}  patience={patience}/{EARLY_STOP_PATIENCE}")
        if patience >= EARLY_STOP_PATIENCE:
            print(f"  early stop at epoch {epoch}")
            break

    if best_state is not None:
        model.load_state_dict(best_state)

    # Final eval.
    model.eval()
    with torch.no_grad():
        val_pred = model(X_va).numpy()
        val_labels = y_va.numpy()
        train_pred = model(X_tr).numpy()
        train_labels = y_tr.numpy()

    val_at_05 = _binary_metrics(val_pred, val_labels, thr=0.5)
    roc, best_f1, best_thr = _roc(val_pred, val_labels)
    train_at_05 = _binary_metrics(train_pred, train_labels, thr=0.5)

    report = {
        "n_total": len(samples),
        "n_train": len(train_s),
        "n_val": len(val_s),
        "val_mse": float(best_val),
        "val_at_0.5": val_at_05,
        "train_at_0.5": train_at_05,
        "best_f1": best_f1,
        "best_threshold_for_f1": best_thr,
        "roc_points": roc,
    }

    save(model, CHECKPOINT_PATH, meta=report)
    REPORT_PATH.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def main() -> int:
    rep = train()
    print()
    print(f"=== Final report ===")
    print(f"val_mse:           {rep['val_mse']:.4f}")
    print(f"val @ 0.5 thr:     precision={rep['val_at_0.5']['precision']:.3f}  "
          f"recall={rep['val_at_0.5']['recall']:.3f}  F1={rep['val_at_0.5']['f1']:.3f}")
    print(f"train @ 0.5 thr:   precision={rep['train_at_0.5']['precision']:.3f}  "
          f"recall={rep['train_at_0.5']['recall']:.3f}  F1={rep['train_at_0.5']['f1']:.3f}")
    print(f"best F1 (any thr): {rep['best_f1']:.3f} at threshold {rep['best_threshold_for_f1']:.2f}")
    print(f"\nROC points (every 5th):")
    for p in rep["roc_points"][::4]:
        print(f"  thr={p['threshold']:.2f}  tpr={p['tpr']:.3f}  fpr={p['fpr']:.3f}  "
              f"prec={p['precision']:.3f}  rec={p['recall']:.3f}  f1={p['f1']:.3f}")
    print(f"\nCheckpoint: {CHECKPOINT_PATH}")
    print(f"Report:     {REPORT_PATH}")

    if rep["best_f1"] >= 0.75:
        print(f"\nExit criterion met: best F1 = {rep['best_f1']:.3f} >= 0.75")
        return 0
    print(f"\nExit criterion NOT met: best F1 = {rep['best_f1']:.3f} < 0.75")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
