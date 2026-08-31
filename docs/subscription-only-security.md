# Subscription-only security model

The first supported Claude/Fable path is subscription-only and fail-closed.
Explicit Fable sessions disable fallback. Unknown billing classification, API
key use, metered billing, Extra Usage, or GLM routing is a hard gate failure.

Hermes remains responsible for tool availability, permissions, approvals,
status, persistence, cancellation, and fallback policy. The plugin may request
those host services but cannot execute tools or persist credentials around
them. Runtime state may contain only safe opaque resume data; it must never
contain OAuth material, tokens, cookies, raw configuration, prompts, or
transcripts.
