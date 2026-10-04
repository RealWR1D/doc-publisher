---
name: doc-publisher
description: >-
  Validate and publish a single Markdown document to a configured HTTP endpoint or
  local file destination, with optional build polling and content verification.
  Use when the user asks to publish, push, or sync documentation to such a target;
  writing or reviewing documentation alone does not imply publication.
---

# Document Publisher

Use the bundled standard-library Python scripts to deliver Markdown. The remote service owns rendering, building and hosting; this skill does not provide a site engine, directory sync or platform-specific SaaS integrations.

## Resolve the operation

Locate this skill directory and use absolute script paths. Keep the user's project as the working directory so project configuration can be discovered; do not change directories to the skill just to run its scripts.

Select the target the user requested. Existing authorization to publish to that target is sufficient; editing a document alone does not authorize publication. Do not invent an endpoint or silently substitute another environment. For a new or ambiguous destination, prepare the document and dry-run first, then obtain the missing target choice.

Read [configuration.md](references/configuration.md) when configuring authentication, request formats, build status, or verification. Private credentials belong in environment variables (`token_env`) or private config, never in generated examples or committed files. Use `--token` only when necessary because arguments can appear in shell history/process listings.

## Prepare and publish

Follow the user's document structure and the target's rendering requirements. The default checker expects one leading H1 (optional YAML frontmatter), no downward heading skips, closed backtick/tilde fences, and consistent basic table columns. Missing fence languages/H2 are warnings; `--strict` makes warnings fatal. If this checker is unsuitable for an intentional document format, use the appropriate validator and explicitly opt out with `--skip-lint`; do not rewrite the user's content merely to satisfy this checker.

Run a dry-run using the actual skill path:

```bash
python3 <absolute-skill-dir>/scripts/publish.py <document.md> --target <name> --dry-run --json
```

Review the resolved endpoint/destination, file hash, byte count, overwrite action and verification mode. Dry-run checks file/config validity, not remote permissions. Fix validation failures before publishing. For an already authorized target, continue without requesting redundant confirmation.

```bash
python3 <absolute-skill-dir>/scripts/publish.py <document.md> --target <name> --json
```

The publisher runs lint by default. Add `--verify` only with a configured verification URL (local-copy compares destination hash directly). Prefer SHA-256 or a unique version/content marker when the user needs proof of the update; `available` proves accessibility only. Configured build polling runs after an accepted upload, using the configured job ID mapping when supplied.

`--url` changes do not carry original target credentials, private headers, build URLs or preview URLs to another endpoint. Supply intentional new credentials/verification settings if overriding an address. Invalid configs, unknown targets and unknown target types stop the operation. Relative local destinations are resolved from the config file directory.

## Interpret results

Read both the exit code and JSON fields:

- `uploaded` / `accepted`: the endpoint accepted the upload; do not claim a completed build.
- `build-completed`: the configured status API reports completion; confirm it describes this upload.
- `copied`: local atomic copy completed.
- `reachable`: the preview URL is accessible; it may still show old content.
- `content-match` / `json-match`: the configured content/version condition matched.
- `sha256-match`: bytes or a server-provided hash matched this file.

Failures identify `stage`: validation (exit 1), upload/copy (2), build (3), verification (4). If upload succeeded and a later step failed, report that distinction. Do not blindly rerun a mutating upload: it may already have taken effect. Status and verification GETs have bounded polling; uploads have no automatic retries. Redirects are refused; use the final URL rather than forwarding credentials.

Report the actual target, completion evidence and configured preview URL when available. Never infer a live preview URL from the upload endpoint, or claim rendering quality from HTTP status alone. Do not include credentials or raw service responses in the report.
