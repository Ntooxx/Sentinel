# Architecture

<p align="center">
  <img src="../logos/diagram.png" alt="Sentinel Architecture Diagram" width="80%">
</p>

## Overview

Sentinel follows a simple scan pipeline:

1. Scan the target directory and collect file metadata.
2. Diff the current scan against the most recent checkpoint.
3. Audit the project structure and quality signals.
4. Update the persistent knowledge base.
5. Generate prioritized suggestions.
6. Save a new checkpoint when the change is significant.
7. Render terminal and markdown reports.

## Components

### `src/sentinel.py`

Coordinates the full workflow, loads configuration, manages runtime paths, and exposes the CLI.

### `src/auditor.py`

Owns directory scanning, file hashing, checkpoint diffs, audit metrics, issue detection, and architecture summaries.

### `src/knowledge.py`

Stores file metadata, architecture details, dependency manifests, patterns, issues, decisions, and scan timestamps in JSON.

### `src/suggester.py`

Applies rule-based heuristics to audit output and knowledge state to propose the next action.

### `src/reporter.py`

Formats the scan result for terminal output, markdown reports, and JSON serialization.

### `src/monitor.py`

Provides the interruptible loop used for continuous monitoring mode.

### `src/utils.py`

Holds shared defaults, JSON helpers, path resolution, and small runtime utilities.

## Data Flow

```text
project files
    |
    v
ProjectAuditor.scan_directory
    |
    v
ProjectAuditor.audit_project ----> Suggester.generate_suggestions
    |                                   |
    v                                   v
KnowledgeBase.update_*            ReportGenerator.render_*
    |
    v
ProjectAuditor.create_checkpoint
```

## Storage

- `data/knowledge_base.json` keeps durable project context.
- `data/checkpoints.json` stores per-scan hashes and summary metrics.
- `data/reports/` stores archived reports.
- `data/sentinel.log` stores runtime logs.

Runtime-generated files are excluded from scans so Sentinel does not create feedback loops when monitoring itself.
