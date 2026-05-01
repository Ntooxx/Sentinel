# Contributing

## Setup

```bash
git clone https://github.com/your-org/sentinel.git
cd sentinel
pip install -e .
```

## Running tests

```bash
python -m unittest discover -s tests -v
```

## Code style

```bash
pip install ruff
ruff check
```

Sentinel has zero external runtime dependencies. Do not add new dependencies without discussion.

## PR checklist

- [ ] Tests pass
- [ ] Ruff lint clean
- [ ] No new external dependencies
- [ ] CHANGELOG updated (if applicable)

## Reporting issues

Include Sentinel version, Python version, OS, and minimal reproduction steps.
