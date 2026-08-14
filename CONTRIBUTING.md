# Contributing to Shaft Machining Planner

Thank you for contributing! Shaft Machining Planner is a motor-shaft process planning system
(Python backend + frontend).

## Getting Started

1. Fork the repository and clone your fork.
2. Create a conda environment: `conda env create -f environment.yml`
3. Install Python dependencies: `pip install -r requirements.txt`
4. Run the tests: `pytest` (from the repository root)

## Submitting Changes

- Create a feature branch from `main`: `git checkout -b feat/my-change`
- Keep changes focused; one pull request per logical change.
- Add tests for new behavior when applicable; run `pytest` before pushing.
- Open a pull request with a clear description of what and why.

## Commit Message Style

- Use conventional prefixes: `feat:`, `fix:`, `docs:`, `refactor:`, `test:`.
- Keep the first line under 72 characters.

## Code Style

- Follow PEP 8; use type hints for new public functions.
- Keep f-strings for string interpolation.
- The project is fully English by design; keep all UI strings, comments, and
  documentation in English.

Thanks again for helping improve the project!
