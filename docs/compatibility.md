# Compatibility matrix

| Plugin | Hermes host | SDK | Status |
| --- | --- | --- | --- |
| `0.1.0rc1` candidate | AgentRuntime v1 branch based on `64b96bb5d2755f1d34347e1fb15924a97d652f31`; final host SHA pending | `claude-agent-sdk==0.2.144` | Not yet certified |

Registration must reject an unsupported runtime API or missing host capability
before importing the SDK, resolving credentials, starting a subprocess, or
issuing a model query. Compatibility with future Hermes main or future SDK
versions is not implied.
