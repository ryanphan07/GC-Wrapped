# Status
Last session: 2026-11-18 — Quick task: remove Serena MCP (D97)

## Phase & state
Phase 2 (Enrichment + evals); burst plan in CLAUDE.md. P2-2b (NER gate) is needed only before a hosted provider (D84).
consent ledger [x] two-level opt-in [x] redaction [x] labeling tool [x] synthetic labeled sets [x] consent replies (Ryan, collecting) user-text definition [x] real labels [ ] tagging pipeline [x synthetic] purge unlisted threads [x] eval harness [x synthetic] CI eval (S4b) [ ] real eval + 4B vs 1.5B comparison [ ] profiles [ ]

## Done this session
- Outside the repo: `claude mcp remove serena --scope user`, `uv tool uninstall serena-agent`, deleted ~/.serena (no repo .serena/ existed).
- CLAUDE.md: Serena bullet removed from "## Tooling".
- docs/decisions.md: D97. .claude/settings.json and .gitignore unchanged.

## Deviations from brief
- none

## Checks
- Serena cause (newest log ~/.serena/logs/2026-11-18): 53 tools loaded, but "Number of exposed tools: 2: initial_instructions, serena_repl"; the claude-code context in 2.1.0 exposes a REPL facade, and serena_repl is denied (D96), so no symbol tools were usable.
- `claude mcp list`: no serena entry; `which serena`: not found; `git diff .claude/settings.json`: empty.
- make lint: pass. make test: 795 passed, 138 deselected (pytest).

## Not verified
- Real data (Ryan, carried): `make real-ingest ENV_FILE=$HOME/gcw-private/real.env`, then `make text-audit ENV_FILE=$HOME/gcw-private/real.env`; paste the fixed lines. Then delete tag_sample.json once (D95).

## Open issues and questions
- Pre-existing gap: `Read(./data/**)` covers the Read tool only; no Bash rule blocks `cat data/...` (RTK rewrites it to `rtk read`, same reach). Suggested extra deny `Bash(*data/*)`-style; not applied (touches settings beyond the brief). Serena symbol tools are likewise not covered by Read deny; they rely on .gitignore.
- Carried: P2-5c items (placeholder NAME wildcard, METRICS rows before D95 not comparable); `gcw tag --no-start` does not verify the llm model; stop-word over-redaction (D82); isolation_guard multi-host URLs; real-ingest-synthetic ~323 s; label files of a purged thread stay; metrics.py reports external-eligible only.

## Suggested next session
Ryan's real text-audit result decides new seed rows; then the model upgrade, S4b CI eval, S5 embeddings.
