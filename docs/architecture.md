# VendorScope architecture

Vendor aggregates are versioned inside a tenant transaction. Critical vendors require encryption, access reviews and recovery evidence; standard vendors require the first two. Missing evidence creates an explicit finding. A clean approval rejects unresolved findings, while a risk acceptance requires compensation and a maximum thirty-day lifetime. A decision pins the exact assessment digest and aggregate version. Region or subprocessor changes remove that decision and require replacement evidence. Offboarding records both access revocation and data disposition evidence before terminal closure.

## Domain sequence

Vendor inventory --> Tiered assessment --> Version-bound independent decision --> Subprocessor change --> Reassessment --> Offboarding

## Consistency and recovery

`app/domain.py` owns explicit schemas and domain transitions. `app/service.py` validates input and commits state, audit entry and idempotency receipt in one SQLite transaction with `BEGIN IMMEDIATE`. `app/api.py` authenticates server-owned identities, limits request bytes and exposes scoped operations. `app/cli.py` executes the committed scenario against the same service. `web/` validates the scenario contract and renders real API results without HTML interpolation.

A process crash before commit rolls back domain state and receipts. Local filesystem effects in the certificate authority are separate from SQLite and partial CA initialization fails closed. Container state is disposable. Persistence, backups, independent audit checkpoints and external identity lifecycle are deployment responsibilities.

## Explicit scope

This is an assessment workflow, not independent verification of a vendor or its documents. Safeguards and evidence digests are entered by authorized operators. Tiering is an explicit illustrative policy, not a universal risk formula. Region labels document a processing inventory and do not automatically decide legal permissibility. There is no continuous external breach feed, document authenticity service or commercial GRC connector. Offboarding records reviewed assertions and does not revoke external access itself.

Customer questionnaire responses are restricted to three supported safeguard questions. Each answer is derived from the current assessment and retains its evidence digest. Unknown remains unknown. Independent approval binds the exact response set, and export rechecks the current vendor version and assessment freshness, so processing changes invalidate previous responses.
