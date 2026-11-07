# Repository Guidelines

## Project Structure & Module Organization

This repository is currently an initial scaffold. Place application and library code under `src/`, keeping modules organized by feature or responsibility. Put design notes, API documentation, and contributor-facing material under `docs/`. Add tests in a dedicated `tests/` directory (or beside the code when the chosen framework convention requires it), and keep generated output in an ignored build directory such as `dist/` or `build/`. Do not commit generated artifacts, local credentials, or tool caches.

## Build, Test, and Development Commands

No build system or runtime has been configured yet, so there are no repository-specific commands to run today. When adding one, document the canonical commands here and in the project README. Prefer a small, repeatable command set, for example:

- `make build` — produce a clean production build.
- `make test` — run the complete automated test suite.
- `make lint` — run formatting and static-analysis checks.
- `make dev` — start the local development workflow.

Commands should fail on errors and should not depend on undeclared global tools.

## Coding Style & Naming Conventions

Follow the formatter and linter selected for the implementation language; commit their configuration at the repository root. Use four spaces for Python and two spaces for JSON/YAML unless the language tool enforces another standard. Use `PascalCase` for types, `camelCase` for functions and variables, and `UPPER_SNAKE_CASE` for constants where those conventions fit the language. Name files after their primary module or feature, using lowercase kebab-case or the ecosystem’s standard.

## Testing Guidelines

Add focused unit tests for new behavior and regression tests for fixed bugs. Keep test names descriptive, such as `test_rejects_expired_token`, and mirror the `src/` structure where practical. Run the full suite and lint checks before opening a pull request; record any required external services or environment variables in `docs/`.

## Commit & Pull Request Guidelines

Git history is not yet available, so use short, imperative commit subjects (for example, `Add token validation`) and keep unrelated changes separate. Pull requests should explain the behavior change, identify affected areas, link the relevant issue, and include test commands and results. Add screenshots or sample requests when changing a user-facing interface or API.

## Security & Configuration

Keep secrets in environment variables or an approved secret store, never in source or documentation. Provide safe `.env.example` entries when configuration is needed, and review dependencies and generated files before committing.
