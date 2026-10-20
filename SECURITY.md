# Security policy

## Reporting a vulnerability

Please do not open a public issue for a vulnerability that could expose API
keys, local files, or user data. Instead, use GitHub's private vulnerability
reporting feature for this repository when available.

## Secrets and local data

- Store API keys only in `.env`; never commit them.
- Uploaded papers and generated artifacts live under `tasks/` and are excluded
  from Git.
- `llm_wiki/raw/`, `llm_wiki/wiki/`, `usage/`, and local build outputs are also
  excluded because they may contain user or research data.
- Rotate a key immediately if it is ever committed, even if the commit is later
  deleted.

