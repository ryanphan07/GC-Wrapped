Last session: 2026-11-03 — State audit (no feature work)
Brief completed: yes (audit + STATUS rewrite; nothing implemented)
Repo state (origin/main = local main = 2201a80, clean tree, no open or unmerged PRs; PRs #1-#13 all merged; CI on 2201a80 passed, run 41746638953):
a. Local synthetic run: DONE. docs/images/dashboard-synthetic.png (6efd149, PR #10) is in README line 28; Quickstart section exists (README line 11). Not re-run this session.
b. Separate real stack: DONE (9ee61c9, PR #12). All compose calls use `docker compose --env-file`, COMPOSE_PROJECT_NAME override verified, POSTGRES_HOST_PORT / DASHBOARD_PORT configurable, CI asserts gc-wrapped-ci_pgdata (ci.yml:155), .claude/settings.json denies gc-wrapped-real and real.env, decisions.md entries 2026-11-03. Airflow UI port 8080 not configurable (known).
c. Real schema: DONE (2201a80). docs/formats/instagram-schema.md is inspector output (fake names thread_001, counts, no content), no PROVISIONAL text in it.
d. Format reconciliation: NOT DONE. docs/formats/reconciliation.md does not exist. src/gcwrapped/parsers/instagram_format.py:3 still says PROVISIONAL; decisions.md:51 and the old checklist still say provisional. thread_id() returns the full folder name (not numeric suffix). Attachment identity uses full URIs (instagram.py:120), not basenames.
Landing layout (from code, landing.py + instagram_format.py):
  GCW_LANDING_HOST_DIR/           <- mounted read-only at GCW_LANDING_DIR
    export_a/                     <- one subfolder per export ROOT (landing dir itself is never a root)
      your_instagram_activity/messages/inbox/
        thread_001/message_1.json, message_2.json, photos/...
  Thread folders must NOT sit directly in the landing dir. A subfolder with no threads under that exact inbox path is silently skipped.
  `gcw check-landing` prints only "export roots: N" (or an error if 0): it never prints names, but it does not count threads or distinguish "wrong layout" from "empty", so it verifies layout only coarsely.
  Caution: the schema doc shows thread folders directly under the inspected dir, so the real inbox path is probably not confirmed against MESSAGES_DIR yet; reconciliation must check this.
In progress / broken: nothing known.
Remaining Phase 1 steps, in order:
1. Ryan compares the schema doc to instagram_format.py (root path, keys, uri shape) and writes docs/formats/reconciliation.md (aggregate differences only).
2. Update instagram_format.py, generator and fixtures to the real format; remove PROVISIONAL banner; thread_id numeric suffix and basename attachment hashing if the reconciliation says so (parser/dedup are write-it-yourself: Ryan implements).
3. Optional: check-landing prints thread count per root count total (counts only).
4. Ryan runs the real-data ingest in his own terminal and reports aggregate counts.
5. Mark Phase 1 done once `make e2e` passes on the reconciled format.
Open questions for planning: does the real inbox path match MESSAGES_DIR? Should check-landing report thread counts?
Measured this session: none (git/gh output only).
Commits: (this PR, hash filled after merge)
Phase 1 checklist: parser [~] (built on provisional format, needs reconciliation) loader [x] dbt staging [x] marts [x] Airflow DAG [x] dashboard [x] one-command e2e [x] (synthetic only) real-format reconciliation [ ]
