# AGENTS.md

`achterhus-telemetry-api` is a FastAPI backend for a service telemetry application.

## Repository layout

- This is a Python 3.12+ project using the `src/` layout.
- Package code lives in `src/telemetry/`.
- Tests live in `tests/` and use pytest.
- Dependencies and tool configuration are defined in `pyproject.toml`; `uv.lock` is committed.
- `pyproject.toml` is the project source of truth for Python version, dependency groups, Ruff settings, mypy settings, pytest configuration, and coverage thresholds. If a configuration question arises, check `pyproject.toml` before relying on editor defaults.

## Development commands

Use `uv` from the repository root:

```bash
uv sync --locked --all-extras --dev
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy .
```

## Conventions

- Target Python 3.12 or newer.
- Use 4 spaces and a maximum line length of 88 characters.
- Use double-quoted strings, consistent with Ruff format configuration.
- Add explicit annotations for functions, parameters, and return values.
- Avoid broad refactors and unrelated formatting changes.
- Do not add comments that merely restate the code. Add a comment only when the reason for a non-obvious decision cannot be expressed clearly in code.

## Validation workflow

Run the same checks that CI runs before submitting changes:

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy .
uv run pytest
```

Use focused tests during development and the full suite before final submission.

## CI and pre-commit

The authoritative pipeline is in [.github/workflows/ci.yml](.github/workflows/ci.yml). The project also uses [.pre-commit-config.yaml](.pre-commit-config.yaml) for local quality hooks.

Do not weaken lint, type, test, or coverage settings to make a change pass. Fix the underlying code or add focused tests for the behavior being changed.
