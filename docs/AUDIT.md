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

## 2026-10-27 — Completion must include the actual handoff package

The installed DSH analysis flow delivered its plot, statistics and ZIP, but completion_check returned complete before project_export ran: a HANDOFF.md artifact had been linked to the package requirement. Add an explicit archive requirement kind and validate the produced ZIP and current source manifest before satisfying it. Also keep pending analysis/export work visible in completion checks. Replay this observed sequence and the installed handoff flow.

The requirement patch included an unrelated earlier context after a later section, so atomic verification rejected it. Apply the requirement and delivery changes in file order.

## 2026-10-27 — Repeated macOS service replacement failure

A second installed upgrade returned service_install_failed and restored the previous daemon. The wrapper now preserved that failure, but launchctl stderr was still missing. Two failures require a common-cause check: preserve launchctl diagnostics, wait for service removal before registering the replacement, and compare the live interpreter with the target, rather than only comparing plist contents. Also found that adding archive metadata invalidated unrelated plots; scope requirement invalidation to affected requirement IDs and retain calculations when scientific constraints are unchanged.

## 2026-10-27 — Derived result freshness

Two targeted checks failed: editing original data outside the service left a downstream report preview marked current, and file_patch marked only direct outputs stale. The existing completion pass caught transitive changes later, but previews could appear current in between. Propagate known file changes immediately, check the full source chain when previewing, and let new verified registrations supersede old stale records at the same path.

The test and CI lookups used nonexistent filenames. Use the file inventory first; process and socket coverage is in test_process_integration.py. No execution state or project content was affected.

## 2026-10-27 — Background Agent and analysis queue deadlock

A focused continuation test failed: the background decision occupied the only queue consumer while waiting for its own analysis operation. Earlier tool-read and file-write checks did not exercise this dependency. Run model decisions in a separate serial consumer, retain task leases for mutations, and keep ordinary import, analysis and export work advancing. Verify the original failure and a real installed background handoff.

Wheel installation review found that the source path installed locked analysis libraries while the wheel path installed only base dependencies. Package the same dependency lock inside the verified wheel and request the analysis feature for both routes. Verify an installation into a clean environment before publishing the package.

## 2026-10-27 — Installer receipt with pip

The new installed-package CI failed on both macOS and Linux: pip wrote dependency progress to stdout before the installer's JSON receipt, causing JSONDecodeError. Local verification had used uv, whose progress goes to stderr. Route dependency installer output to stderr and verify the pip-only path as well as the built package before retrying CI.

## 2026-10-27 — Cross-platform progress and image capability

The installed DSH check returned a truncated task_get record because snapshot and work results were repeated inside recent history. The configured model also rejected read_image because its declared input does not include images. Provide a compact default task record with explicit detail pagination, and use the host's user-facing presentation path without changing the user's model configuration. The image files and MCP binary response remain intact.

A regression-test patch named the same file twice; the patch tool rejected it before applying changes. Combine edits for each file before retrying.

Two follow-up checks reproduced the impact: one 500-file snapshot produced a 119 KB task summary, and a restored export receipt still pointed to the unavailable original directory. Preserve full history behind explicit reads, return compact task progress, and resolve recovered file links against the restored directory while retaining their original paths.

The same recovery check failed after a second backup-and-restore cycle: historical receipts still contained the first source root. Preserve the complete chain of previous state roots when restoring, so all retained receipts resolve in the latest recovery directory.

## 2026-10-27 — Official Harness web startup

A repository-status check ran in the extracted Harness distribution, which has no Git metadata; subsequent project checks use the explicit product checkout. Official Harness web startup then failed because its default loopback port 3080 was occupied. Preserve the existing listener, inspect its ownership, and use an available loopback port for the official runtime acceptance. No model configuration or retired desktop is changed.

The first local suite invocation used the historical unittest runner and discovered zero tests; this project uses pytest, as declared in its CI. The official DSH directory chooser stayed pending outside the observable Safari window; reload the UI and select an existing workspace for this read-only presentation check.

## 2026-10-27 — Upload responsiveness review

A read used a nonexistent client.py filename; the RPC client is in transport.py. Review found that staging/upload shares the serial concrete-work consumer, so a long upload can delay queued analysis and has no per-run progress record. The initial reproduction omitted the required analysis input list and was rejected before reaching that condition; supply an existing input to reproduce queue behavior, then keep dispatch independent and record upload progress.

The corrected reproduction confirmed that an analysis remained pending while upload was blocked. The same dispatch review found that a policy update could leave an accepted-but-undispatched run using old rules. Return pending runs to preparation when policy changes and compare uploaded rules before actual dispatch, while preserving previous request events.

## 2026-10-27 — Candidate packaging

The local packaging invocation found that the development environment does not include the optional build frontend. Use the available uv builder against an archive of the fixed Git commit, keeping the candidate independent of subsequent audit edits. The installed source candidate remains available.

## 2026-10-27 — Isolated installation preserves host settings

A focused check confirmed that `--host none` still overwrote the global DSH skill. Independent package validation must retain the user's selected host version. Keep the packaged skill available in that environment and write global host skills only when Codex or DSH is selected.

Installed dependency metadata shows NumPy 2.5.4 requires Python 3.12 while the installer advertised 3.11. Align the package and fallback installer minimum with the locked analysis environment; uv continues selecting the tested Python 3.13 runtime.

## 2026-10-27 — Real host response budget

The final official DSH turn still marked task_get truncated. Its actual compact response was 9,444 characters: 20 recent events contributed 4,281 and seven artifacts 2,353. The earlier large-snapshot test covered repeated payloads but not realistic multi-stage history. Bound the default recent lists to five entries, retain counts and explicit pagination, and replay the same installed task through DSH before publication. A Harness search included a nonexistent package/agent directory; further searches use its package inventory.

The multi-stage regression reproduced a 10,205-character formatted summary, confirming that bounded recent-list length is needed in addition to removing repeated payloads.
