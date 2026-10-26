---
name: auto-lammps-mcp
description: Install and use Auto-LAMMPS MCP from Codex or DSH to manage native scientific scripts, run accounted Slurm calculations, monitor and recover persistent tasks, analyze existing results and hand off research projects.
---

# Auto-LAMMPS MCP

For project development, begin every work turn by reading the entire original approved plan in `docs/APPROVED_PLAN.md`, followed by `docs/PLAN_AMENDMENTS.md` and current progress. Use the plan packaged with this skill when outside the checkout. Apply later user corrections while preserving the original plan. This new project uses v1.0 as a source of reusable work and lessons; the seven old images do not need repeated review.

Use `doctor` first for installation or connection work. Use `task_list` and `task_get` first when continuing research. `help` returns the actual installed version's parameters. MCP and `alm call` share one service and task store.

task_get returns compact progress with the full original request. Use task_records to page through complete run, artifact, work or stage records, and operation_get for a specific result. Follow returned cursors instead of repeatedly requesting the entire history.

## Installation and setup

Follow the repository's `docs/FOR_AGENTS_INSTALL.md`. Detect local execution and MCP support. Reuse an existing installation. Use the actual executable path returned by the installer. Keep installation progress and give a copyable resume sentence when the host needs a new session.

For DSH, use the user's official DeepSeek Harness CLI/source installation and existing model configuration. Supply `--dsh-runtime` and the intended profile to the installer. Its managed MCP block preserves the profile's other settings and credentials. Record the actual runtime version and verify a real tool call from DSH. A retired custom desktop is not an installation target.

After installation or upgrade, check MCP doctor.current_connection. current confirms this conversation loaded the same candidate as the service. reload_required means reload the host MCP connection or open a new conversation, then call doctor again. unverified_client means the old bridge has no candidate receipt. Historical host_calls show the latest calls from each platform; they do not replace the current conversation check.

Discover existing SSH aliases, project directories and validated environments before asking for missing details. Keep passwords and keys in the platform's login or system credential flow. Distinguish installed package, live service, actual host tool call, cluster connection, compute-node environment validation and background Codex continuation.

## Research interaction

Save the user's words and required results with `task_create`. Clarify scientific ambiguities together, in plain language. Preserve answered questions. Resolve ambiguous task names using material, date and stage before writing.

Give each deliverable an id and description. Use kind='archive' when the user requires an exported result package. project_export links the actual ZIP to that requirement; a handoff note alone does not satisfy it. Run completion_check after the export finishes, with no analysis or export still pending.

Check the resource catalog and existing projects. Write native LAMMPS, Python and shell files using ordinary file tools or `file_patch`. Import whole directories; preserve binary models, helpers and relative references. Derive file facts with the tools. Acquire a task lease before coordinated changes and release it before waiting for queued work.

Take a snapshot before preparing a run. Describe scientific choices, fixed conditions, resources and expected results. Save the user's authorization in the task policy. Use `run_prepare` and `run_submit` for all physical evaluations, including run 0 and engine checks. Register known run/analysis dependencies with `workflow_register` so they continue after chat closes.

Use the task's current policy. Ordinary research, author reference and formal B are separate modes. Diagnostics, file edits and analysis do not consume calculation submissions. Formal B defaults to two submissions. Original experiment identities and stopped historical campaigns remain intact.

## Progress and recovery

Show task name, completed work, current stage, latest evidence time, next action and any user decision. Read durable events and acknowledge only displayed events. Explain pause as stopping future dispatch; report the actual state of currently running jobs. Stop intent persists before cancellation.

Read run_get.evidence.output_progress alongside the scheduler state. It includes recent log text, output modification time and complete thermo rows when the column header is present. Use run_log for custom logs or more byte ranges; wait on its operation_id and continue from next_offset. Read physical time units from the native input. Keep distinct thermo blocks separate when steps reset.

Before job dispatch, run_get.evidence.preparation reports input preparation/upload with a fresh observation every 30 seconds. Policy changes return undispatched runs to prepared; read the new policy, then call run_submit within the saved authorization. Uploading old settings cannot dispatch a newly updated run.

Reuse the same operation ID when retrying an uncertain tool call. An unknown submission must be reconciled by original identity. An analysis failure reuses calculation results. An engine failure preserves the failed attempt and creates a linked retry only within authorization. Never silently change a fixed scientific condition to make a calculation succeed.

Use background Codex only when enabled and verified. Record unresolved decisions if the model or connection is unavailable. On return, read task facts and recent events rather than relying on the old chat summary. If two repairs yield no new evidence, reduce the failure and investigate shared causes.

## Analysis and delivery

Choose analyses from the scientific question and actual files. Separate averages from atom-resolved evidence, independent samples from frames, new samples from retries, program errors from physical anomalies. Preserve definitions, units, statistical windows, sample identities and uncertainty. Keep rejected hypotheses and previous plots.

Use existing plotting and structure/trajectory viewers. Show important plots inline when the host supports them, plus data/script/trajectory links. Register real artifacts and their sources and checks. `completion_check` reports missing requirements; continue those items. Export a package that a colleague can inspect and continue using.

In official DSH, use its present tool with files=[{path: absolute_path, description: short_description}] to give the user a plot and handoff ZIP. Clicking the filename previews a supported image in DSH's side panel; its app button opens the original file in the normal desktop viewer. This works with the existing text model: verify numerical outputs and file content, then present the files. Use image reading when the selected model declares image input. Explain how to open the result directly; model input capabilities and user-visible previews are separate. In Codex, show supported images inline or open the actual file in its preview panel.

For existing-data analysis: import the complete source directory; write a native Python script under the task's project_path; call analysis_submit with project-relative inputs and output names; release the lease while the queue runs. Its working directory is analysis/<operation_id>/ and inputs keep their relative names. Read operation_get for output paths and logs, use artifact_preview for the actual plot, then register checks and requirement links. Use help(method) for these exact contracts before inspecting implementation files. All long-running methods return operation_id; a waiting queue needs its task lease released.

For handoff, project_export returns a ZIP outside the project tree. Give the user that returned path directly; project_import accepts it in a new task and preserves original records. For backup, release leases, call backup_create and retain its SHA-256 receipt. A restored service waits for recovery_activate until the original controller has stopped.

For paper reproduction, determine scope from published results first. Keep author source, reference answers and independent evaluation access separate. Retrieve resources through existing authorized connections.
