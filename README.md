# Hermes Claude Agent SDK Runtime

`hermes-claude-agent-sdk` is the standalone, third-party Claude Agent SDK
whole-turn runtime plugin for Hermes Agent. It is being extracted from
[NousResearch/hermes-agent PR #65982](https://github.com/NousResearch/hermes-agent/pull/65982)
behind a provider-neutral AgentRuntime v1 host contract.

The current candidate registers a provider-neutral AgentRuntime v1 descriptor
through Hermes' existing plugin entry point. Registration is lazy: it performs
no SDK import, credential lookup, subprocess start, or model query. After the
host accepts a compatible selection, the runtime performs a fail-closed local
subscription preflight, constructs the pinned Claude Agent SDK session through
public APIs, bridges tools back through host-owned execution, and emits generic
state and subscription-included usage receipts. Deterministic and packaging
tests cover that composition; the first isolated live turn remains a separate
release gate.

For a bound Hermes parent session, the runtime retains one public SDK client
and one `receive_messages()` reader across turns. Native Agent work that ends
during `run_turn()` stays in that turn and produces one terminal event. A
later idle completion is reduced to the host's bounded provider-neutral
`RuntimeBackgroundResult` and passed only to
`RuntimeHostServices.emit_background_result()`. The plugin never receives or
chooses a Hermes session or gateway route, never performs a latest-session
lookup, and never adds a provider-specific queue or retry path.

The descriptor owns the provider id `claude-agent-sdk` and the generic
`agent_runtime` mode. Claude model ids are selected by the declared `claude-`
and `anthropic/claude-` prefixes; the host's `anthropic_messages` provider
remains a separate transport and is not routed to this plugin.

## Compatibility target

The first release candidate targets the provider-neutral host branch
`codex/agent-runtime-plugin-api-v1` at exact host SHA
`d10bbae5cd90f21f6b6d5386a7d5fc1a8ee99d5e`. That SHA is a test-only
successor of the runtime implementation at
`7d025c946e743799f26eb9abcb4c0abde2f88f85`.

Run `hermes_claude_agent_sdk.doctor()` (or `doctor_json()`) from an environment
with the public host API to inspect API and capability compatibility. The
doctor never reads credentials or constructs an SDK client.

- [Project tracker](https://github.com/100yenadmin/hermes-claude-agent-sdk/issues/1)
- [Compatibility matrix](docs/compatibility.md)
- [Architecture boundary](docs/architecture.md)
- [Subscription-only security model](docs/subscription-only-security.md)
- [Removal and rollback](docs/removal-and-rollback.md)

## Release boundary

No package-index release is authorized. The first distributable candidate will
be a checksummed GitHub prerelease tagged `v0.1.0-rc.1` only after the named host
candidate, thin install/runtime/uninstall gate, frozen parity contract, package
lifecycle, CI, and independent semantic review all pass.
