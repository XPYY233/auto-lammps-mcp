# Progress and recovery

Show task name, completed work, current stage, latest evidence time, next action and any user decision. Read durable events and acknowledge only displayed events. Explain pause as stopping future dispatch; report the actual state of currently running jobs. Stop intent persists before cancellation.

Read run_get.evidence.output_progress alongside the scheduler state. It includes recent log text, output modification time and complete thermo rows when the column header is present. Use run_log for custom logs or more byte ranges; wait on its operation_id and continue from next_offset. Read physical time units from the native input. Keep distinct thermo blocks separate when steps reset.

Before job dispatch, run_get.evidence.preparation reports input preparation/upload with a fresh observation every 30 seconds. Policy changes return undispatched runs to prepared; read the new policy, then call run_submit within the saved authorization. Uploading old settings cannot dispatch a newly updated run.

Reuse the same operation ID when retrying an uncertain tool call. An unknown submission must be reconciled by original identity. An analysis failure reuses calculation results. An engine failure preserves the failed attempt and creates a linked retry only within authorization. Never silently change a fixed scientific condition to make a calculation succeed.

Use background Codex only when enabled and verified. Record unresolved decisions if the model or connection is unavailable. On return, read task facts and recent events rather than relying on the old chat summary. If two repairs yield no new evidence, reduce the failure and investigate shared causes.
