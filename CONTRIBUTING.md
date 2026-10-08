# Contributing to shaftmachiningplanner

shaftmachiningplanner uses a Python backend and local web frontend for shaft machining process planning. Contributions should include reproducible input, expected behavior, and evidence relevant to the change.

## Development setup

1. Fork the repository and clone your fork.
2. Create a Python 3.10 environment. The Conda option is `conda env create -f environment.yml`.
3. Install locked development dependencies: `python -m pip install --require-hashes -r requirements-dev.lock.txt`.
4. Run `python -m pytest` from the repository root.

## Pull requests

- Create a feature branch from `main`.
- Keep each pull request focused on one logical change.
- Include regression coverage for changed behavior where applicable, and run the relevant checks before publication.
- Describe the problem, resulting behavior, validation, and material limitations.

## Commit messages

Use conventional prefixes such as `feat:`, `fix:`, `docs:`, `refactor:`, and `test:`. Keep the subject under 72 characters.

## Code and documentation conventions

- Follow PEP 8 and use type hints for new public functions.
- Use f-strings for string interpolation.
- Run `python scripts/verify_public_sources.py` for capability-workbook changes. Automatic source checks preserve engineering values. Record source URLs and review dates after comparing with the official source.
- Write public project documentation in English, using precise engineering terminology and direct descriptions of behavior. The workbench currently uses Simplified Chinese; match the surrounding interface language for UI changes. Product naming is `shaftmachiningplanner`.
- Update the architecture/harness reference and evaluation definitions when workflow behavior changes.
- Capture documentation screenshots with isolated synthetic local inputs. Preserve actual status and usage labels and record source/version details.
- Bind engineering claims to their available evidence. Identify synthetic evaluation, live-service acceptance, and factory validation separately.
