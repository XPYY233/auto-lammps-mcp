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
