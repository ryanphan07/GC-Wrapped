# Status
Last session: 2026-11-18 — Quick task: Claude Code token tools (RTK, pyright-lsp, Serena, Ponytail) + Tooling section (D96)

## Phase & state
Phase 2 (Enrichment + evals); burst plan in CLAUDE.md. P2-2b (NER gate) is needed only before a hosted provider (D84).
consent ledger [x] two-level opt-in [x] redaction [x] labeling tool [x] synthetic labeled sets [x] consent replies (Ryan, collecting) user-text definition [x] real labels [ ] tagging pipeline [x synthetic] purge unlisted threads [x] eval harness [x synthetic] CI eval (S4b) [ ] real eval + 4B vs 1.5B comparison [ ] profiles [ ]

## Done this session
- Tools (all outside the repo): RTK 1.2.0 installed (hook in ~/.claude/settings.json; also ~/.claude/RTK.md and an @RTK.md line in ~/.claude/CLAUDE.md); pyright 1.1.414 (uv tool) + pyright-lsp 1.0.0 plugin, user scope, enabled; Serena 2.1.0 (user-scope MCP, connected; web_dashboard_open_on_launch: false); Ponytail skills (6) from DietrichGebert/ponytail in ~/.claude/skills. All four were missing before.
- .claude/settings.json: 9 deny entries added (mcp__serena__ execute_shell_command, serena_repl, onboarding, write/read/list/delete/rename/edit_memory); none removed or changed.
- docs/decisions.md: D96. CLAUDE.md: "## Tooling" section before "Session rules". .gitignore: `.serena/`.

## Deviations from brief
- npm/npx on PATH are the Windows ones (/mnt/c) and WSL has no node. pyright's bundled node needs libatomic.so.1 and sudo needs a password, so: `uv tool install "pyright[nodejs]"` (wheel ships a Linux node). Ponytail ran via `node .../nodejs_wheel/lib/node_modules/npm/bin/npx-cli.js` from that wheel (its npx stub is broken), from the scratchpad dir.
- `rtk init -g` did not patch settings.json without a TTY; reran with `--auto-patch` (backup: ~/.claude/settings.json.bak).
- Serena installed with `uv tool install -p 3.13 serena-agent@latest --prerelease=allow` (resolved 2.1.0). serena_config.yml did not exist until first MCP start; edited after.
- `check_onboarding_performed` does not exist in Serena 2.1.0; denied real names only, plus `serena_repl` (optional, runs code).
- No SQL LSP plugin exists in claude-plugins-official (SQL is 27 of ~139 tracked source-ish files), so none added.

## Checks
- RTK: `rtk --version` 1.2.0 (>= 0.42.2); `rtk gain` runs; `rtk init --show`: hook installed. Raw command: `rtk bypass`.
- Step-3 deny check (`rtk rewrite`, 25 analogs on /tmp/gcw-denytest/x, 0 executed): rewritten ones (cat->rtk read, ls, tree, grep, find, git log/diff, diff) only gain an `rtk` prefix and keep arguments verbatim; the rest are not rewritten. All Bash deny rules are `*substring*` globs, so none stops matching. Read/Edit/Write rules unaffected. No STOP.
- `claude mcp list`: serena connected. `claude plugin list`: pyright-lsp enabled, user. ~/.claude/skills: 6 ponytail skills, no .exe/.dll, lock source github.com/DietrichGebert/ponytail.
- make lint: pass. make test: 795 passed, 138 deselected (pytest), same as before this change.
- CI on this PR: watched after push.

## Not verified
- Restart Claude Code once: the RTK hook, Serena tools and LSP load at session start.
- Real data (Ryan, carried): `make real-ingest ENV_FILE=$HOME/gcw-private/real.env`, then `make text-audit ENV_FILE=$HOME/gcw-private/real.env`; paste the fixed lines. Then delete tag_sample.json once (D95).

## Open issues and questions
- Pre-existing gap: `Read(./data/**)` covers the Read tool only; no Bash rule blocks `cat data/...` (RTK rewrites it to `rtk read`, same reach). Suggested extra deny `Bash(*data/*)`-style; not applied (touches settings beyond the brief). Serena symbol tools are likewise not covered by Read deny; they rely on .gitignore.
- Carried: P2-5c items (placeholder NAME wildcard, METRICS rows before D95 not comparable); `gcw tag --no-start` does not verify the llm model; stop-word over-redaction (D82); isolation_guard multi-host URLs; real-ingest-synthetic ~323 s; label files of a purged thread stay; metrics.py reports external-eligible only.

## Suggested next session
Ryan's real text-audit result decides new seed rows; then the model upgrade, S4b CI eval, S5 embeddings.
