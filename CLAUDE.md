# keep — Reflective Memory for AI Agents

## MANDATORY: This is a PUBLIC repository

This repository is open-source and public on GitHub. Every commit is visible to the world.

**NEVER commit or include:**
- API keys, tokens, secrets, credentials, or .env files
- Business plans, financial data, pricing, or revenue information
- Customer data, user information, or private communications
- Internal infrastructure details (IPs, DSNs, deployment configs)
- Content from any private repository or internal systems

If you accidentally stage private content, stop and alert the user before committing.

## Repository structure

- `keep/` — core library (keep-skill on PyPI)
- `langchain-keep/` — LangChain integration (langchain-keep on PyPI)
- `tests/` — test suite (run with `python -m pytest tests/`)
- `scripts/bump_version.py` — version management

## Release process

1. `python scripts/bump_version.py X.Y.Z`
2. Commit, tag `vX.Y.Z`, push with `--tags`
3. `python -m build && twine upload dist/keep_skill-X.Y.Z*`
4. `gh release create vX.Y.Z`

Both `keep-skill` and `langchain-keep` go to PyPI.

## Testing

```bash
python -m pytest tests/ -x -q    # full suite (~500 tests, ~90s)
python -m pytest tests/test_deferred_embedding.py -v  # specific file
```

Uses `mock_providers` fixture to avoid loading real ML models.
