# Architecture boundary

The provider-neutral AgentRuntime v1 ADR is owned by the Hermes host branch and
is the sole cross-repository interface truth. This plugin does not maintain a
second copy of that contract.

Candidate ADR:
<https://github.com/100yenadmin/hermes-agent-for-upstream-PR-only/blob/codex/agent-runtime-plugin-api-v1/docs/agent-runtime-plugin-api-v1.md>

The candidate link is not release proof until the branch is pushed and its
exact SHA is recorded. The frozen local capability manifest has SHA-256
`a4bd97694b09069ca8d77a51bdaefb588ea1701e0b1fb83a9ffc51314bad7b19`.

Hermes owns the runtime protocol, registration and dispatch, host security and
tool facades, generic state and receipts, compaction lifecycle, and replay
policy. This package owns the Claude SDK dependency, SDK session lifecycle,
content conversion, subscription classification, Claude resume state, native
compaction mapping, context adapters, diagnostics, and packaging.
