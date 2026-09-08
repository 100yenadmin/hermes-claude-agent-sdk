# Native request admission: portable reproduction

This reuses the maintained DirectSDK recording peer and unmodified client
from Hermes commit `eafb4186a4e8be7ac0ca94d69b10c57e0154aee8`.
Credit belongs to the authors of [DirectSDK PR #105863](https://github.com/NousResearch/hermes-agent/pull/105863).
The peer is imported from that checkout, not vendored. Only synthetic response
`stop_reason` is changed. No subscription credentials or provider traffic are used.

## Requirements and command

Use macOS with `/usr/bin/sandbox-exec` and Python 3.11+ (standard library only).
Obtain the native
binary through the official SDK/npm distribution; do not modify its bytes.
Use an isolated checkout/environment and inspect the script before running.
The script refuses a different checkout SHA or an absent sandbox. Every native
process receives a sterile environment, fake fixture credential, temporary
home, dead proxies and an OS outbound policy allowing localhost only.

From the plugin repository root:

```sh
git clone https://github.com/NousResearch/hermes-agent.git hermes-native-repro
git -C hermes-native-repro checkout --detach eafb4186a4e8be7ac0ca94d69b10c57e0154aee8
python3 docs/ownership-recovery/native_recovery_probe.py --hermes-checkout ./hermes-native-repro --binary /absolute/path/to/official/claude
```

Do not point this script at a live proxy, supply real credentials, remove the
sandbox, or copy a live home/config. stdout contains sanitized counts/hashes,
not wire bodies. The temporary homes are removed on exit.

## Observed results, not a new rerun

| Native | Normal | One output-limit response | Repeated output-limit responses |
| --- | ---: | ---: | ---: |
| 2.1.258 | 1 | 2 | 4, then error |
| 2.1.263 | 1 | 2 | 4, then error |

Counts are actual localhost Messages requests, not inferred `num_turns`.
Both retain max-turns=1, zero HTTP retries, disabled native tools and compaction.
The [original sanitized receipt](native-recovery-receipt.json) has SHA-256
`05fd629f64c4ed3980c5516d541cf69fe683aed38703e28866e86ab2f0ba305d`.
Its probe hash identifies the original machine-local script. The portable
script here changes argument/path handling and stdout packaging; it has not
been represented as the original executed bytes or as a new run.

## Required vendor contract

A supported single-generation mode or synchronous before-request callback must
let Hermes deny every subsequent generation before transmission, including
output-limit recovery. Partial content, usage and finish reason must remain
available; a later explicitly admitted model step must continue correctly.
Documented controls must cover alternate recovery/auxiliary request paths.
A flag suggestion or acknowledgment is not a passing reproduction.

The counterexample disproves the tested flags' guarantee, not every possible
future/vendor mechanism. No proxy, OAuth extraction, binary patch or API-billing
substitute is part of this recovery.
