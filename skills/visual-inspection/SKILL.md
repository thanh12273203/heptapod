---
name: visual-inspection
bundle: visual_inspection
description: Inspect processed HGCAL board tensors through HEPTAPOD, diagnose preflight blockers, and interpret saved legacy or regional results. Use for the visual-inspection pipeline; physical scanning, training, calibration, and annotation changes are separate tasks.
---

# Visual inspection

Use the configured HEPTAPOD tools for processed boards. Discover the available
names before calling: the per-toolkit names below normally carry the
`heptapod__` prefix through toolbase. Python wrappers add the `Tool` suffix.
If the bundle is absent, identify missing configuration or activation; do not
substitute direct pipeline calls that bypass the harness policy.

## Workflow

For an existing saved result, call `ReadInspectionResult` with `result_path`.
It needs no inference or writable board root. For a new inspection:

1. Call `VisualInspectionPreflight` with `config_path` and, if supplied,
   `board_path`. Relative inputs resolve against the visual-inspection repository.
   A successful tool envelope may contain `result.ready: false`; read the named
   checks for blockers. Preflight does not run inference or repair calibration.
2. Resolve blockers within the user's scope. Repository/interpreter/read roots
   and artifact root are injected state, not runtime arguments. Board sidecars
   and persistent lock files require an explicit writable root inside a read root.
   An artifact directory does not relocate the board sidecar. Do not move boards
   to evade provenance or write policy.
3. Call `InspectBoard` with the same inputs; use `force: false` unless recomputation
   is requested. The upstream API validates calibration before cache reuse.
   `force: true` never bypasses stale calibration. Preflight is diagnostic;
   it does not guarantee that inputs will stay fresh or that GPU memory suffices.
4. Call `ReadInspectionResult` on the returned `result_path` snapshot when evidence
   or source coordinates are needed. Preserve the sidecar, snapshot, run record,
   and log paths when reporting the outcome.

Training, calibration, annotation mutations, and scanner operation are distinct
operations. An inspection request does not authorize them. If stale calibration
blocks inspection, report the mismatch and the separate calibration action needed;
do not silently launch it or replace canonical artifacts. Missing regional review
inputs must come from reviewed data, never fabricated labels or assumed normality.

## Interpret the correct mode

| Result mode | Authoritative interpretation |
| --- | --- |
| Legacy (upstream schema absent or 1) | `pw_flags`, `ae_flags`, `hybrid_flags`: -1 skipped, 0 OK, 1 flagged. Hybrid is the intersection. Use the returned legacy summary keys. |
| Regional (upstream schema 2) | `decision_grid`: `ok`, `flagged`, `uncertain`, `skipped`. Zero compatibility flags do not imply a pass. Use regional summary counts and retain uncertainty reasons and coverage. |

Coordinates are zero-based `(row, col)` in stored source order. The UI reverses
presentation, not data coordinates. Do not reinterpret skipped or uncertain
segments as inspected passes. Report flags as pipeline decisions, not established
physical defects or measured detection accuracy.

Reader responses bound evidence size. Check `evidence.complete`, `omitted_fields`,
and `omitted_segment_coordinates`; full metadata and regional evidence remain in
`result_path`. Use that artifact for omitted reference/template/calibration identity,
coverage, scores, and reasons. Missing evidence is not negative evidence.

`validation.status: valid` means saved-result structure passed upstream checks;
regional evidence semantics are not fully validated by that loader.
`freshness.status: unverified` means the current board, model, configuration, and
calibration were not checked. A SHA-256 identifies captured bytes, not current
pipeline provenance. A historical saved result must not be described as current
solely because it loaded successfully.

## Cache and failure handling

- `cache_status: reused` means an unchanged sidecar was observed after a successful
  non-forced upstream call. `refreshed` means created or changed; it does not prove
  every region ran inference. Preserve `unknown` when evidence is inconclusive.
- On `board_busy`, let the owning job finish or investigate its status. Do not
  delete the persistent `.inspection.lock` file. Other upstream writers, including
  a web server, may not honor this harness lock.
- A timeout or artifact error may occur after the upstream sidecar was committed.
  Read the run record and stderr, then inspect any existing result before deciding
  whether to retry. Do not promise rollback or delete a result automatically.
- For missing dependencies, check the configured upstream interpreter; installing
  PyTorch into unrelated HEPTAPOD bundles is not the remedy.
- On unresolved provenance, path, or malformed-result errors, stop dependent work
  and report the actionable blocker with diagnostics. Avoid repeated blind retries.

Distinguish bridge operation, served-tool operation, real-board operation, and
held-out detection accuracy. Synthetic workflow success establishes only the
layers actually exercised. Real-board acceptance remains a separate task.
