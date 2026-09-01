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
`54cd331127ffe5069e49dcf2c5a647aeff779794`, which includes upstream main
`3783fd9ffeada5bee050326f6f96360b6e213d6a`.

Run `hermes_claude_agent_sdk.doctor()` (or `doctor_json()`) from an environment
with the public host API to inspect API and capability compatibility. The
doctor never reads credentials or constructs an SDK client.

- [Project tracker](https://github.com/100yenadmin/hermes-claude-agent-sdk/issues/1)
- [Compatibility matrix](docs/compatibility.md)
- [Architecture boundary](docs/architecture.md)
- [Subscription-only security model](docs/subscription-only-security.md)
- [Removal and rollback](docs/removal-and-rollback.md)

## Feature-first parity v3

The release-candidate quality gate is the repo-owned
[`qa/parity-contract-v3.yaml`](qa/parity-contract-v3.yaml), not an idle
48/49-hour wait. It pins and completely maps the frozen v2 non-soak set
(`53/53`), OpenClaw's active behavior pack (`12/12`), the adapted Agent SDK
boundary set (`23/23`), and the ClawProBench native slice (`36/36`). The
separate runtime lane contains the active 100-turn same-session campaign.

The installed console entry point exposes three fail-closed commands. Capture
the isolated profile's complete tool surface through the real host bridge
before validating or running it:

```sh
hermes-claude-agent-sdk-parity inventory --catalog qa/parity-contract-v3.yaml \
  --capture --lane rc --profile fable-v3-isolated \
  --profile-manifest ./profile.json --output ./tool-inventory.yaml

hermes-claude-agent-sdk-parity inventory --catalog qa/parity-contract-v3.yaml \
  --lane rc --profile fable-v3-isolated --profile-manifest ./profile.json \
  --tool-inventory ./tool-inventory.yaml

hermes-claude-agent-sdk-parity run --catalog qa/parity-contract-v3.yaml \
  --lane rc --profile fable-v3-isolated --plugin-sha "$PLUGIN_SHA" \
  --host-sha "$HOST_SHA" --profile-manifest ./profile.json \
  --tool-inventory ./tool-inventory.yaml \
  --output ./parity-results

hermes-claude-agent-sdk-parity grade --catalog qa/parity-contract-v3.yaml \
  --lane rc --profile fable-v3-isolated --plugin-sha "$PLUGIN_SHA" \
  --host-sha "$HOST_SHA" --profile-manifest ./profile.json \
  --tool-inventory ./tool-inventory.yaml \
  --output ./parity-results --resume
```

Exit `0` means the requested gate passed, `1` is a verified scenario failure,
`2` is a contract or safety violation, and `75` is pending or environment
blocked. Unknown tools, changed schemas, missing executors, missing terminal
events, unsafe billing evidence, proofless passes, and candidate drift never
degrade to a pass. See [`qa/README.md`](qa/README.md) for the packet and runner
contract.

The active executors fail closed unless the plugin and host checkouts are clean
at the supplied SHAs. Live RC execution additionally requires
`HERMES_PARITY_LIVE=1`, `HERMES_PARITY_MODEL=claude-fable-5`, the exact host
root, and the pinned ClawProBench root. The v2 source map also requires the
immutable `33fe73a` reference checkout. These are execution inputs, not values
written into result packets.

The runtime lane has a stricter barrier. It accepts only a persistent
`local_profile` manifest and requires an immutable wheel, its SHA-256 digest,
and a sanitized issue-9 `release_ready` receipt matching
[`qa/runtime-release-ready-receipt.schema.json`](qa/runtime-release-ready-receipt.schema.json).
The executing package must byte-match that wheel. A successful bundle must
bind exactly 100 turns; a 99-turn or partial campaign cannot grade as passed.

## Installation and activation

Download the wheel attached to the compatible GitHub prerelease, verify its
published checksum, and install that exact artifact into the Hermes environment:

```sh
python -m pip install ./hermes_claude_agent_sdk-0.1.0rc1-py3-none-any.whl
```

Installation exposes the `hermes_agent.plugins` entry point but does not enable
the plugin. Hermes keeps installed plugins disabled until the operator opts in
explicitly. Enable this plugin with the supported host command:

```sh
hermes plugins enable claude-agent-sdk
```

To roll back while keeping the package installed, disable the entry point:

```sh
hermes plugins disable claude-agent-sdk
```

For full removal, disable the entry point first and then uninstall the package:

```sh
python -m pip uninstall -y hermes-claude-agent-sdk
```

Disabling or uninstalling this plugin does not remove built-in Hermes behavior.

## Release boundary

No package-index release is authorized. The first distributable candidate will
be a checksummed GitHub prerelease tagged `v0.1.0-rc.1` only after the named host
candidate, approval-followthrough thin gate, all feature-first parity-v3 RC
packs, exact tool/schema inventory, package lifecycle, exact-head CI, and
independent semantic review all pass. The active 100-turn campaign is a
separate isolated-runtime qualification and does not block package RC closure.
