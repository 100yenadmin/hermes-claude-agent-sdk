# Hermes Claude Agent SDK Runtime — Revision 4

`hermes-claude-agent-sdk` is a standalone plugin for the Hermes host. Revision
4 is the Hermes-owned, zero-native boundary: Hermes owns every visible behavior
and side effect, while the Claude Agent SDK is used only for subscription
transport, stream reading, cancellation, opaque external-session continuity,
and native-compaction mapping.

The plugin registers lazily through Hermes' public plugin entry point. It does
not import the SDK, inspect credentials, start the bundled subprocess, or query
a model during registration. Once Hermes has selected this runtime, the plugin
constructs the public SDK client and translates its bounded stream into the
host's generic events. Provider reasoning that the SDK or its bundled
Claude Code-derived subprocess may use internally is not visible to Hermes or
the operator; only the host-approved content, tool, lifecycle, and usage
surfaces are exposed.

Hermes composes the exact prompt, transcript, context, permissions, approvals,
tool inventory, delegation, background delivery, status, persistence, usage,
and replay behavior. The SDK receives that direct Hermes prompt as
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

The Revision 4 candidate is checked against the Hermes host at exact commit
`ab49081c228907264b8912831344b7873180219f`. The standalone plugin identity is
the exact source commit and wheel digest recorded in the v4 result manifest;
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

The repo-owned [`qa/parity-contract-v4.yaml`](qa/parity-contract-v4.yaml) is the
current source-to-parity map. It preserves the v3 rows as historical
predecessors, but replaces their provider-native assumptions with Hermes-owned
proof atoms: zero-native absence, the direct Hermes prompt, exact settings and
MCP inventory, canonical transcript/stream ownership, `delegate_task`, and
host-owned background delivery. The v3 contract and its evidence remain
historical only; they are not a current support or release claim.

The installed `hermes-claude-agent-sdk-parity` console script remains the
historical v3 inventory/run/grade surface in this pinned source. Do not use it
as a v4 pass claim: it expects the v3 profile policy. Revision 4's closed
contract is validated by the repository's v4 contract/runner modules and the
exact candidate evidence harness; see [`qa/README.md`](qa/README.md). Any v4
executor must fail closed unless SDK `0.2.151`, bundled CLI `2.1.258`, direct
model `claude-fable-5-1`, and the exact plugin/host SHAs are bound.

The contract's runtime-soak row is a separate bounded evidence lane; neither a
source map, deterministic test, nor local parity packet proves an upstream
merge, package publication, future compatibility, or customer readiness. Do not
substitute the OpenRouter/Nous slug `anthropic/claude-fable-5.1` for this
subscription-only route.

## Local installation and activation

Install the exact locally built or otherwise approved artifact into the
isolated Hermes environment. A local install is not a release or publication:

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

## Scope and proof boundary

These instructions establish only a bounded local install/disable/remove path
and the exact Revision 4 source-compatibility target. They do not authorize or
prove an upstream merge, package release, future Hermes/SDK compatibility,
shared-Eva or fleet operation, or customer readiness.
