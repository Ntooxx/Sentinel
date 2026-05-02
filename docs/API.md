# API

<p align="center">
  <img src="../logos/logo2.png" alt="Sentinel" width="120">
</p>

## `SentinelAgent`

Defined in `src/sentinel.py`.

### Constructor

```python
SentinelAgent(project_dir: str, config_path: Optional[str] = None)
```

### Methods

- `scan_once(print_report: bool = True) -> dict`
  Runs a single scan, audit, knowledge sync, and suggestion cycle.
- `scan_once(..., fast_mode: bool = False, compact: bool = False, output_format: str = "text") -> dict`
  Supports faster shallow scans and smaller output formats for CLI usage.
- `scan_once(..., top_suggestions: Optional[int] = None) -> dict`
  Can trim the suggestion list for lower-noise CLI output.
- `get_full_report() -> str`
  Runs a scan and returns a markdown report string.
- `get_status() -> dict`
  Returns the latest saved summary from the knowledge base without performing a new scan.
- `save_full_report(destination: Optional[str] = None) -> dict`
  Writes a markdown report to the scanned project and an archived copy to `data/reports/`.
- `run_continuous() -> None`
  Starts the continuous monitor loop.

## `ProjectAuditor`

Defined in `src/auditor.py`.

### Key Methods

- `scan_directory(...) -> dict`
  Walks the target project and returns metadata for important files.
- `audit_project(file_data: dict) -> dict`
  Computes metrics, structure, patterns, issues, architecture, health score, risk summary, and per-file risk scores.
- `diff_from_last_checkpoint(current_files: dict) -> dict`
  Computes added, modified, and deleted files.
- `create_checkpoint(file_data: dict, audit: dict) -> dict`
  Persists a new checkpoint snapshot.
- `is_significant_change(diff: dict) -> bool`
  Applies threshold rules to determine whether a checkpoint should be created.

## `KnowledgeBase`

Defined in `src/knowledge.py`.

### Key Methods

- `update_file_info(filepath, info, persist=True)`
- `remove_file(filepath, persist=True)`
- `replace_patterns(patterns, persist=True)`
- `replace_issues(issues, persist=True)`
- `update_architecture(arch, persist=True)`
- `update_dependencies(deps, persist=True)`
- `get_project_summary() -> dict`
- `export_context(max_items: int = 50) -> str`

## `Suggester`

Defined in `src/suggester.py`.

### Key Methods

- `generate_suggestions(audit, diff, knowledge) -> list`
- `format_suggestions(suggestions) -> str`

## `ReportGenerator`

Defined in `src/reporter.py`.

### Key Methods

- `render_terminal(result, include_all_suggestions=False) -> str`
- `render_markdown(result, knowledge_context="") -> str`
- `render_json(result) -> str`
- `save_markdown(report_text, destination) -> Path`
