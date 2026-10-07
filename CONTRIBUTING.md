# Contributing to shaftmachiningplanner

Thank you for contributing! shaftmachiningplanner is a motor-shaft process planning system
(Python backend + frontend).

## Getting Started

1. Fork the repository and clone your fork.
2. Create a conda environment: `conda env create -f environment.yml`
3. Install reproducible development dependencies: `pip install --require-hashes -r requirements-dev.lock.txt`
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
- Run `python scripts/verify_public_sources.py` for capability workbook changes;
  automatic scraping must never overwrite engineering values. Record source URLs
  and review dates only after a human comparison with the official page.
- The local workbench and user guides use Simplified Chinese; engineering contracts
  and operation identifiers may remain English. Match the surrounding language and
  keep user-facing product naming as `shaftmachiningplanner`.
- Changes to workflow behavior must update the routing/Harness guide and relevant
  evaluation definitions. Documentation screenshots must use synthetic local inputs,
  retain truthful status/usage labels, and include source/version notes.

Thanks again for helping improve the project!
