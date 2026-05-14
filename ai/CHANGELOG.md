# `chat_request.json` snapshot history

The live file is `ai/chat_request.json`. Before each tuning round we copy it to `chat_request_v{N}.json` so changes are rollback-able.

| Version | Date       | Notes |
|---------|------------|-------|
| v1      | original   | Pre-tuning baseline. |
| v2      | 2025-05-13 | After PLAN_MORE_LOCAL_AI_FUNCTIONS phases 1–4. |
| v3      | 2025-05-13 | Pre-Phase-8 baseline. |
| v4      | 2025-05-13 | Post-Phase 8. |
| v5      | 2025-05-13 | Pre PLAN_ML_LIBS — UMAP / HDBSCAN / PageRank / KeyBERT / Louvain / IsolationForest / BERTopic / vulture. |
| v6      | 2026-05-14 | Pre tool-catalog rewrite (single-source-of-truth routing rules + concise descriptions + bucketed cheat-sheet). |
