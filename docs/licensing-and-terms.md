# Licensing and service terms

This document explains the boundaries that operators and package maintainers
must keep separate. It is operational provenance documentation, not legal
advice or a determination that a particular distribution or use is permitted.

## 1. Standalone and Hermes source

The standalone repository's `LICENSE` is the MIT License and retains the Hermes
Agent copyright notice for the source boundary. The package's own additions may
carry a separate copyright year in that file. `NOTICE` and `AUTHORS` preserve
the relevant public PR, commit, and contributor attribution without contributor
email addresses.

The MIT license grants permissions for the covered source and documentation
subject to its conditions and disclaimer. It does not grant rights in software,
assets, trademarks, accounts, or services that are not covered by that license.
For clarity, that grant includes customer and commercial use of the covered
Software; the separate Anthropic service boundary below does not narrow those
MIT rights.

## 2. Dependency license metadata

Package metadata admits the bounded first-RC range:

```text
claude-agent-sdk==0.2.151
```

Revision 4 targets exact SDK `0.2.151`, bundled Claude Code-derived CLI
`2.1.258`, and direct model `claude-fable-5-1`. The exact wheel and bundled
subprocess require separate license/provenance inspection.

The standalone plugin wheel and sdist declare this dependency but do not vendor
or redistribute the SDK package or bundled CLI. An installer obtains that
separate distribution from the operator's configured package source. Any
future bundled or vendored artifact would be a different release boundary and
must stop for a fresh redistribution and notice review.

The published metadata for those SDK versions declares the MIT license. This
is a statement about each dependency distribution's declared metadata. It is not a
complete review of all transitive dependency licenses, bundled assets, CLI
components, or redistribution notices. A release owner must inspect the exact
wheel/sdist and resolved dependency set before publishing a release candidate,
then add or update notices when a dependency requires them.

The dependency declaration also does not make the SDK a part of the Hermes MIT
copyright grant. Each dependency remains subject to its own license and notice
conditions.

## 3. Anthropic and other service terms

When the SDK or CLI connects to Claude or another Anthropic service, the
operator's account, product, subscription, API, acceptable-use, commercial, and
other applicable terms govern that service use. Those terms are separate from
the MIT license for source code.

In particular:

- The MIT source license does not provide an Anthropic account, subscription,
  OAuth entitlement, API permission, or billing allowance.
- Anthropic service terms are not equivalent to the MIT license.
- This repository does not grant an Anthropic account or service entitlement,
  and it does not certify that customer or commercial use of Anthropic services
  complies with current Anthropic service terms.
- Credential, account, usage, and data-handling decisions remain the
  operator's responsibility and must not be inferred from source licensing.

Any use of Anthropic services requires the operator's own terms review and,
where appropriate, legal review. No sentence in this document should be read as
a service-terms compatibility opinion or approval. These service restrictions
do not narrow the rights granted for the Software by `LICENSE`.

## 4. Release-time checklist

Before an immutable release candidate is considered, the owning release lane
must:

1. retain `LICENSE`, `NOTICE`, and `AUTHORS` in the artifact;
2. inspect the exact dependency metadata and transitive license/notice set;
3. update third-party notices for any shipped component that requires one;
4. keep each tested exact SDK version, bundled CLI identity, source provenance
   SHA, and artifact checksum together in the release evidence; and
5. record any unresolved license, terms, or redistribution question as a
   release stop rather than assuming compatibility.

This checklist is independent of runtime proof. A package can have complete
source attribution while still lacking release, service-terms, or customer-use
approval.

## 5. Scope and proof boundary

The provenance and licensing files do not prove an upstream merge, a package
release, successful Claude/Fable execution, subscription billing behavior,
future SDK compatibility, or customer readiness. Those claims require their
own exact identity and evidence.
