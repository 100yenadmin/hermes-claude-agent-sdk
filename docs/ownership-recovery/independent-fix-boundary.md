# Independent correction boundary — 2026-09-09

The initial recovery inspection found that two nominally independent fixes
cannot satisfy their complete acceptance through the pinned public host API.
This does not change the goal or authorize another whole-turn host interface.

## Persistence: blocked on ordinary provider-loop recovery (#24 / #22)

On host `80332e62eb19e48ed4a1c220dc4c06fe343418ac`,
`RuntimeHostServices.emit_content` only streams, and the public protocol has
no canonical assistant-message persistence/insertion operation.
`execute_tool` persists tool rows immediately. The final
`_merge_external_runtime_messages` helper appends after the common prefix;
it cannot insert commentary before those already-persisted rows.

A provider-free execution of the actual host helper with a synthetic user,
one saved tool call/result and a returned full commentary/tool/final transcript
produced roles:

```text
user, assistant(tool), tool, assistant(commentary), assistant(tool), tool, assistant(final)
tool_result_count=2; commentary_index=3; first_tool_index=1
```

This is an expected negative reproduction, **not passing persistence evidence**.
Appending only commentary avoids duplicated tools but still places commentary
after its effects; emitting completion on cancellation also cannot repair
incremental durability. No such workaround was implemented. Ordinary Hermes
model-step persistence is the selected repair once request admission unblocks
that architecture. Do not access private host fields from the plugin.

## Accounting: blocked on ordinary provider-loop recovery (#25 / #22)

Host `agent/turn_runtime.py:222–239` converts absent/non-integer
`api_calls` to zero and passes an integer to finalization.
`RuntimeUsageReceipt` carries token/model/billing data but no optional
observed-request count. Native `num_turns` is not an HTTP count.
Changing the plugin's hardcoded one to None, zero or num_turns would therefore
still be misleading. The legacy field is not fixed in this correction.

This is the exact dependency under #22, not a new Claude-specific host type.
The successor must distinguish admitted host steps and actually observed
requests and preserve unknown/partial usage. Existing token receipts are
unchanged; they do not establish request admission.

## Tool correction (#26)

The correction removes model-payload whitespace/control-category flattening
and description/result display truncation. Host-approved text and Unicode
remain intact except existing credential-pattern redaction. Structured
results remain JSON-serialized. Explicit structural graph safeguards remain;
there is no promise of unlimited provider payloads.

Schema handling uses the existing dependency stack's JSON Schema library,
declared directly as `jsonschema==4.26.0`, rather than maintaining a partial
validator. Standard draft-2020-12 constraints, nullable types and local
references are preserved. Unknown keywords/dialects and external references
are rejected explicitly; remote schema retrieval is disabled. Backend-specific
acceptance still needs the successor's actual model boundary proof.

Focused local command: `python -m pytest tests/test_tool_bridge.py -q`.
Observed: 24 passed. These tests include multiline/Unicode, long output and
descriptions, schema/reference preservation and enforcement, unknown-tool
denial, cancellation, secret filtering and host execution. This is source
regression proof, not installed-Hermes or full-parity qualification.

No provider calls, host changes, merges or published artifact changes occurred.
