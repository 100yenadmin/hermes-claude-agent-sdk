# Architecture boundary — Revision 4

The Hermes host ADR is the sole cross-repository interface authority:
[`AgentRuntime Plugin API v1`](https://github.com/100yenadmin/hermes-agent-for-upstream-PR-only/blob/54621dd44e859aa354672a8a388f1cf08f116246/docs/adr/agent-runtime-v1.md)
at host commit `54621dd44e859aa354672a8a388f1cf08f116246`. This plugin does not
copy or redefine that public API.

## One ownership definition

Hermes owns every visible behavior and side effect: request selection, the
prompt and context snapshot, transcript content, tool inventory, permissions,
approvals, tool execution, delegation, background delivery, status, usage
receipts, persistence, cancellation, replay, and lifecycle. The plugin is an
adapter around the public Claude Agent SDK only. The SDK's role is limited to
subscription transport, stream reading, cancellation, opaque external-session
continuity, and native-compaction mapping.

The host passes an immutable `RuntimeTurnRequest` and a host-services facade.
The plugin never receives `AIAgent`, `SessionDB`, a gateway route, or a private
Hermes object. Events cross the facade only after the host has established the
turn's policy and exact delivered surface. A stream or SDK result cannot create
a second transcript, permission path, queue, retry policy, or persistence
store.

## Prompt, settings, and MCP boundary

The SDK receives the direct Hermes prompt snapshot as `system_prompt`. The
adapter always supplies `tools=[]` and `setting_sources=[]`; it does not read
Claude user/project/local settings, `CLAUDE.md`, or a plugin-owned prompt.

The host's delivered-request inventory is the only tool authority. The adapter
creates exactly one in-process MCP server named `hermes-tools`, enables only
the exact `mcp__hermes-tools__<tool>` names present in that inventory, and sets
`strict_mcp_config=true`. Unknown aliases, extra MCP servers, disabled tools,
and a second discovery pass are rejected. Each handler calls the Hermes host
execution and approval funnel.

`permission_mode="bypassPermissions"` is an SDK subprocess setting. It avoids
an SDK-side permission prompt; it does not bypass Hermes permissions,
approvals, guardrails, or execution. Hermes therefore remains the owner of
every effect even when this SDK setting is present.

## Claude subprocess and hidden reasoning

`claude-agent-sdk` brings its bundled Claude Code-derived executable as the
transport subprocess. The plugin may inspect only the bounded public SDK
messages needed for content, tool, lifecycle, usage, and model evidence.
Provider reasoning or other internal subprocess behavior is not a supported
visible surface and is never projected into Hermes transcript content.

## Delegation, background, and compaction

Revision 4 exposes no supported Claude-native `Agent`, `Task`, or background
route. A delegated operation is the Hermes `delegate_task` tool through the
strict MCP bridge. Detached completion is submitted to the host's existing
background-delivery rail; the plugin does not choose a parent route, perform a
latest-session lookup, or maintain a provider-specific queue.

Native compaction is the one SDK lifecycle mapping retained by the adapter.
The public `PreCompact` hook and the bundled CLI's observed
`SystemMessage(subtype="compact_boundary")` become provider-neutral lifecycle
events. Hermes records status and ownership, but does not invoke its own
compressor or insert lifecycle messages into the transcript. Missing boundary
evidence is handled by the bounded terminal fallback/watchdog; it is not a
claim about a future SDK or CLI guarantee.

## State and evidence boundary

The host persists generic state and usage receipts. The plugin may return only
the opaque external SDK session identifier needed for continuity and bounded
model/billing evidence. It does not persist credentials, tokens, cookies,
prompts, transcripts, or customer data.

The source and parity checks bind this boundary to the exact plugin commit and
wheel digest recorded in the v4 result manifest, host commit
`54621dd44e859aa354672a8a388f1cf08f116246`, SDK `0.2.151`, bundled CLI
`2.1.258`, and direct model `claude-fable-5-1`. A plugin source document cannot
self-identify its final commit, so an unbound or zero digest is never accepted
as candidate proof. These identities establish a bounded candidate only; they
do not prove merge, release, future compatibility, or customer readiness.
