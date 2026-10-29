---
name: auto-lammps-mcp
description: Install and use Auto-LAMMPS MCP from Codex or DSH to manage native scientific scripts, run accounted Slurm calculations, monitor and recover persistent tasks, analyze existing results and hand off research projects.
---

# Auto-LAMMPS MCP

Use native scientific files with the persistent task service from Codex or official DeepSeek Harness. MCP and `alm call METHOD --json '{...}'` share the same service.

## Choose the relevant guide

Read the linked guide before its phase. Resolve paths against this skill's directory (DSH returns it as resourceBase). Use the host's file reader; read long documents in consecutive ranges until complete.

- Installing, connecting or upgrading: [installation](references/installation.md). Call MCP `doctor`; current_connection.state must be `current` to confirm this conversation's connection. CLI calls and historical host_calls do not confirm it.
- Starting or changing research: [research](references/research.md). Preserve the user's words, fixed conditions and required results; import complete projects and write native scripts.
- Reusing work or adding repeats: [memory and validation](references/memory-reuse.md). Search local evidence, verify changed components only, then prepare production batches.
- Monitoring, controlling or recovering: [recovery](references/recovery.md). Read durable state and evidence; preserve failures and reuse uncertain operation IDs.
- Analyzing, showing or handing off results: [delivery](references/delivery.md). Link real files to requirements, export the actual package, then run completion_check.

For continuation, start with task_list and task_get. Resolve ambiguous names before writing. Use help(method) for current parameters instead of inspecting implementation files. help() lists the first catalog page; follow next_offset while more is true to discover further tools. Acquire task ownership for coordinated mutations and release it while waiting for queued work.

## Read complete results

task_get gives compact progress and the original request. Page through task_records for run, artifact, work and stage history. operation_get returns one operation; large results preserve identifiers and supply operation_read links. Follow path and next_offset until more is false. Paths contain literal field names or array-index strings; snapshot files use ['result','files']. Text offsets count characters. Complete records remain in exports.

Any oversized MCP reply, including an explicit full-detail read, keeps the complete response in the service and returns response_read links. Follow response_id, literal path and next_offset to read missing fields or text; these links survive a new conversation and service restart. A summary is not a complete dataset. Long errors also link to their original diagnostic and recovery text.

For operation_read/response_read, offset starts at zero and limit/text_limit are positive requested maxima. The service returns at most 50 items or 4096 characters per page, with smaller pages when needed. Keep the returned next_offset; larger requests return their applied limits. Use these tools and task_records to read history.

## Execution and feedback

All physical evaluations, including run 0 and engine checks, use accounted HPC through run_prepare/run_submit. Read the task's current policy and authorization; diagnostics, edits and existing-data analysis are separate from calculation submissions. Ordinary research, reference runs and formal B keep their own rules. Preserve stopped historical campaigns.

Show task name, completed work, current stage, latest evidence time, next action and needed decisions. Reuse existing outputs and tools. Never change a fixed scientific condition silently. Completion requires the user's requested artifacts and checks, including a real archive when requested.

## Project development

At every development work turn and after context restoration, read the entire original [approved plan](references/APPROVED_PLAN.md) and the entire [capability-improvement plan](references/APPROVED_CAPABILITY_PLAN.md), then [amendments](references/PLAN_AMENDMENTS.md) and the checkout's current progress. Load the full texts into the working context; summaries do not replace them. Apply later user corrections.

Locate the current P0–P4 stage and the completion condition the next action addresses. Complete and record acceptance for P0, P1, P2, P3 and P4 in order; do not skip stages. All DSH verification uses visible official page controls, never a headless/background model invocation. Distinguish implemented code, source tests, installed checks and complete user-flow acceptance. A report from existing logs does not establish memory reuse or batch acceptance. For user-facing DSH acceptance, verify the visible official session title and location before starting. This is a new project using v1.0 assets and lessons; the seven historical images need no repeated review. Keep the README's fixed installation prompt unchanged.
