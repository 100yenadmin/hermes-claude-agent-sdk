# Subscription-only security model — Revision 4

The first supported Claude/Fable path is subscription-only and fail-closed.
Explicit Fable sessions disable fallback. Unknown billing classification, API
key use, metered billing, Extra Usage, or GLM routing is a hard gate failure.

The selected model string is configuration evidence, not proof of the model
that served the turn. Revision 4 retains selected, effective, and canonical
model evidence separately and reports `unknown` when SDK evidence is absent or
ambiguous. The exact direct target is `claude-fable-5-1`; an OpenRouter, Nous
Portal, API-key, or metered slug is not a substitute. A selected/effective
mismatch is explicit evidence and cannot be hidden by aliasing or fallback.

Hermes owns every visible behavior and side effect: tool availability,
permissions, approvals, prompt/context, transcript, status, persistence,
cancellation, delegation, background delivery, usage, and fallback policy.
The SDK adapter only carries subscription transport, stream, cancellation,
opaque continuity, and native-compaction mapping. It cannot execute a tool or
persist credentials outside the host facade.

The SDK options are deliberately restrictive: `tools=[]`,
`setting_sources=[]`, and one strict `hermes-tools` MCP server containing only
the exact host-delivered `mcp__hermes-tools__<tool>` names. The adapter passes
`permission_mode="bypassPermissions"` to avoid an SDK-side prompt; that flag
does not bypass Hermes approval, guardrails, or execution policy.

The SDK runs its bundled Claude Code-derived subprocess. Hidden provider
reasoning is not an operator-visible or transcript-visible surface. Runtime
state may contain only safe opaque resume data; it must never contain OAuth
material, tokens, cookies, raw configuration, prompts, transcripts, or
customer data.
