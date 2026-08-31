# Compatibility matrix

| Plugin | Hermes host | SDK | Status |
| --- | --- | --- | --- |
| `0.1.0rc1` candidate | AgentRuntime v1 at `7d025c946e743799f26eb9abcb4c0abde2f88f85` | `claude-agent-sdk==0.2.144` | Local contract-tested |

Registration must reject an unsupported runtime API or missing host capability
through the host's `register_agent_runtime()` before retaining or constructing
the factory, importing the SDK, resolving credentials, starting a subprocess,
or issuing a model query. `doctor()` reports the same API/capability handshake
without credentials or SDK client construction. Compatibility with future
Hermes main or future SDK versions is not implied.

The pinned SDK exposes a public `PreCompact` hook but no typed post-compaction
hook. Completion mapping therefore also binds this candidate to the pinned
Claude CLI's observed `SystemMessage(subtype="compact_boundary")` behavior.
The plugin keeps a bounded terminal-result fallback and 600-second watchdog,
projects only provider-neutral lifecycle events, and does not turn lifecycle
messages into conversation content. This is exact-candidate compatibility,
not a guarantee for later SDK or CLI versions.

The required capability set includes `background_delivery_v1`. A host that
lacks it is incompatible before SDK import, credential inspection, client
construction, or query. On a compatible session-scoped host, one plugin
runtime owns one SDK client/reader until parent-session close. Idle completion
content is bounded and deduplicated in arrival order, then delivered only
through the exact bound host service. Provider session identifiers remain
resume state only and are never background-delivery metadata.

The descriptor accepts only the plugin-owned provider id
`claude-agent-sdk`, the provider-neutral `agent_runtime` API mode, and model
ids beginning with `claude-` or `anthropic/claude-`. The host's `anthropic`
Messages provider is a separate transport and is never silently redirected to
this whole-turn runtime. Claude/Fable policy remains in this standalone plugin.
