from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(_PROJECT_ROOT / ".env")


def _required(name: str) -> str:
    v = os.getenv(name, "").strip()
    if not v:
        raise RuntimeError(
            f"Missing env var {name}. Copy .env.example to .env and fill it in."
        )
    return v


def _optional(name: str, default: str) -> str:
    return os.getenv(name, default).strip() or default


ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "").strip()
LUMENX_ADMIN_TOKEN = _required("LUMENX_ADMIN_TOKEN")
LUMENX_BASE_URL = _optional("LUMENX_BASE_URL", "https://lumenx-demo.up.railway.app")

DATA_DIR = (_PROJECT_ROOT / _optional("VIZUARA_DATA_DIR", "./data")).resolve()
DATA_DIR.mkdir(parents=True, exist_ok=True)

LUMENX_DB_PATH = DATA_DIR / "lumenx.db"
COST_LOG_PATH = DATA_DIR / "cost_log.jsonl"


def _bool(name: str, default: bool) -> bool:
    v = os.getenv(name, "").strip().lower()
    if v in ("1", "true", "yes", "on"):
        return True
    if v in ("0", "false", "no", "off", ""):
        return default
    return default


def _float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, "").strip() or default)
    except ValueError:
        return default


# Router. The auto-send flag is the project's master safety switch — leave OFF
# during development. Threshold is the MLP score above which a non-sensitive,
# non-abstained draft will be auto-sent. Tuned from the Phase 6 ROC.
AUTO_SEND_ENABLED = _bool("VIZUARA_AUTO_SEND_ENABLED", False)
AUTO_SEND_THRESHOLD = _float("VIZUARA_AUTO_SEND_THRESHOLD", 0.5)


def project_root() -> Path:
    return _PROJECT_ROOT
