# Sentinel Next-Level Roadmap

Sentinel is currently a local project-understanding CLI plus MCP bridge plus a Kilo file bridge. The next jump is making it an active context governor: it should decide what an agent needs, prove why that context is enough, and refresh that context as work changes.

## Highest-Leverage Features

1. Query-aware retrieval command

Add `project-sentinel retrieve axiom --query "scheduler bug"` that returns only the most relevant files, symbols, and snippets. This is the core token-saving feature: query-aware context instead of one generic context pack.

2. Symbol and call graph index

Build a lightweight Python symbol graph from AST: modules, classes, functions, imports, call hints, test relationships, and ownership. This turns Sentinel from "project summary" into "where should I look for this exact behavior?"

3. Task memory and decision ledger

Store task goals, files touched, tests run, decisions made, and unresolved risks. Future LLM calls should receive the compressed history instead of rediscovering yesterday's work.

4. Patch verifier

After edits, Sentinel should run the narrowest useful checks, summarize failures, and generate the next repair prompt. This closes the loop between context, changes, and validation.

5. Kilo native agent profile

Create a `.kilo/agents/sentinel-code.md` profile that starts every task with Sentinel, limits broad reads, and keeps file access focused. This is stronger than rules because the behavior lives in the selected Kilo agent profile.

6. MCP resources for stable context

Expose `sentinel://context/small`, `sentinel://overview`, `sentinel://focus-files`, and `sentinel://roadmap` as MCP resources. Tools are great for actions, but resources give clients a stable low-token context surface they can fetch without inventing a tool call.

7. Cost and context budget report

Track estimated tokens per Sentinel pack, raw files avoided, and repeated file reads. This makes token savings visible and helps tune the workflow.

## Practical Build Order

1. Add query-based retrieval.
2. Add AST symbol graph indexing for Python.
3. Add patch verification and task memory.
4. Add MCP resources.
5. Add cost and context budget reporting.

## Design Constraint

Sentinel should stay useful without requiring a paid API call. Local static analysis, local summaries, and deterministic context packing should remain the default path.
