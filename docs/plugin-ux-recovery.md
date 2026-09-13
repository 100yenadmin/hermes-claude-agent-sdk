# Fable plugin UX recovery — candidate, not a release

This successor improves the existing standalone whole-turn plugin. It does not
replace Hermes with Claude's native tools, and it does not claim ordinary
Hermes per-generation model-loop ownership. Issues #21 and #22 remain open.

## Candidate changes

- Hermes acknowledges saved assistant message updates before an associated MCP
  tool can execute. Long commentary is chunked rather than clipped; identities
  distinguish legitimate repeated messages from repeated terminal snapshots.
- One cumulative receipt identifies an actual execution attempt. SDK native
  turns and upstream request counts are separate. Unknown requests remain unknown.
- Unchanged canonical prior history may continue the same native session.
  Incompatible or uncertain history starts a separate native Fable session with
  an explicit **CONTEXT HANDOFF**: a bounded historical excerpt, not exact replay
  or a semantic summary. Original history is not rewritten.
- Host-selected effort/thinking travels through a generic settings field. The
  pinned adapter rejects unsupported settings instead of silently downgrading.

## Required compatible host

This candidate requires the additive host capabilities in
`ea59da297f0cf6df4e81001f975379ccd4bc9339`, based on the historical host
`80332e62eb19e48ed4a1c220dc4c06fe343418ac`. It intentionally fails registration
on a host missing those capabilities. SDK remains `0.2.151`; no SDK upgrade,
automatic profile migration, merge or new release is part of this correction.

## Proof boundary

Focused source tests are not an installed-Hermes pass. Installed canary,
remaining UX checks, exact-head CI and scoped independent review remain required.
Historical qualification and released artifacts do not inherit successor proof.
Native compaction is accepted; hidden native state cannot be reconstructed.
Uncertain interrupted effects are never automatically replayed. This work does
not prove fewer subscription charges or service-terms approval.

The current work graph is [tracker #1](https://github.com/100yenadmin/hermes-claude-agent-sdk/issues/1)
and [UX acceptance #27](https://github.com/100yenadmin/hermes-claude-agent-sdk/issues/27).
