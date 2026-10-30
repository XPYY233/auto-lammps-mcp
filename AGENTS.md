# Auto-LAMMPS MCP development

At the start of every work turn and after context restoration, read the entire `docs/APPROVED_PLAN.md` and `docs/APPROVED_CAPABILITY_PLAN.md`, then `docs/PLAN_AMENDMENTS.md` and `docs/PROGRESS.md`. Load both full plans into the working context before choosing work; summaries and remembered plans do not replace this reading. Apply later user corrections.
For the capability-improvement work, identify the current P0–P4 stage, its unmet completion conditions, and which condition the next action closes. Report code implementation, source tests, installed-candidate checks and complete user-flow acceptance separately. Complete and record acceptance for P0, then P1, P2, P3 and P4 in order; do not skip stages. All DSH verification must use page controls in the visible official web session, never a headless/background model invocation. Existing-data reports do not close memory, validation-reuse or batch acceptance. User-facing DSH acceptance must start in a visible official session whose title and location the user can see.
Use the repository skill in `skills/auto-lammps-mcp/SKILL.md` for research usage.
The user accepted P2 and authorized P3 on 2026-10-30. Development now uses bounded stage acceptance; run the full end-to-end acceptance suite after all planned functions are developed. Report each stage's implemented outcomes, stage checks and remaining integration evidence. Keep the final acceptance ledger instead of reopening an accepted stage for one research case. Report completion or a material blocker for the user's decision. The paused W case and its scientific evidence remain preserved; stage acceptance does not change its scientific result or dispatch state.

This is a new project with a new skill. v1.0 supplies reusable assets and failure evidence. The seven historical interface/roadmap images no longer require repeated review.

- The approved product uses Codex/DSH, native scientific files, MCP/CLI, a persistent task service and Slurm. User outcomes determine scope.
- Keep the original request, scientific conditions, required outputs, attempts and failures. A scheduler exit is one event in a research workflow.
- Every physical evaluation runs on accounted HPC. Local analysis reads existing results.
- The historical service and databases keep their own controller. Read old databases through SQLite read-only connections; never initialize them with a new schema.
- Reuse audited assets and record source revision, license, modifications and validation before copying code.
- Work through Issue, `codex/` branch, PR and CI. Preserve other work and Git history.
- Record newly observed failures in `docs/AUDIT.md` before retrying. Two repairs without new evidence trigger a minimal reproduction and common-cause review.
- Publish only redistributable test inputs. Research data, author reference solutions, evaluation answers, credentials and real execution logs remain in the private acceptance workspace.
- A release candidate is identified by code revision, package hash, skill version and acceptance evidence. Run the whole user journey on the installed candidate.
- Report what the user can now finish, remaining deliverables, evidence and next action. Keep Chinese explanations concise and direct.
