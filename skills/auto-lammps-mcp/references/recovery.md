# Progress and recovery

Show task name, completed work, current stage, latest evidence time, next action and any user decision. Read durable events and acknowledge only displayed events. Explain pause as stopping future dispatch; report the actual state of currently running jobs. Stop intent persists before cancellation.

After a failed operation, read its error.evidence. Failed analyses retain a receipt with input hashes, the actual log_path for file_read, exit code, partial outputs and missing outputs. A script that has already run reports effect=partial. Correct the native file and submit a new analysis operation; preserve the failed operation and its outputs.

Read task_get.executor and active_background before taking over a repair. An active background operation may temporarily release its lease while awaiting analysis; follow that operation's progress. Re-read the task revision before a later write. A task-specific instruction should be handled within that task; keep other projects' shared service preferences intact.

Read run_get.evidence.output_progress alongside the scheduler state. It includes recent log text, output modification time and complete thermo rows when the column header is present. Use run_log for custom logs or more byte ranges; wait on its operation_id and continue from next_offset. Read physical time units from the native input. Keep distinct thermo blocks separate when steps reset.

Before job dispatch, run_get.evidence.preparation reports input preparation/upload with a fresh observation every 30 seconds. Policy changes return undispatched runs to prepared; read the new policy, then call run_submit within the saved authorization. Uploading old settings cannot dispatch a newly updated run.

Reuse the same operation ID when retrying an uncertain tool call. An unknown submission must be reconciled by original identity. An analysis failure reuses calculation results. An engine failure preserves the failed attempt and creates a linked retry only within authorization. Never silently change a fixed scientific condition to make a calculation succeed.

Use background Codex only when enabled and verified. Record unresolved decisions if the model or connection is unavailable. On return, read task facts and recent events rather than relying on the old chat summary. If two repairs yield no new evidence, reduce the failure and investigate shared causes.
