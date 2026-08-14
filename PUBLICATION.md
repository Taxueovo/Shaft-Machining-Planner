# Public release checklist

Run the following from a clean environment before publishing:

```bash
pip install --require-hashes -r requirements-dev.lock.txt
python scripts/release_audit.py
python scripts/scan_secrets.py
python scripts/verify_public_sources.py
ruff check backend frontend scripts start_shaftplanner.py
pytest backend/tests -q
uvx pip-audit --path /path/to/clean-env/lib/python3.10/site-packages
```

The release audit fails on credentials, local paths, private case/job/RAG data,
large files, symlinks, workbook formulas, macros, external links, connections and
custom XML. Publish this clean repository history instead of exposing an older
private history that has not passed equivalent scanning.

After publication, enable GitHub secret scanning, push protection, Dependabot
alerts and private vulnerability reporting, and require CI on `main`.

This remains a loopback-only engineering-assistance tool. Do not deploy it as a
public multi-user web service. Rule-only mode keeps geometry local; remote model
and embedding modes transmit selected request/RAG content to the configured
provider and therefore require an approved data-retention policy.
