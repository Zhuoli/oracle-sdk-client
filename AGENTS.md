# Repository Guidelines

OCI SSH Sync generates bastion-ready SSH configs for Oracle Cloud Infrastructure. Use this guide to align contributions with the current tooling.

## Project Structure & Module Organization
- `tools/src/oci_client/`: reusable client library (auth, models, utils). All developers and automation, including this LLM agent, must reuse its helpers for OCI client initialization so authentication stays consistent. For example:
  ```python
  # Leverage ssh_sync's session-token workflow so operators authenticate the same way here.
  profile_name = setup_session_token(context.project, context.stage, context.region)

  client = create_oci_client(context.region, profile_name)
  ```
- `tools/src/ssh_sync.py`: CLI entry point; reads `meta.yaml` and writes `ssh_config_<project>_<stage>.txt` under a runtime `ssh_configs/` directory.
- `tools/tests/`: pytest suite mirroring module names; keep fixtures near their consumers.
- `tools/pyproject.toml`, `poetry.lock`, `mypy.ini`, `setup.cfg`: single source for dependencies, formatting, type hints, and lint rules.
- Root `Makefile`: canonical automation surface—prefer extending it over ad-hoc scripts.

## Build, Test, and Development Commands
- `make install`: install Poetry environment inside `tools/`.
- `make dev-setup`: install deps and register pre-commit hooks.
- `make ssh-sync PROJECT=<name> STAGE=<env>`: run the generator; convenience targets cover common stage/project pairs.
- `make test`, `make test-verbose`, `make test-coverage`: execute pytest with optional verbosity and coverage on `src/oci_client`.
- `make format`, `make lint`, `make type-check`: Black+isort, flake8 (line length 140), and mypy (Python 3.9 target).
- `make check`: run the full quality gate in one command.

## Coding Style & Naming Conventions
- Python 3.9+, 4-space indent, favour explicit typing (mypy disallows untyped defs and implicit Optional).
- Black line length 100; isort uses the Black profile. Keep docstrings concise and focused on business rules.
- Modules, functions, and variables stay `snake_case`; tests follow `test_<feature>.py`.
- Run `make format` before committing to avoid churn.

## Testing Guidelines
- Default to `make test`; use `poetry run pytest -k <pattern>` for targeted runs.
- Add regression coverage for bastion selection, token refresh, and multi-region branching.
- Record manual validation whenever interacting with live OCI tenants or generated SSH configs.

## Commit & Pull Request Guidelines
- Follow existing history: short, imperative commit titles (e.g., `fix static check`). Squash cosmetic tweaks where possible.
- PRs should explain the problem, highlight risky areas, list validation commands, and link issues or tickets when relevant.
- Include sample SSH config diffs or CLI output whenever the generator behaviour changes.

## Security & Configuration Tips
- Do not commit real OCI credentials, session tokens, or generated configs; rely on local `.gitignore` hygiene.
- Validate `meta.yaml` edits with `make ssh-sync` in a sandbox tenancy before sharing.
