# Security Policy

## Reporting a Vulnerability

Report security issues privately to the repository owner (do not open a public
issue). Include affected files, a description, and a minimal reproduction.

## Secrets

- **Never commit secrets.** Credentials, API keys and access tokens must be
  supplied via environment variables or a secrets manager, never committed.
- If a secret was ever committed, treat it as compromised: rotate it, purge it
  from Git history, and force-push only after coordinating clones.

## Scope

This is educational/research software. Nothing in this repository is intended
for production trading or live deployment without additional hardening.
