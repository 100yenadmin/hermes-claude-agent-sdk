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
policy. It also owns the exact parent binding, idle delivery/requeue, route
refresh, and post-close rejection behind `background_delivery_v1`. This
package owns the Claude SDK dependency, SDK session lifecycle,
content conversion, subscription classification, Claude resume state, native
compaction mapping, context adapters, diagnostics, and packaging. It retains
one public SDK reader per runtime instance and classifies idle result bursts,
but it never sees host routing identifiers and never duplicates host delivery.

## Native compaction boundary

The plugin registers the public `PreCompact` hook provided by the supported
`claude-agent-sdk>=0.2.144,<0.2.152` range and converts it to the
provider-neutral runtime `started` phase. The frozen-v2 cell remains exact
SDK 0.2.144. The admitted bundled Claude CLIs report the other edge as a
`SystemMessage` with subtype `compact_boundary`; that message is treated only
as lifecycle metadata and never projected into user or assistant content.

The admitted SDK range does not expose a typed post-compaction hook.
Consequently,
`compact_boundary` is an empirical compatibility adapter, not a claim about a
stable future SDK guarantee. A successful terminal SDK result is the bounded
compatibility fallback when a boundary is omitted. A non-success terminal
result emits `failed`, and a local 600-second watchdog emits `watchdog` and
interrupts the turn if neither boundary nor terminal result arrives. The
watchdog proves only that completion evidence was missing before the bound; it
does not diagnose a provider failure.

Hermes receives only `RuntimeCompactionEvent` values through its public host
facade. Provider payloads remain inside the plugin, the host compressor is not
invoked for runtime-native ownership, and no compaction message is inserted
into the conversation role stream.
