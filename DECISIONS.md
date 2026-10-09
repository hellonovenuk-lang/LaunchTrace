# Decisions

Ambiguities met during the structural upgrade, and the conservative option
taken. Numbered by range (see PLAN.md): orchestrator D-001–099, foundation
D-100–199, domain-layer D-200–299, public-feed D-300–399, compliance
D-400–499, backtest D-500–599, rescan D-600–699, reviewer fixes D-700+.

## Orchestrator

**D-001 — Branch name.** The brief says work on `structural-upgrade`; this
session is only permitted to push to `claude/launchtrace-structural-upgrade-8umgor`.
All work is integrated on a local `structural-upgrade` branch and pushed to the
designated remote branch (same commits). `main` is never touched. If you want
the remote branch to be called `structural-upgrade`, that is a one-line push
for a human (HUMAN_ACTIONS).

**D-002 — Stability evidence is generated with network-free providers.** The
2026 journals are replayed against the recorded web evidence in
`data/web_evidence/` and the fixture Companies House registry (the real bulk
index is a 500 MB download, not available offline). That makes the snapshot
deterministic and reproducible on any machine, at the cost of lower absolute
scores than a live run (no company matches for the 2026 journals). It answers
the question it is for — did a code change move any lead — not "what would a
live run produce".

**D-003 — Python 3.11 for all verification**, matching CI and the Dockerfile,
although the sandbox default interpreter is 3.13.
