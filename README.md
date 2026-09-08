# Hermes Claude Agent SDK — v0.1.0 (pinned-host edition)

> **Ownership correction — 2026-09-09:** v0.1.0 is not full Hermes parity.
> It uses Hermes' actual executor and approvals, but bypasses the ordinary
> model-step loop, relies on native history, loses intermediate saved commentary,
> and has accounting/tool-fidelity defects. Native output recovery can issue
> additional requests without Hermes admission. Historical qualification did not
> test those requirements. See the [investigation](docs/ownership-recovery/investigation.md)
> and [recovery tracker](https://github.com/100yenadmin/hermes-claude-agent-sdk/issues/1).
> Do not use this release where strict Hermes budgets or canonical-history
> ownership are required. Existing downloads remain unchanged.

**Maintainers: [start with the review, install, and evidence guide](docs/maintainer-guide.md).**
It includes the ownership diagrams, exact qualified versions, and rollback.
**Requires Hermes host `80332e62eb19e48ed4a1c220dc4c06fe343418ac`.**
Stock Hermes is not supported until the provider-neutral runtime interface lands
upstream. This is opt-in; ordinary API providers are unchanged.

[GitHub release and downloads](https://github.com/100yenadmin/hermes-claude-agent-sdk/releases/tag/v0.1.0)
provide the wheel, source distribution, `SHA256SUMS`, and verification receipt.
There is no PyPI release. The release page is authoritative for publication status.
Start with the [isolated install and login guide](docs/maintainer-guide.md#try-the-pinned-host-release-in-isolation).

`hermes-claude-agent-sdk` is a standalone plugin for the Hermes host. Revision
4 disables native Claude tools and routes effects through Hermes. It does not
establish Hermes ownership of model execution or canonical context. The SDK
still owns the internal turn, retained history and compaction.

The plugin registers lazily through Hermes' public plugin entry point. It does
not import the SDK, inspect credentials, start the bundled subprocess, or query
a model during registration. Once Hermes has selected this runtime, the plugin
constructs the public SDK client and translates its bounded stream into the
host's generic events. Provider reasoning that the SDK or its bundled
Claude Code-derived subprocess may use internally is not visible to Hermes or
the operator; only the host-approved content, tool, lifecycle, and usage
surfaces are exposed.

Hermes supplies the system prompt, permissions, approvals and tool inventory,
and executes delegation/background work. Only the latest user input is sent;
native retained history is not a replay of Hermes' canonical transcript.
The SDK receives the direct Hermes system prompt as
`system_prompt`, with `tools=[]` and `setting_sources=[]`. The only SDK tool
surface is the strict, exact `hermes-tools` MCP server and its admitted
`mcp__hermes-tools__<tool>` names. `bypassPermissions` disables an SDK-side
permission prompt; it never bypasses Hermes approval or execution policy.

There is no supported Claude-native `Agent` or background route in Revision 4.
Delegation goes through the Hermes `delegate_task` tool, and detached completion
goes through Hermes-owned background delivery. The plugin retains one public
SDK client/reader per bound parent session and only the opaque external session
identifier needed to resume that SDK conversation.

## Compatibility target

The final qualified candidate uses Hermes host
`80332e62eb19e48ed4a1c220dc4c06fe343418ac`; GA CI uses that same host.
Historical RC CI used `e89d36a38fbb86b33d685ccf3a57f0557b891069` with a
separately reviewed host delta. See the [candidate/evidence table](docs/maintainer-guide.md#qualified-candidate-and-proof).
The standalone plugin identity is
the exact source commit and wheel digest recorded in the release receipt;
an unbound or zero digest cannot prove a candidate. The dependency target is
`claude-agent-sdk` `0.2.151`, whose bundled Claude Code-derived CLI is
`2.1.258`, with direct model `claude-fable-5-1`.

This is an exact source-compatibility target, not a claim about upstream merge,
publication, future Hermes/SDK versions, or customer readiness. Validate in an
isolated checkout or virtual environment; do not replace a pinned installed
Hermes merely to exercise this candidate.

Run `hermes_claude_agent_sdk.doctor()` (or `doctor_json()`) from an environment
with the public host API to inspect API and capability compatibility. The
doctor never reads credentials or constructs an SDK client.

- [Project tracker](https://github.com/100yenadmin/hermes-claude-agent-sdk/issues/1)
- [Compatibility matrix](docs/compatibility.md)
- [Architecture boundary](docs/architecture.md)
- [Installed Hermes session handoff](docs/installed-hermes-session-handoff.md)
- [Subscription-only security model](docs/subscription-only-security.md)
- [Removal and rollback](docs/removal-and-rollback.md)

## Revision 4 parity contract

The [Hermes release acceptance policy, H1–H8](qa/hermes-release-acceptance.md)
governs release qualification. It supersedes the blanket inherited
220-path/390-packet benchmark obligation; those benchmark totals were **not**
declared passed. [Release #9](https://github.com/100yenadmin/hermes-claude-agent-sdk/issues/9)
and [isolated runtime #15](https://github.com/100yenadmin/hermes-claude-agent-sdk/issues/15)
record the completed, separate qualification decisions.

The repo-owned [`qa/parity-contract-v4.yaml`](qa/parity-contract-v4.yaml) is the
current source-to-parity map. It preserves the v3 rows as historical
predecessors, but replaces their provider-native assumptions with Hermes-owned
proof atoms: zero-native absence, the direct Hermes prompt, exact settings and
MCP inventory, canonical transcript/stream ownership, `delegate_task`, and
host-owned background delivery. The v3 contract and its evidence remain
historical only; they are not a current support or release claim.

The historical v3/v4 parity modules and their executors remain available from
the source checkout and source distribution for repository QA, but are
intentionally excluded from the installed wheel. The wheel exposes only the
Hermes plugin entry point and the offline doctor CLI. Revision 4's closed
contract is validated by the repository's source-level v4 contract/runner
modules and the exact candidate evidence harness; see
[`qa/README.md`](qa/README.md). Any v4 executor must fail closed unless SDK
`0.2.151`, bundled CLI `2.1.258`, direct model `claude-fable-5-1`, and the
exact plugin/host SHAs are bound.

The contract's runtime-soak row is a separate bounded evidence lane; neither a
source map, deterministic test, nor local parity packet proves an upstream
merge, package publication, future compatibility, or customer readiness. Do not
substitute the OpenRouter/Nous slug `anthropic/claude-fable-5.1` for this
subscription-only route.

## Local installation and activation

Use the pinned host in a separate virtual environment, then download the wheel
and `SHA256SUMS` from the v0.1.0 GitHub release. Verify the wheel's SHA-256
against that file before installing (do not substitute the historical RC hash):

```sh
shasum -a 256 ./hermes_claude_agent_sdk-0.1.0-py3-none-any.whl
python -m pip install ./hermes_claude_agent_sdk-0.1.0-py3-none-any.whl
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

## Scope and proof boundary

These instructions establish only a bounded local install/disable/remove path
and the exact Revision 4 source-compatibility target. They do not authorize or
prove an upstream merge, package release, future Hermes/SDK compatibility,
shared-Eva or fleet operation, or customer readiness.
