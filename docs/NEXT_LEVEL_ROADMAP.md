# Sentinel Next-Level Roadmap

Sentinel is becoming a project intelligence scanner for both humans and AI agents. The product direction is simple: a user should be able to point Sentinel at a repository, understand the project quickly, ask focused questions, and hand an agent the smallest useful context with a clear verification path.

## Implemented Product Slice

These features form the first product-grade workflow:

1. Repo URL analyzer

Run `project-sentinel analyze-url https://github.com/user/repo` to clone a git source, scan it, and write a portable report bundle containing:

- `SENTINEL_REPORT.md`
- `SENTINEL_REPORT.html`
- `CONTEXT.md`
- `NEXT_PROMPT.md`
- `analysis.json`

2. HTML report

Run `project-sentinel report . --format html` or `project-sentinel report . --format both` to generate shareable HTML output with project identity, health, risk, issues, suggestions, focus files, and agent prompt context.

3. Better dashboard

Run `project-sentinel dashboard . --fast` for a live local operator dashboard with project identity, risk summary, suggestions, focus files, hotspots, issues, top file risks, agent prompt, and health timeline.

4. Ask Sentinel

Run `project-sentinel ask . --question "where is authentication handled?" --fast` to answer project questions using local retrieval, symbol hints, snippets, and current project understanding.

## Highest-Leverage Features

1. GitHub PR and CI integration

Add a GitHub Action and PR summary mode that posts Sentinel findings as a PR comment: changed files, risk level, suggested tests, affected components, and compact agent prompt.

2. Hosted/static report bundles

Make URL analysis produce a fully portable static folder that can be uploaded to GitHub Pages, S3, Netlify, or any artifact store.

3. Optional AI summary layer

Keep deterministic local analysis as the default, but optionally let an LLM rewrite the project identity, executive summary, and Ask Sentinel answer when an API key is configured.

4. Query-aware retrieval command

Add `project-sentinel retrieve axiom --query "scheduler bug"` that returns only the most relevant files, symbols, and snippets. This is the core token-saving feature: query-aware context instead of one generic context pack.

5. Symbol and call graph index

Build a lightweight Python symbol graph from AST: modules, classes, functions, imports, call hints, test relationships, and ownership. This turns Sentinel from "project summary" into "where should I look for this exact behavior?"

6. Task memory and decision ledger

Store task goals, files touched, tests run, decisions made, and unresolved risks. Future LLM calls should receive the compressed history instead of rediscovering yesterday's work.

7. Patch verifier

After edits, Sentinel should run the narrowest useful checks, summarize failures, and generate the next repair prompt. This closes the loop between context, changes, and validation.

8. Kilo native agent profile

Create a `.kilo/agents/sentinel-code.md` profile that starts every task with Sentinel, limits broad reads, and keeps file access focused. This is stronger than rules because the behavior lives in the selected Kilo agent profile.

9. MCP resources for stable context

Expose `sentinel://context/small`, `sentinel://overview`, `sentinel://focus-files`, and `sentinel://roadmap` as MCP resources. Tools are great for actions, but resources give clients a stable low-token context surface they can fetch without inventing a tool call.

10. Cost and context budget report

Track estimated tokens per Sentinel pack, raw files avoided, and repeated file reads. This makes token savings visible and helps tune the workflow.

## Practical Build Order

1. Finish URL analyzer, HTML reports, dashboard, and Ask Sentinel. Done.
2. Add GitHub Action and PR comment output.
3. Add portable static report bundles.
4. Add optional AI summary provider.
5. Expand language-specific symbol indexing beyond Python.
6. Add MCP resources.
7. Add cost and context budget reporting.

## Design Constraint

Sentinel should stay useful without requiring a paid API call. Local static analysis, local summaries, and deterministic context packing should remain the default path.
