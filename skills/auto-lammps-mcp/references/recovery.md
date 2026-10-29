# Progress and recovery

Show task name, completed work, current stage, latest evidence time, next action and any user decision. Read durable events and acknowledge only displayed events. Explain pause as stopping future dispatch; report the actual state of currently running jobs. Stop intent persists before cancellation.

Pause also holds queued analysis, import, snapshot, export and storage work. Logs, collection and measured diagnostic checks remain available. A new failed measured check reopens dependent completed results; a later file-existence check cannot clear that numerical failure. Read the current receipt and preserve the original failed evidence.

Use task_get.artifacts[].checks_passed for the current verdict. Detailed checks retain their original passed value and add current_passed. research_metrics.completion_recorded describes current completion; historical_completion_recorded preserves an earlier completion event after a failed review reopens the task.

After a failed operation, read its error.evidence. Failed analyses retain a receipt with input hashes, the actual log_path for file_read, exit code, partial outputs and missing outputs. A script that has already run reports effect=partial. Correct the native file and submit a new analysis operation; preserve the failed operation and its outputs.

Read task_get.executor and active_background before taking over a repair. An active background operation may temporarily release its lease while awaiting analysis; follow that operation's progress. Re-read the task revision before a later write. A task-specific instruction should be handled within that task; keep other projects' shared service preferences intact.

Use task_background(enabled=false) to hold this project's future automatic continuations, and enabled=true to resume them. Read task_get.automation for the project and shared service settings. A current background turn finishes; the response lists its active operation. For immediate interruption use task_control pause, read retained work, then resume when the foreground is ready. Pending decisions survive a hold and restart. This preference does not change scientific conditions, artifact validity, active calculations or other projects.

Read run_get.evidence.output_progress alongside the scheduler state. It includes recent log text, output modification time and complete thermo rows when the column header is present. Use run_log for custom logs or more byte ranges; wait on its operation_id and continue from next_offset. Read physical time units from the native input. Keep distinct thermo blocks separate when steps reset.

Before job dispatch, run_get.evidence.preparation reports input preparation/upload with a fresh observation every 30 seconds. Policy changes return undispatched runs to prepared; read the new policy, then call run_submit within the saved authorization. Uploading old settings cannot dispatch a newly updated run.

Reuse the same operation ID when retrying an uncertain tool call. An unknown submission must be reconciled by original identity. An analysis failure reuses calculation results. An engine failure preserves the failed attempt and creates a linked retry only within authorization. Never silently change a fixed scientific condition to make a calculation succeed.

Use background Codex only when enabled and verified. Record unresolved decisions if the model or connection is unavailable. On return, read task facts and recent events rather than relying on the old chat summary. If two repairs yield no new evidence, reduce the failure and investigate shared causes.

For a long queued analysis or export, save background_request(after_operations=[operation_id]) with a precise next action, then end the model turn. The saved request can be recorded while the worker owns the task; it does not acquire its write lease. The service waits for the dependency's success or failure, then resumes with the actual evidence. Verify the returned operation_id and waiting_for before saying a follow-up is saved. expected_outputs must be real project-relative file paths, not descriptions such as "final ZIP" or "passing check".

For LAMMPS command/state failures, read the complete error and the matching input lines. Call lammps_reference for the recorded engine version and lammps_input_review before resubmitting. After two repairs with the same error, compare actual compute consumers, output cadence and state-changing commands with the official restrictions; establish new evidence before the next dispatch.
