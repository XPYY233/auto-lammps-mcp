# Development audit

## 2026-10-26 — repository bootstrap

- GitHub connector issue creation returned 403 for the new repository. No issue was created through that connector. Authenticated CLI created issue #1 successfully.
- Git HTTPS clone timed out. Remote repository is intact. Recovery uses GitHub Git-object APIs and verifies object hashes before establishing a local branch.
- First commit reconstruction produced a different SHA. Blob/tree hashes matched. Suspected commit serialization differences; no remote refs or history changed. Inspect original commit serialization before retry. Validation pending.

Second bootstrap check established the original commit is GitHub-signed; unsigned reconstruction was rejected. Preserve the original signature and verify serialization before creating refs.

Verified original signed commit recovered exactly via API. Blob/tree/commit hashes match. Signature trailing continuation line caused the first mismatch.

## 2026-10-27 — dependency readiness
- An import check ran before the asynchronous dependency installation finished and returned `ModuleNotFoundError: mcp`. No application or scientific task ran. Wait for installation completion before repeating the import and integration checks.

## 2026-10-27 — documentation patch
- A document batch was rejected before writing because it deleted and added README in one patch. The files were unchanged. Replace README with one update operation, then add the companion documents separately.

## 2026-10-27 — skill routing update
- The local skill update script had a Python string-quoting error and made no change. Use a triple-quoted literal, then verify the inserted routing section.

## 2026-10-27 — manual layout
- The first PDF compilation produced two overfull table cells containing long asset identifiers. Add line-break opportunities to long identifiers, recompile twice, and visually inspect the resulting pages.

## 2026-10-27 — DSH connection discovery
- DSH's documented harness configuration uses a per-server plugin entry, while the initial exporter only emitted the common mcpServers shape. Add the native harness entry and validate against the installed platform.
- Selecting DSH by bundle identifier was ambiguous because two app copies exist. No UI action occurred. Select the installed application by its full path.

## 2026-10-27 — installed DSH workspace selection
- The installed DSH desktop opened with a missing previous workspace. Its directory picker kept Open disabled even after selecting the existing new project directory. No DSH configuration was changed. The picker was cancelled; native Harness configuration is prepared separately while Codex installation proceeds.

## 2026-10-27 — DSH runtime selection and documentation paths

The user identified the old native desktop as retired and selected the existing DeepSeek Harness source distribution. Its CLI reports 0.1.7-rc.2. Read attempts using guessed package locations failed; the package inventory identifies `packages/bundle/headless` and `packages/util/home-paths`. Continue against these exact paths and the official CLI. No retired desktop source or configuration is changed.

DSH configuration inspection: the source-root Node resolver could not import `yaml`; the installed profile dependency tree contains the parser. Use that existing parser for selected non-secret configuration fields. No dependency installation or model configuration change was made.

## 2026-10-27 — Installed host validation

The official DSH CLI completed a real model turn and invoked the installed MCP tools. Codex background launch reached a session but its model refresh and request timed out; the service retains the running continuation and its log for reconciliation. No calculation was submitted. Git HTTPS push of the next candidate also timed out after the initial branch push had succeeded; existing macOS/Linux CI for the previous commit passed. Retry the publication separately and retain the exact candidate identity. One documentation read used the product checkout instead of the Harness checkout; subsequent reads use the explicit runtime directory.

## 2026-10-27 — Background continuation needs tool evidence

The installed Codex subprocess exited with code zero and a summary, but both MCP reads were rejected because the runner combined required tool approval with `approval_policy=never`. Its shell fallback could not access the Unix socket. The runner incorrectly stored this as a successful background receipt. Correction: use Codex automatic approval review, retain read/write tool annotations, and verify successful task reads plus requested output files before saving a verified receipt. The failed attempt and model output remain in the task history.

A multi-file update included a documentation context that did not match the paragraph. Inspect the applied file state before completing the remaining patch; do not rerun already applied replacements.

Installed upgrade: the wrapper captured the inner installer error but discarded it when raising CalledProcessError. The host connection points to the new candidate while the live daemon still runs the previous one. Preserve this partial state, expose the inner error and reconcile the daemon before retrying; successful tool registration is not a completed upgrade.

The next background turn successfully passed automatic review for actual MCP reads and task ownership. Repeated initial network timeouts were observed alongside an enabled macOS system proxy and absent CLI proxy environment variables. Background processes now inherit the existing system HTTP/HTTPS route while retaining explicit environment overrides. The installer wrapper now saves and prints inner step failures instead of losing them in a generic traceback.

The second installed background attempt passed MCP approval and released task ownership, but exceeded its 300-second per-turn timeout after repeated connection fallback delays. The timeout and any produced handoff file are retained. Next verification uses the discovered system network route and checks existing outputs before continuing.
