# Sentinel Benchmark Fixtures

These small public fixtures exercise Sentinel detection paths:

- `python_app`
- `node_app`
- `go_service`
- `rust_cli`
- `cpp_repo`
- `docs_heavy`
- `generated_heavy`

Run the full benchmark across all fixtures:

```bash
python sentinel.py benchmark . --fast
```

Or benchmark a single fixture:

```bash
python sentinel.py scan benchmarks/fixtures/python_app --fast
```
