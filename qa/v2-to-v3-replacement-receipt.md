# V2 to v3 replacement receipt

- Prior mechanism: Hermes Agent SDK Feature Parity `v2.0.0`, frozen SHA-256
  `e4842f3a78c855f18af59a8024c4360bde59143987d133724b81594d0f0bfe2e`,
  candidate `6967371b9ff8efce9372dd428b3b764322bd6481`.
- Preserved evidence: all 57 v2 mandatory cells and every historical result
  remain immutable. V3 imports the 53 non-soak source rows by ID. It neither
  rewrites the four `SOAK-*` rows nor counts their exclusion as a pass.
- Discarded assumption: 48 elapsed hours or a 30-minute idle polling cadence is
  a meaningful proxy for supported feature, tool, policy, state, or terminal
  parity on an otherwise idle test account.
- Successor mechanism: `qa/parity-contract-v3.yaml`, version `3.0.0`. RC is the
  exact `53 + 12 + 23 + 36` source union plus dynamic tool/schema inventory.
  Runtime qualification is the separate active 100-turn same-session row.
- Difference evidence: every v3 source item is independently addressable,
  trace-graded, candidate-bound, and required to produce its applicable source
  proof. Active-12, native-36, and runtime scenarios require positive, denial,
  and recovery evidence. Each frozen-v2 row and focused boundary row remains a
  mandatory single invariant; its synthetic secondary paths are explicit
  `NOT_REQUIRED` entries and never count as passes. Missing required executors
  or evidence remain `PENDING`.
- Rollback identity: restore the v2 gate definition by its frozen SHA without
  altering its evidence. This does not retroactively turn the historical soak
  aggregate into a pass.
- Claim boundary: this receipt replaces the gate design only. It proves no v3
  scenario execution, RC, release, runtime, publication, or customer state.
