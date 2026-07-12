# In-box scenario-pack catalog

`manifest.yaml` declares the scenario packs Shifter ships by default.

Per ADR-033/ADR-034, the in-box catalog is **not** loaded through a privileged
code path. The bootstrap (`cms.scenarios.inbox.register_inbox_packs`, invoked by
the `bootstrap_inbox_catalog` management command) registers each manifest entry
through the same `cms.services.register_pack` service an operator uses via the
API or the `register_pack` CLI. The shipped catalog is dogfooded through the
uniform, entitlement-blind ingestion boundary.

There are no conformant default packs yet (authored under program #1584), so
`manifest.yaml` currently declares an empty `packs` list. Add one entry per
shipped pack as they land; the entry shape matches the operator registration
request (see the example in `manifest.yaml`). Packs themselves are validated as
foreign input at registration against the `aces-scenario-packs` contract.
