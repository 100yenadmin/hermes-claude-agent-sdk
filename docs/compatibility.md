# Compatibility matrix — Revision 4

| Plugin | Hermes host | SDK | Status |
| --- | --- | --- | --- |
| `0.1.0rc1` candidate at the exact plugin SHA and wheel digest recorded in its v4 result manifest | Hermes host candidate `93c72953e9728f183732ad97ba680c859f3b0067` | SDK `0.2.151`; bundled Claude Code-derived CLI `2.1.258`; direct model `claude-fable-5-1` | Exact Revision 4 source target; local evidence only |
| v3 predecessor | Historical plugin/host evidence | Historical SDK/model inputs | Historical only; not a current support or release claim |

The exact host candidate above is required. A host without the declared
AgentRuntime v1 capabilities is rejected before SDK import, credentials,
subprocess startup, or query. Use an isolated checkout; do not alter a pinned
installed Hermes merely to exercise this candidate.

Registration must reject an unsupported runtime API or missing host capability
through the host's `register_agent_runtime()` before retaining or constructing
the factory, importing the SDK, resolving credentials, starting a subprocess,
or issuing a model query. `doctor()` reports the same API/capability handshake
without credentials or SDK client construction. Compatibility with any other
Hermes or SDK revision is not implied.

SDK `0.2.151` exposes the public `PreCompact` hook but no typed post-compaction
hook. The bundled CLI's observed `SystemMessage(subtype="compact_boundary")`
is mapped to provider-neutral lifecycle events with a bounded terminal-result
fallback and watchdog. This is exact-candidate behavior, not future-version
compatibility proof.

The required capability set includes the host's tool, approval, content,
cancellation, state, usage, and compaction facades. The tool facade must expose
the provider-neutral public method
`execute_tool(name, arguments, *, request_id=None)` under
`host_tool_request_id_v1`; the plugin passes each validated SDK/MCP request ID
through exactly once. Hermes owns all visible behavior and effects, including
background delivery. One bound parent retains one SDK client/reader; opaque
provider session identifiers are resume state only and never routing metadata.

The candidate also requires `runtime_model_provenance_v1` so Hermes can retain
selected/effective/canonical model evidence without rewriting its legacy
receipt identity. An older host without this additive shape is rejected before
runtime activation.

The descriptor accepts only provider id `claude-agent-sdk`, API mode
`agent_runtime`, and bounded direct `claude-*` identifiers. Provider-qualified
slugs are rejected before auth or SDK startup. The host's `anthropic` Messages
transport remains separate and is not redirected here.

The selection provider remains `claude-agent-sdk` because it identifies the
Hermes routing profile. Generic usage receipts instead record `anthropic` as
the upstream model provider, so accounting never mistakes the transport plugin
for the provider that supplied the model.

## SDK and bundled CLI policy

Revision 4 is pinned to SDK `0.2.151`, bundled CLI `2.1.258`, and direct model
`claude-fable-5-1`. Before authentication, client construction, subprocess
startup, or query, preflight reads only installed distribution metadata and the
bundled CLI version declaration. Missing, malformed, or below-floor metadata
fails closed; a system Claude CLI or custom CLI path is not substituted.

The SDK invokes its bundled Claude Code-derived subprocess. Any provider
reasoning it performs remains hidden; only bounded public stream messages are
mapped into Hermes events.

SDK `modelUsage` is an aggregate and may include auxiliary models used during
the turn. When more than one usage entry is present, the plugin accepts the
sole top-level `AssistantMessage.model` as primary-route evidence only when it
matches a usage key or uniquely matches that key's canonical model. Nested
agent messages do not select the parent turn's model, canonical identity stays
bound to its own usage entry, and missing, conflicting, malformed, or unrelated
evidence remains `ambiguous`. The requested model is retained as selection
metadata and never substitutes for SDK-observed effective-model evidence.

SDK `0.2.151` may instead report the primary route only on a safe
`SystemMessage(subtype="init")` `data.model`, expose a synthetic root
`AssistantMessage.model`, and return an empty `modelUsage` mapping. The plugin
retains the validated init model for the runtime session, treats empty usage as
absence, and resets usage/malformed state at each new turn. Unsafe, conflicting,
or malformed init evidence and any unrelated non-synthetic SDK model remain
fail-closed as `ambiguous`; a turn with no session or SDK evidence remains
`unknown` and never falls back to the requested model.

## Fable model identifier

The supported Revision 4 target is direct `claude-fable-5-1`. The descriptor
accepts it without importing the SDK, then preflight requires the exact SDK/CLI
identity above and passes the model through unchanged. The dotted
`anthropic/claude-fable-5.1` OpenRouter/Nous slug is a different route and is
not evidence for the subscription path. Billing and end-user subscription
proof remain separate; prefix matching never proves either.

Compatibility outside this exact plugin/host/SDK/CLI/model tuple is not
claimed.
