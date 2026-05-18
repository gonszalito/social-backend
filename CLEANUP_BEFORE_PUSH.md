# Cleanup Before Pushing to Remote

This repo generates local-only artifacts while developing and testing. Before pushing, make sure you **don’t commit** any of the following.

## Never commit (should be ignored by `.gitignore`)

- **Virtualenvs**
  - `.venv/`, `venv/`
- **Environment files**
  - `.env`, `.env.*`
- **Local runtime artifacts**
  - `var/` (includes NLP staging outputs like `var/nlp-staging/*.json`)
- **Private keys**
  - `keys/iam_private.pem`
- **Caches / build outputs**
  - `__pycache__/`, `.pytest_cache/`, `.coverage`, `coverage.xml`, `dist/`, `build/`, `*.egg-info/`, etc.

## Quick commands to verify you’re clean

Show what would be committed:

```bash
git status
```

If you accidentally created local artifacts and they’re tracked already, remove them from git tracking (keeps the local file):

```bash
git rm -r --cached var .venv keys/iam_private.pem .env
```

Then re-check:

```bash
git status
```

