Last session: 2026-11-05 — CI negation fix, real-ingest deny rules, ENV_FILE must exist
Brief completed: yes locally (make lint test passes); CI result to be confirmed on the PR.
Done:
- ci.yml: every `! cmd` line replaced by `if cmd; then echo "<fixed reason>"; exit 1; fi` (real-ingest-synthetic sentinel checks for out1/out2/out3, the "unexpected real-ingest: ok" check, and the docker volume check). errexit ignores `!`, so before this only the last line of each run block was enforced.
- tests/test_ci_workflow.py: fails if any line in .github/workflows/*.yml matches ^\s*!\s.
- .claude/settings.json + REQUIRED_DENY: added `Bash(*gcw real-ingest*)` and `Bash(*gcwrapped.real_ingest*)`.
- config.env_file_path(): a named ENV_FILE (not unset/empty/.env) that is not an existing file raises EnvFileMissingError (ValueError) with the message "ENV_FILE names a file that does not exist"; never the path. Tests in tests/test_env_file.py.
- CLAUDE.md: "Workflow rules" section with the commit-after-tests rule. decisions.md: entry appended verbatim.
Deviations from the brief (what + why):
- Also converted `! docker volume ls ... | grep -q '^gc-wrapped_'` (not a `! grep` line) because the new test forbids any line starting with `!`.
- `python -m gcwrapped.real_ingest` is not an entry point (no __main__ block; pyproject has only the `gcw` script). I added the deny rule anyway because it also matches `python -c "from gcwrapped.real_ingest ..."`. Say if you want it removed.
- Other invocation paths: `make real-ingest` (already denied) and `gcw real-ingest`.
In progress / broken: nothing known. The previously dead checks are now live; a failure in real-ingest-synthetic would be a real finding (e.g. "sentinel" in the output of run 1/2/3).
Next up: Session C (make metrics with the dbt model/test split, shared parse loop); Ryan re-runs real-ingest after merge.
Open questions for planning: none.
Measured this session (exact value + source):
- make lint test: 228 passed, 20 deselected (was 220); ruff and mypy clean.
- grep -nE '^\s*!\s' .github/workflows/*.yml: no output.
Commits: see git log on branch fix/ci-negation-env-file
Phase 1 checklist: parser [x] loader [x] dbt staging [x] marts [x] Airflow DAG [x] dashboard [x] one-command e2e [x] real-data run [x]
