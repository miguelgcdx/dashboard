# Project guidance

## Skills

- Load [pi-usage-python](.agents/skills/pi-usage-python/SKILL.md) for Python parsing, JSONL attribution, offline dashboard generation, and unittest work.
- Reuse available React/TypeScript skills for UI work; do not create generic duplicate skills. Python tests use standard-library unittest, not pytest.

## Module boundaries

- Keep the React `App` shell responsible for routes and layout. As PUD-7 implements Companies and Agents pages, extract focused page components rather than expanding the shell.
- Extract shared calculations only when multiple consumers actually reuse them; keep page-specific logic local.
- Keep metadata collection/attribution in `pi_usage.py` and rendering/CLI/private publication in `dashboard.py`. Split Python only at a demonstrated feature seam; the current audit warrants no source refactor.
- Preserve the legacy dashboard and timer while React remains an opt-in, separate-output preview.
