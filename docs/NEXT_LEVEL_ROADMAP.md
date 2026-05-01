# Roadmap

Sentinel is a project intelligence scanner for humans and AI agents. Point it at a repository to understand the project quickly, ask focused questions, and hand an agent the smallest useful context with a clear verification path.

## Core (Implemented)

- **Scan** — fast project structure and risk analysis
- **HTML/Markdown reports** — self-contained, responsive output
- **Dashboard** — live local operator GUI with metrics and suggestions
- **Context packs and prompts** — compact token-efficient agent handoff
- **Ask Sentinel** — natural-language project questions via local retrieval
- **URL analysis** — clone and scan remote repositories
- **Release check** — open-source readiness validation
- **PR summaries** — changed-file risk with suggested tests
- **Verify** — focused test detection for changed files
- **Coverage** — coverage.xml gap analysis
- **Persistent memory** — scan history, task memory, token savings

## Planned

1. **CI/CD integration** — GitHub Action that posts findings as a PR comment
2. **Static report bundles** — portable folder for GitHub Pages or artifact stores
3. **Optional AI summary** — LLM-powered identity and executive summary when an API key is provided
4. **Expanded symbol indexing** — call graphs and test relationships beyond Python
5. **MCP resources** — stable low-token context surface for AI clients
6. **Cost reporting** — token savings visibility and workflow optimization

## Design Constraint

Sentinel must remain useful without requiring a paid API call. Local static analysis, local summaries, and deterministic context packing are the default path. AI features are optional enhancements, not required functionality.
