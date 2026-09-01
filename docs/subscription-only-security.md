# Subscription-only security model

The first supported Claude/Fable path is subscription-only and fail-closed.
Explicit Fable sessions disable fallback. Unknown billing classification, API
key use, metered billing, Extra Usage, or GLM routing is a hard gate failure.

The selected model string is configuration evidence, not proof of the model
that served the turn. A live gate must retain the selected value separately,
use SDK-reported effective and canonical model identity when available, and
report `unknown` rather than copying the selection when that evidence is
absent or ambiguous. A selected/effective mismatch is explicit evidence and
cannot be hidden by aliasing or fallback. Fable 5.1 qualification uses only the
direct Anthropic identifier admitted by the governing compatibility contract;
an OpenRouter, Nous Portal, API-key, or metered slug is not a substitute.

Hermes remains responsible for tool availability, permissions, approvals,
status, persistence, cancellation, and fallback policy. The plugin may request
those host services but cannot execute tools or persist credentials around
them. Runtime state may contain only safe opaque resume data; it must never
contain OAuth material, tokens, cookies, raw configuration, prompts, or
transcripts.
