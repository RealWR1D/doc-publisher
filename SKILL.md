---
name: doc-publisher
description: >-
  Universal documentation publisher and hot-sync skill. Use this skill when the user
  asks to publish, push, update, or sync Markdown documentation, user guides, or manuals
  to a remote documentation server, static doc site, or custom API endpoint.
---

# Document Publisher & Hot-Sync Skill

A lightweight, universal agent skill designed to bridge the gap between code generation and documentation delivery. Enables an AI agent to write/refactor Markdown documentation, validate its structural semantics, and instantly push it to remote documentation sites via standard REST or local endpoints.

---

## Workflow Steps

When tasked with writing or updating documentation for a project:

### Step 1: Write or Edit Markdown
Ensure the documentation adheres to semantic hierarchy standards:
* **H1 (`# Title`)**: Single document root title at the very top.
* **Sub-title / Metadata (`> text`)**: Immediately follows H1 for version/author notes.
* **H2 (`## Section`)**: Each H2 represents an independent section card and will be indexed into the table of contents.
* **H3/H4 (`### Sub-section`)**: Sub-divisions within a section.
* **Code Blocks**: Always annotate with the language (e.g. ````bash`, ````python`, ````json`).
* **Callouts / Alerts**: Use standard `> [!TIP]`, `> [!WARNING]`, or `> [!NOTE]`.
* **Tables**: Use standard GFM tables (`| Header |`).

### Step 2: Validate Document Structure
Run the built-in linter to verify formatting and detect syntax issues:
```bash
python3 scripts/lint.py <path/to/doc.md>
```

### Step 3: Publish to Target Endpoint
Publish using the unified CLI client:
```bash
# Push using a named target profile from targets.json
python3 scripts/publish.py --target <target_name> <path/to/doc.md> --verify

# Or push directly via CLI flags without a config file
python3 scripts/publish.py --url "<API_ENDPOINT>" --token "<TOKEN>" <path/to/doc.md> --verify
```

### Step 4: Verification and Reporting
1. Confirm the CLI output reports `HTTP 200` with parsed response metadata (e.g. section count, build latency).
2. Report the live preview URL to the user.

---

## Configuration & Target Profiles

Target profiles are loaded in the following order:
1. Local workspace config: `./.doc-publisher.json` or `./targets.json` (ignored by git).
2. Skill root config: `targets.json` (ignored by git).
3. User global config: `~/.config/doc-publisher/targets.json`.
4. Environment variables: `DOC_PUBLISHER_URL`, `DOC_PUBLISHER_TOKEN`.

Refer to [targets.example.json](./targets.example.json) for profile structure.
