# Changelog

## 1.0.1

- Corrected the shared lock mismatch with Gate/Crew using identical protocol 2 code in all four repositories.
- Registry message writes check the mirrored lease inside the write transaction.
- Shared resource IDs are unique per guild; existing conflicting bindings require explicit review.
- Added protocol regression tests, four-process test evidence, rollout guide and matching install packages.
- Eligibility can consume dedicated Verifier qualifying roles through existing requirements/weights; no new financial ledger or role assignment is added.
- Raffle entries, draws, commitments, proof format and saved results are unchanged.

## 1.0.0

Independent Rip Cars raffle application with Discord administration, role-based eligibility/weights, scheduled draws, auditable seed commitments, prize claims, exports, recovery, diagnostics and isolated deployment.
