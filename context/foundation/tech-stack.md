---
starter_id: python-library-uv
package_manager: uv
project_name: agentic-memory-system
hints:
  language_family: python
  team_size: solo
  deployment_target: pypi
  ci_provider: github-actions
  ci_default_flow: manual-promotion
  bootstrapper_confidence: best-effort
  path_taken: custom
  quality_override: false
  self_check_answers:
    typed: true
    from_official_starter: true
    conventions: true
    docs_current: true
    can_judge_agent: false
  has_auth: false
  has_payments: false
  has_realtime: false
  has_ai: false
  has_background_jobs: false
---

## Why this stack

Solo developer, 3 weeks after-hours, small scale, local-only SQLite graph store. Python wins over Go and Rust because the decisive workload — multi-seed Personalized PageRank with sentence-transformer embeddings (Slice 8) — is Python-first; porting or binding those libraries in Rust or Go would consume the available timeline before the graph core was stable. The MCP Python SDK is the reference implementation and the most mature surface for the 5-call agent interface. Package management with uv and pyproject.toml library layout follows Python Packaging Authority conventions; sqlite3 is stdlib with no extra dependency. Distribution targets PyPI eventually; v1 is local editable install. CI on GitHub Actions runs lint, type-check, and tests on push; publish is a manual step, not triggered automatically on merge.
