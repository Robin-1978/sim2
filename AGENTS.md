# Repository Guidelines

## Project Structure

- `src/sim2/` contains the Python package. Keep platform state changes in `platform/`, persistence in `persistence/`, Agent/ReAct logic in `agents/`, prompt projection in `agent_view/` and `prompting/`, and provider adapters in `llm/`.
- `worlds/starter_village/` is an importable configuration package, not hard-coded game logic. Add new worlds as sibling configuration packages.
- `tests/` contains vertical and integration coverage. `scripts/` contains benchmarks, model checks, and long-run verification. Design and world guides live under `docs/`.

## Development Commands

Install the editable package once:

```bash
python -m pip install -e .
```

Run the normal checks before submitting changes:

```bash
python -m pytest -q
python -m compileall -q src scripts
python scripts/benchmark_platform.py --agents 1000 --runs 5
```

Use `python scripts/run_long_simulation.py --minutes 10080` for a deterministic one-week smoke run. Real-provider checks use `python scripts/verify_agent_models.py` or `sim2 simulate --provider remote`; both require `~/.sim2/llm.json`, which stores provider metadata and environment-variable names only.

## Style and Design Rules

Use Python 3.10+, four-space indentation, type hints, and small focused modules. Keep names in `snake_case`, classes in `PascalCase`, and constants in `UPPER_SNAKE_CASE`. Prefer standard-library code and existing dependencies. Format prompts as compact, readable projections; keep tool schemas in JSON Schema. Do not add per-world `if/else` branches to Platform: actions, behavior trees, rules, and effects must remain configuration-driven. Agents submit intents; Platform alone mutates SQLite state.

## Testing Guidelines

Add a focused regression test for each behavior change and keep tests deterministic with fixed configuration and provider responses. Cover transaction rollback, intent rejection, long-action progress, and Agent context boundaries when relevant. A green unit suite is not sufficient for Agent changes; inspect model outputs with the verification scripts without printing secrets or raw reasoning.

## Commits and Pull Requests

Use short imperative commit subjects, matching the existing history (for example, `Add snapshot recovery`). Keep unrelated changes separate. Pull requests should explain behavior and affected modules, link an issue when available, list commands and results, and include a sample CLI/API interaction for user-facing changes. Update the relevant world or design documentation when schemas or configuration contracts change.

## Security and Licensing

Never commit API keys, `~/.sim2/llm.json`, SQLite runtime files, `artifacts/`, or generated caches. Reference secrets through environment variables. New source and documentation are released under [Apache License 2.0](LICENSE).
