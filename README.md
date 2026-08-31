# Hermes Claude Agent SDK Runtime

`hermes-claude-agent-sdk` is the standalone, third-party Claude Agent SDK
whole-turn runtime plugin for Hermes Agent. It is being extracted from
[NousResearch/hermes-agent PR #65982](https://github.com/NousResearch/hermes-agent/pull/65982)
behind a provider-neutral AgentRuntime v1 host contract.

The default branch is currently a packaging and policy shell. It intentionally
registers no runtime and performs no SDK import, credential lookup, subprocess
start, or model query. Do not treat it as a working runtime or release.

## Compatibility target

The first release candidate targets the provider-neutral host branch
`codex/agent-runtime-plugin-api-v1`, based on Hermes main
`64b96bb5d2755f1d34347e1fb15924a97d652f31`. The architecture issue remains
open until that branch and its exact candidate SHA are published and read back.

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
