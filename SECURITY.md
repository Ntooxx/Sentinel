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

Sentinel is a local-first static analysis tool. It:
- Reads and parses local files and repository structures
- Writes reports and cache data to `.sentinel/` directories
- Parses `coverage.xml` (when using the `coverage` command)
- Optionally clones git repositories via the `analyze-url` command

Sentinel does **not**:
- Transmit data over the network (except during `analyze-url` git clone)
- Store or process credentials
- Access system resources outside the project directory

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| 1.1.x   | Yes               |
| < 1.1.0 | No                |

## Disclosure Policy

Once a fix is released, we may publish a security advisory with technical details. Reporters will be credited unless they request anonymity.
