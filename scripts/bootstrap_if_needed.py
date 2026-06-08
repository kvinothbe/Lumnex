"""Run-once bootstrap that prepares data files on a fresh deploy.

Each step is idempotent — re-running just confirms the file exists and returns.
Order matters (each step reads from the previous):

  1. data/lumenx.db          ← /api/admin/export
  2. data/wiki/chunks.json   ← lumenx.db (no LLM)
  3. data/corpus_summary.txt ← lumenx.db (one Sonnet call, ~$0.07)
  4. data/confidence_train.jsonl ← 100 re-drafts + judges + 50 mutations (~$1.50)
  5. data/confidence.pt      ← PyTorch training (~30s, no LLM)

Skips steps whose output already exists. Safe to run on every container boot.
"""

from __future__ import annotations

import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from vizuara import config


def _step(name: str, target_path, fn) -> None:
    if target_path.exists():
        print(f"[bootstrap] SKIP {name} — {target_path} already exists")
        return
    print(f"[bootstrap] RUN  {name} — building {target_path}")
    fn()
    if not target_path.exists():
        raise RuntimeError(f"{name} did not produce {target_path}")
    print(f"[bootstrap] DONE {name}")


def main() -> int:
    # Step 1: mirror LumenX
    def s1():
        from vizuara.lumenx.sync import sync
        sync()
    _step("lumenx sync", config.LUMENX_DB_PATH, s1)

    # Step 2: build wiki chunks + markdown
    def s2():
        from vizuara.wiki.build import build
        build()
    _step("wiki build", config.DATA_DIR / "wiki" / "chunks.json", s2)

    # Step 3: corpus summary (one Sonnet call)
    def s3():
        from vizuara.context.summary import build_summary
        build_summary(force=False)
    _step("corpus summary", config.DATA_DIR / "corpus_summary.txt", s3)

    # Step 4: confidence training data (100 author + 100 judge + 50 mutations)
    def s4():
        from vizuara.confidence.bootstrap import build_source_a, build_source_b, save_samples
        save_samples(build_source_a() + build_source_b())
    _step("confidence bootstrap", config.DATA_DIR / "confidence_train.jsonl", s4)

    # Step 5: train MLP
    def s5():
        from vizuara.confidence.train import train
        train()
    _step("confidence train", config.DATA_DIR / "confidence.pt", s5)

    print("[bootstrap] all steps complete")
    return 0


if __name__ == "__main__":
    sys.exit(main())
