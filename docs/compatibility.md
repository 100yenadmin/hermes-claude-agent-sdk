# Compatibility matrix

| Plugin | Hermes host | SDK | Status |
| --- | --- | --- | --- |
| `0.1.0rc1` candidate | AgentRuntime v1 at `ffd0a985bdc7b0afccee843de45aaf627a74b0c1` (includes upstream main `f98f5e74e00e54c36088fa2e78171e2a408ba7c9`) | `claude-agent-sdk==0.2.144` | Local contract-tested |

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

The candidate also requires `runtime_model_provenance_v1`. That capability
means the host can durably store the selected, effective, and canonical model
identities plus their resolution while retaining the legacy receipt `model`
as the observed billing identity. An older AgentRuntime v1 host without this
additive receipt shape is rejected during compatibility validation; the plugin
does not silently discard provenance or defer the failure until a turn.

The descriptor accepts only the plugin-owned provider id
`claude-agent-sdk`, the provider-neutral `agent_runtime` API mode, and model
ids matching a bounded direct `claude-*` identifier. Provider-qualified model
slugs are rejected before auth or SDK startup. The host's `anthropic` Messages
provider is a separate transport and is never silently redirected to this
whole-turn runtime. Claude/Fable policy remains in this standalone plugin.

## Fable model identifiers

The frozen RC evidence was produced with the direct Claude model identifier
`claude-fable-5`. Anthropic now also lists the direct identifier
`claude-fable-5-1` as active. The runtime descriptor and preflight accept that
new direct identifier without importing the SDK and pass it through unchanged,
but live subscription compatibility is a separate gate and is not inferred
from prefix matching.

Hermes upstream also exposes `anthropic/claude-fable-5.1` in its OpenRouter and
Nous Portal catalogs. That dotted provider-qualified slug is a different route
contract. It is not evidence for the Claude Agent SDK subscription path and
must not be substituted into this plugin's subscription-only proof.

Existing Fable 5 receipts and parity contracts remain immutable. Until an
exact installed-Hermes subscription probe for `claude-fable-5-1` is frozen,
the plugin keeps `claude-fable-5` as its advertised fallback and the current
parity commands retain their exact Fable 5 model input.
