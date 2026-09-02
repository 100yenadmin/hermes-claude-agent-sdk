# Compatibility matrix

| Plugin | Hermes host | SDK | Status |
| --- | --- | --- | --- |
| `0.1.0rc1` candidate | AgentRuntime v1 at `4f3b4bfcd9c17ced85df25e25c3890755fdbf26c` (includes upstream main `180291162ff4df0d42b5dc4fecd08005cf7cebf9`) | `claude-agent-sdk>=0.2.144,<0.2.152` | Frozen Fable 5 cell; version-gated Fable 5.1 successor, live pending |

Registration must reject an unsupported runtime API or missing host capability
through the host's `register_agent_runtime()` before retaining or constructing
the factory, importing the SDK, resolving credentials, starting a subprocess,
or issuing a model query. `doctor()` reports the same API/capability handshake
without credentials or SDK client construction. Compatibility with future
Hermes main or SDK versions outside the admitted range is not implied.

The pinned SDK exposes a public `PreCompact` hook but no typed post-compaction
hook. Completion mapping therefore also binds the frozen Fable 5 cell to its
pinned Claude CLI's observed `SystemMessage(subtype="compact_boundary")` behavior.
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

## SDK and bundled CLI policy

The package admits the first-RC SDK range
`claude-agent-sdk>=0.2.144,<0.2.152`, so it cannot silently resolve to an
untested successor beyond 0.2.151.
The frozen parity-v2 Fable 5 cell remains constrained to exact SDK `0.2.144`;
the range does not rewrite that contract. Direct `claude-fable-5` therefore
remains eligible on the frozen 0.2.144 installation.

Direct `claude-fable-5-1` is a separate successor cell. Before authentication,
client construction, subprocess startup, or query, preflight reads only the
installed distribution metadata and the SDK's bundled
`claude_agent_sdk/_cli_version.py` declaration. It requires SDK `>=0.2.151`
within the admitted range and bundled Claude Code `>=2.1.257`. The first
successor identity is exact SDK `0.2.151` with bundled CLI `2.1.258`.
Missing, malformed, or below-floor metadata fails closed. No system Claude
CLI, custom CLI path, or SDK import is used for this check. `doctor()` reports
the same bounded metadata and decision without credentials or a client.

## Fable model identifiers

The frozen RC evidence was produced with the direct Claude model identifier
`claude-fable-5`. Anthropic now also lists the direct identifier
`claude-fable-5-1` as active. The runtime descriptor accepts that direct
identifier without importing the SDK, while preflight additionally requires
the SDK/CLI floor above and then passes it through unchanged. Live subscription
compatibility remains a separate gate and is not inferred from prefix matching.

Hermes upstream also exposes `anthropic/claude-fable-5.1` in its OpenRouter and
Nous Portal catalogs. That dotted provider-qualified slug is a different route
contract. It is not evidence for the Claude Agent SDK subscription path and
must not be substituted into this plugin's subscription-only proof.

Existing Fable 5 receipts and parity contracts remain immutable. Until an
exact installed-Hermes subscription probe for `claude-fable-5-1` is frozen,
the plugin keeps `claude-fable-5` as its advertised fallback and the current
parity commands retain their exact Fable 5 model input.
