# Hermes Claude Agent SDK Runtime

`hermes-claude-agent-sdk` is the standalone, third-party Claude Agent SDK
whole-turn runtime plugin for Hermes Agent. It is being extracted from
[NousResearch/hermes-agent PR #65982](https://github.com/NousResearch/hermes-agent/pull/65982)
behind a provider-neutral AgentRuntime v1 host contract.

The current candidate registers a provider-neutral AgentRuntime v1 descriptor
through Hermes' existing plugin entry point. Registration is lazy: it performs
no SDK import, credential lookup, subprocess start, or model query. The runtime
body is still a minimal fake-event shell; real Claude SDK session/process
extraction is tracked separately.

The descriptor owns the provider id `claude-agent-sdk` and the generic
`agent_runtime` mode. Claude model ids are selected by the declared `claude-`
and `anthropic/claude-` prefixes; the host's `anthropic_messages` provider
remains a separate transport and is not routed to this plugin.

## Compatibility target

The first release candidate targets the provider-neutral host branch
`codex/agent-runtime-plugin-api-v1` at exact host SHA
`0b702c0f34d064ac8e1db45096b179085b1fbb92`, based on Hermes main
`64b96bb5d2755f1d34347e1fb15924a97d652f31`.

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
