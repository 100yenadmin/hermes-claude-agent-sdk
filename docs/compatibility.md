# Compatibility matrix

| Plugin | Hermes host | SDK | Status |
| --- | --- | --- | --- |
| `0.1.0rc1` candidate | AgentRuntime v1 at `fe50334bf6976a048689135d776a8da569a034f4` | `claude-agent-sdk==0.2.144` | Local contract-tested |

Registration must reject an unsupported runtime API or missing host capability
through the host's `register_agent_runtime()` before retaining or constructing
the factory, importing the SDK, resolving credentials, starting a subprocess,
or issuing a model query. `doctor()` reports the same API/capability handshake
without credentials or SDK client construction. Compatibility with future
Hermes main or future SDK versions is not implied.

The descriptor accepts canonical `anthropic` (and the public `claude` alias),
`anthropic_messages`, and model ids beginning with `claude-` or
`anthropic/claude-`; Claude/Fable policy remains in this standalone plugin.
