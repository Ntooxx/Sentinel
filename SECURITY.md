# Security Policy

## Reporting a Vulnerability

If you discover a security vulnerability in Sentinel, please report it privately.

**Do not open a public issue.**

Email: contact@sentinel-agent.dev

Include:

- Description of the vulnerability
- Steps to reproduce
- Affected versions
- Potential impact

We aim to acknowledge reports within 48 hours and provide a fix timeline within 5 business days.

## Scope

Sentinel 2.0 is a local-first static analysis tool. It:

- reads and parses local files and repository structures;
- writes only `.sentinel/quality.db*` and requested report output;
- optionally invokes local `git` for discovery and churn.

Sentinel does **not**:

- transmit data over the network during `scan`, `report`, or `watch`;
- clone remote repositories;
- store full source contents in the index;
- execute scanned code or tests;
- perform a security audit of the scanned repository.

The `security` dimension is a small set of basic pattern checks (secret-like strings, dangerous dynamic execution, debug flags). It can miss real vulnerabilities and can flag test fixtures. Matches are redacted in every output format. See [`docs/PRIVACY_AND_SAFETY.md`](docs/PRIVACY_AND_SAFETY.md).

## Supported Versions

| Version | Supported |
|---|---|
| 2.0.x | Yes |
| 1.x | No |
| < 1.0 | No |

## Disclosure Policy

We will coordinate a disclosure date with the reporter after a fix is available.
