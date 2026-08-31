# Compatibility matrix

| Plugin | Hermes host | SDK | Status |
| --- | --- | --- | --- |
| `0.1.0rc1` candidate | AgentRuntime v1 at `0b702c0f34d064ac8e1db45096b179085b1fbb92` | `claude-agent-sdk==0.2.144` | Local contract-tested |

Registration must reject an unsupported runtime API or missing host capability
through the host's `register_agent_runtime()` before retaining or constructing
the factory, importing the SDK, resolving credentials, starting a subprocess,
or issuing a model query. `doctor()` reports the same API/capability handshake
without credentials or SDK client construction. Compatibility with future
Hermes main or future SDK versions is not implied.

The descriptor accepts only the plugin-owned provider id
`claude-agent-sdk`, the provider-neutral `agent_runtime` API mode, and model
ids beginning with `claude-` or `anthropic/claude-`. The host's `anthropic`
Messages provider is a separate transport and is never silently redirected to
this whole-turn runtime. Claude/Fable policy remains in this standalone plugin.
