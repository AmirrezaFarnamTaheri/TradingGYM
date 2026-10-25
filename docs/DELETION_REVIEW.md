# Deletion review — 2026-10-25

The review distinguishes package moves from actual retirement. The namespace migration moved roughly 379 source files into the seven-domain package; a deleted old path alone does not mean the feature was lost. The cleanup list in the local working record identified 114 retired tracked artifacts, separate from those moves. This is a category/consumer review plus targeted source inspection, not a claim that every historical report was independently revalidated.

| Material | Decision and integration |
| --- | --- |
| Feature-fixture verifier and regression tests | **Revived.** The 28 parquet fixtures remain useful for research regression. Their original HEAD hashes were recovered into `data/feature-fixtures.sha256`, with paths changed to `data/parquet/`. The verifier rejects changed bytes, missing/extra files and symlinks. It is integrated into CI and the local verification script. Hashes were not regenerated from current bytes. |
| Workflow pin verifier | **Revived.** It checks immutable action revisions without reinstating a release bureaucracy. CI and the local verification script run it. |
| Feature integrity architecture note | **Updated.** It describes the new manifest and paths, and preserves the distinction between feature fixtures and real lake partitions. |
| Known-issues report | **Replaced with current limitations** in REBUILD.md. The removed report claimed historical coverage percentages and no critical issues without current evidence. Those claims are not revived. |
| Native System-One/donor convergence ADR | **Protocol knowledge retained**, through `architecture/SYSTEM1_CONTEXT_AND_KEY_ROTATION.md`, ADR-036/042 and the retained intelligence adapters/tests. The deleted donor census and staged pass counts do not describe the current cockpit. The new settings guide explicitly distinguishes native research protocols from the cockpit's chat JSON gateway. |
| Release receipts, checksum of an old release, recovered qualification YAML, generated source-accountability reports | **Keep retired.** Historical hashes and qualification declarations cannot certify the current working tree or a real funded account. Current builds, tests and generated API contracts provide reproducible checks. |
| Launcher wrappers and dummy-data generator | **Keep retired.** Native CLI commands replace launchers; synthetic records must not populate the product. Deterministic test fixtures remain isolated in tests. |
| Donor archive and reconstruction/report-shape tests | **Keep retired.** No current runtime consumer requires the donor source tree or tests that enforce the old report inventory. Financial, archive-safety, provider and execution regressions remain in the migrated suite. |
| Signing/SBOM/release-manifest wrappers | **Keep retired for this local product.** Restoring them without a maintained release policy would recreate misleading guarantees. Workflow artifacts have their own generated checksums; remote release publication was not performed. |
| Private assistant/debug state | **Preserved locally and excluded from source control.** It is not product documentation or portable runtime state. |

Future recovery should follow a concrete consumer or missing invariant. Git history retains retired material; blanket restoration would reintroduce obsolete entrypoints and unsupported deployment claims.
