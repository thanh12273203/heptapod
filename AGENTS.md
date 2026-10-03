# AGENTS.md - HEPTAPOD Agent Harness Current Spec

## Current Project Summary

HEPTAPOD (High-Energy Physics Toolkit for Agentic Programming/Planning,
Orchestration, and Deployment) provides structured tools for agents working on
high-energy physics workflows.

The current repository packages Orchestral `BaseTool` implementations through
`toolkit.yaml`. toolbase installs selected bundles and serves them over MCP.
Existing capabilities include event generation, symbolic calculations, particle
data, literature lookup, and event analysis.

The immediate extension objective is an agent harness for the sibling
`../visual-inspection` project. HEPTAPOD should own the tool interfaces and agent
workflow guidance; visual-inspection should own image processing, model training,
calibration, and defect inference.

Runs 1–6 implement the execution bridge, read-only preflight, board inspection,
saved-result reading, gated toolbase/MCP registration, workflow skill, and
reproducible examples. Real-data acceptance remains pending.

---

## Implementation Boundaries

- Keep reusable agent adapters in this repository.
- Keep scientific pipeline implementations in visual-inspection; do not copy its
  models, loaders, or inference algorithms into HEPTAPOD.
- Read the sibling repository's `AGENTS.md` before work affecting its contracts.
  Its instructions govern changes there, not unrelated HEPTAPOD tools.
- Configure external repository and interpreter paths explicitly. Do not bake a
  developer's absolute filesystem paths into tools or skills.
- Prefer the existing toolbase/MCP and Orchestral integration surfaces. The
  `_legacy_mcp/` implementation is not the default extension point.
- Preserve existing bundles and avoid imposing PyTorch or scanner dependencies
  on unrelated physics tools.
- Separate pipeline readiness from adapter readiness. A successful tool import
  does not establish model, calibration, or dataset correctness.

---

## Target Code Surface

Existing framework surfaces:

- `tools/`: domain-specific Orchestral tools and reusable implementation logic
- `toolkit.yaml`: tool registration, bundle dependencies, and injected config
- `skills/`: packaged workflow instructions associated with tool bundles
- `examples/`: agent demos and shared harness helpers
- `scripts/`: execution-only entry points
- `CONTRIBUTING.md`: tool fields, error handling, path safety, and output contracts
- `test_runner.py`: existing tool test runner

Recommended visual-inspection additions:

- `tools/visual_inspection/`: adapters, process execution, result validation
- `tools/visual_inspection/tests/`: contract and failure-path tests
- `skills/visual-inspection/SKILL.md`: workflow ordering and result interpretation
- `examples/visual_inspection/`: a minimal reproducible invocation and setup guide
- a `visual_inspection` bundle in `toolkit.yaml`

These paths describe planned additions; inspect the working tree before assuming
that any proposed tool is available.

---

## Tool Interface Contract

New tools should follow `CONTRIBUTING.md`:

- inherit from Orchestral `BaseTool`
- describe agent-provided inputs using typed `RuntimeField` definitions
- use `StateField` for configured paths and other injected state
- declare injected configuration under `config:` in `toolkit.yaml`
- register each exported tool under its domain bundle
- return structured JSON with a schema version and explicit success status
- report failures using `self.format_error()` with actionable context

Resolve paths against documented roots and enforce output containment, including
symlink resolution. Use argument lists for subprocesses. Keep logs separate from
machine-readable results, preserve diagnostics, and bound execution time.

Use a separately configured visual-inspection Python environment when bridging
to its APIs. Do not mutate `sys.path` or import CLI modules from `scripts/`.

---

## Current Upstream Status

### Repository audit: 2026-10-02

Inspected visual-inspection at commit `6ca792c` with no tracked working-tree
changes. Its original board/model/trainer/server migration is implemented;
do not repeat that migration as a prerequisite for adapters. Reflection Runs
1–8 are also implemented, including region storage, mask geometry, annotation
APIs/editor/review, migration, local calibration, and local inspection.

Current reusable package APIs include:

- `src.configs.InspectionConfig.from_yaml()`
- `src.inspection.inspect_board()` and `validate_calibration()`
- `src.inspection.calibrate()`, `analyze()`, `evaluate_reconstruction()`
- `src.inspection.calibrate_local()`, `load_local_calibration()`
- `src.utils.InspectionResults.load_json()` and result serialization

The configured reference, example board, checkpoint, and skip map exist. A fresh
read-only call to `validate_calibration()` rejects the default configuration:
"Baseline, checkpoint, or skip mask changed. Recalibrate before inspection."
The standard regional manifests/masks/annotations/thresholds directories contain
only `.gitkeep` placeholders. Synthetic examples and tests are not reviewed
production inputs. Real inspection therefore still needs compatible calibration;
regional inspection additionally needs a reviewed data handoff.

Fresh verification in the sibling virtual environment:

- Focused pipeline-contract, calibration, and local-inspection tests: 29 passed,
  with one scheduler warning from the graceful-interruption test.
- `python -m scripts.demo_inspection --check`: 30 OK, one flagged, one uncertain,
  eight skipped, using temporary synthetic data and a constant model.
- No real-board inference, recalibration, training, live server, or full-suite
  rerun was performed. The sibling tracked working tree remained unchanged.

Scanner/cropper acquisition remains a placeholder. Multiple-reference aggregation,
automatic appearance routing, glare/saturation checks, alignment/correction, and
held-out regional accuracy validation remain future work. Legacy `analyze()` does
not evaluate the new regional decisions.

---

## Visual Inspection Data and Result Contract

Stored boards are BGR `uint8` arrays with canonical shape:

```text
(8, 5, 1060, 1882, 3)
```

Use upstream `open_board` validation and memory mapping; segment preprocessing
converts once to normalized RGB. Source coordinates are zero-based `(row, col)`;
the UI reverses grid presentation without changing stored coordinates. The skip
JSON is authoritative; the canonical mask leaves 32 of 40 positions inspectable.

The preferred automation boundary is:

```text
InspectionConfig.from_yaml(path)
inspect_board(config, board_path=None, force=False) -> InspectionResults
```

YAML paths resolve against the visual-inspection repository root. Resolve explicit
board overrides to absolute paths before invocation. Run the bridge in the sibling
repository with its configured interpreter. `run_inspection()` is a lower-level
API and must not be used to bypass provenance validation or cache policy.

The inspection YAML uses latent dimension 64; the training YAML uses 32. Preserve
checkpoint-compatible settings or use `model: {}` for supported CNN architecture
inference. The shared loader currently constructs the CNN, not the ResNet model.

Default whole-segment scoring is `1 - SSIM` for pixels and MAE after sigmoid for
autoencoder reconstructions. Flags use `score >= threshold`; hybrid is the
intersection. Native fields remain `pw_flags`, `ae_flags`, `hybrid_flags`,
`baseline_path`, `inspected_path`, and `metadata`, with flag encoding
`-1 = skipped`, `0 = OK`, `1 = flagged` in legacy mode.

When `local_calibration_path` is configured, `inspect_board()` dispatches to local
inspection. Preserve its schema-version-two payload and the authoritative
`local.decision_grid`: `skipped`, `ok`, `flagged`, or `uncertain`. Zero compatibility
flags are not passes in local mode. Retain regional evidence, coverage, uncertainty
reasons, and reference/template/calibration identity. Report `summary()` with its
mode-specific keys rather than assuming legacy counts. Legacy payloads can omit
`schema_version`; treat that as upstream version one, separate from tool schema.

Both modes validate calibration before cache reuse and write
`<board>.inspection.json` beside the board. `force=True` recomputes but does not
bypass stale calibration. An output directory alone does not relocate this sidecar.
Legacy provenance uses path/size/mtime signatures; regional JSON documents also
use content digests. Do not describe all artifacts as content-addressed.

---

## Execution and Reproducibility Contract

Record the input paths, repository revision, model parameters, checkpoint,
calibration files, selected device, and execution outcome for inspection runs.
Keep large image tensors and weights out of tool responses and version control;
return compact flag grids, summaries, and artifact paths.

The first automation target is inspection of already processed board tensors.
Treat training, calibration, and physical acquisition as distinct operations.
Do not let an inspection request silently launch training, replace canonical
calibrations, or operate scanner hardware.

Use explicit model parameters matching the checkpoint. Calibration and inspection
must agree on preprocessing, segment indexing, and the model architecture.
Surface upstream exceptions as failures rather than plausible empty results.

---

## Code Implementation Plan

Implement the harness in these nine sequential runs. Runs 1–6 deliver the initial
inspection harness; Run 7 establishes real-data acceptance; Runs 8–9 extend
explicit automation. Real-data preparation can proceed alongside adapter work.
Scanner acquisition is a separate future integration.

### Run 1: Configuration and subprocess bridge

1. Add `tools/visual_inspection/` with explicit repository, interpreter, allowed
   data roots, artifact root, and timeout configuration.
2. Execute a worker under the sibling interpreter and repository working
   directory. Keep ML imports out of the HEPTAPOD adapter and keep the worker
   entry point under `scripts/`; do not import CLI modules or mutate `sys.path`.
3. Define versioned JSON requests/responses, request identity, structured errors,
   separate diagnostic logs, and bounded response loading.
4. Validate paths and symlink containment, preserve virtual-environment interpreter
   symlinks, and implement timeouts and subprocess-group cleanup.
5. Verify the foundation using a lightweight `ping` operation and failure tests.

Completion check: ping succeeds in the configured environment; invalid paths,
launch failures, malformed responses, and timeouts have actionable diagnostics.

### Run 2: Read-only preflight tool

1. Implement `VisualInspectionPreflightTool` with staged inexpensive checks before
   loading weights: resolved configuration/mode, board headers, skip coordinates,
   checkpoint settings, artifacts, intended device, and expected writes.
2. Reuse upstream legacy/regional calibration validators and report regional
   manifest/template prerequisites. Distinguish stale artifacts, unavailable
   reviewed data, and missing dependencies.
3. Do not run inference or repair calibration during preflight. Do not use board
   metadata HTTP endpoints that can trigger inspection.
4. Account for `<board>.inspection.json` beside the board. Require an explicitly
   configured writable data root before inspection; a HEPTAPOD output directory
   alone does not relocate the sidecar. Add an upstream destination option if
   read-only datasets must be supported, without moving data to bypass provenance.

Completion check: the current stale legacy calibration and absent regional inputs
are reported correctly without changing data or calibration.

### Run 3: Board inspection tool

1. Implement `InspectBoardTool` around `src.inspection.inspect_board()` with config,
   board, and force inputs. Preserve upstream provenance, caching, and mode dispatch.
2. Validate board-sidecar write containment and prevent simultaneous harness jobs
   from writing the same board result.
3. Reuse bounded subprocess execution and save logs plus a run record containing
   resolved inputs/configuration, revision, timing, and outcome.
4. Return compact results and artifact paths. Report cached versus recomputed
   execution only when evidence supports the distinction.
5. Do not automatically recalibrate on failure or bypass validation with force.

Completion check: synthetic legacy/local inspections work, stale calibration fails
when forced, and errors/timeouts retain useful diagnostics.

### Run 4: Result reading and interpretation

1. Implement `ReadInspectionResultTool` around upstream validated result loading.
2. Support legacy payloads and regional schema version two. Preserve authoritative
   local decisions: `ok`, `flagged`, `uncertain`, and `skipped`.
3. Return mode-specific counts, source coordinates, and available regional evidence.
   Keep responses bounded and retain full evidence in artifacts.
4. Distinguish a structurally valid saved result from a verified current result.

Completion check: uncertainty survives serialization, malformed results fail, and
zero regional compatibility flags never become automatic passes.

### Run 5: HEPTAPOD registration

1. Finish Orchestral `BaseTool` wrappers with typed runtime/state fields and
   HEPTAPOD JSON/error conventions.
2. Register all three tools and configuration in a gated `visual_inspection`
   bundle in `toolkit.yaml` without adding PyTorch to unrelated bundles.
3. Verify schema generation, discovery, and calls through toolbase/MCP and Orchestral.

Completion check: an agent client discovers and invokes all three tools and the
synthetic workflow succeeds through the served interface.

### Run 6: Skill and reproducible example

1. Add `skills/visual-inspection/SKILL.md` for preflight, inspection, evidence
   reading, interpretation, calibration errors, caching, and uncertainty.
2. Keep training, calibration, annotation mutations, and scanning distinct from
   inspection requests; do not infer permission for them.
3. Add `examples/visual_inspection/` with setup and a reproducible synthetic workflow.
4. Document troubleshooting and distinguish operational success from accuracy.

Completion check: the documented workflow works without hidden settings or changes
to production artifacts. Report bridge, served-tool, and real-data checks separately.

### Run 7: Real-board acceptance

1. Select representative boards, reference, checkpoint, and skip map.
2. Generate compatible legacy calibration in a designated artifact directory;
   avoid overwriting canonical artifacts as an incidental repair.
3. Inspect real boards through served tools and verify persistence, cache reuse,
   forced execution, and diagnostics.
4. For regional inspection, obtain reviewed board identities/appearance metadata,
   templates, labels/visibility, disjoint physical-board splits, and new fitted
   thresholds. Do not fabricate missing review data or treat unknowns as normal.

Completion check: record reproducible real-board results and limitations. Regional
acceptance remains pending without reviewed inputs. Operational acceptance is not
held-out detection-accuracy validation.

### Run 8: Calibration and evaluation tools

1. Wrap existing `calibrate()` and `calibrate_local()` as separate explicit actions.
2. Return artifact locations, provenance, sample support, and support gaps.
3. Wrap legacy `analyze()` and `evaluate_reconstruction()`; distinguish reconstruction
   loss from defect-detection metrics and identify evaluation inputs.
4. Extend upstream regional evaluation before offering regional accuracy reports.

Completion check: generated calibration can be consumed by inspection and reports
state their method, dataset, and limits accurately.

### Run 9: Training job management

1. Launch training from an explicitly selected YAML and return a job identifier.
2. Provide status/log retrieval, cancellation, and checkpoint/output locations.
3. Handle failed jobs, interruption, and supported resume behavior.
4. Validate a short bounded target-device run before longer experiments.

Completion check: an agent starts, monitors, stops, and reads artifacts from a short
training job. Scanner operation and annotation mutation remain separate scopes.

### Run 1 implementation status

The configuration and bridge live in `tools/visual_inspection/`; the execution-only
entry point is `scripts/visual_inspection_worker.py`. Run 1 implemented `ping`;
Run 2 adds `preflight`; Run 3 adds `inspect`; Run 4 adds `read_result`.
Run 5 registers the tools; Run 6 adds the workflow skill and examples. The bridge uses only the standard library and supports POSIX execution;
other platforms fail explicitly until process-tree cleanup is implemented there.
Configured repositories/interpreters are trusted code, and path validation is not
an operating-system sandbox or a defense against concurrent hostile filesystem edits.

Configuration uses absolute paths and creates no directories at initialization.
Relative input paths resolve against the sibling repository and must stay in
`data_roots`; relative output paths resolve below `artifact_root`. Data roots grant
read scope only. Run 2 adds `writable_data_roots`, defaulting to no authorized
board writes. Writable roots must be within read roots; Run 3 enforces this
policy when executing inspection.
Each execution creates a private run directory containing `request.json`, worker
`response.json` when available, `stdout.log`, and `stderr.log`. The returned envelope
also contains execution paths, return code, and elapsed time. Failed processes may
have no response file; inspect the returned error and diagnostic logs.

Verification on 2026-10-02: 11 standard-library unittest tests pass, including
subprocess failure/timeout cleanup, venv preservation, path containment, strict
response validation, and imports without site packages. A real `ping` using the
sibling virtual environment confirms Python 3.12.3 and the intended working
directory. Focused Ruff correctness checks (`E4,E7,E9,F`) pass. No model, board
inference, calibration, or sibling source edits were performed.

Use from the HEPTAPOD repository root with configured absolute paths:

```python
from pathlib import Path

from tools.visual_inspection import BridgeConfig, InspectionBridge

config = BridgeConfig(
    repository=Path('/absolute/path/to/visual-inspection'),
    python=Path('/absolute/path/to/visual-inspection/venv/bin/python'),
    data_roots=(Path('/absolute/path/to/visual-inspection/data'),),
    artifact_root=Path('/absolute/path/to/heptapod/logs/visual_inspection'),
    timeout_seconds=60
)
result = InspectionBridge(config).execute('ping')
```

Run the bridge tests without installing optional ML or test dependencies:

```bash
PYTHONPATH=. python3 -B -m unittest discover -s tools/visual_inspection/tests -p 'test_*.py' -v
```

### Run 2 implementation status

`VisualInspectionPreflightTool` is available from
`tools.visual_inspection.agent_tools`. It is an Orchestral `BaseTool`, now registered
in `toolkit.yaml` by Run 5. The dependency-free bridge accepts `preflight`,
and reusable worker checks live in `tools/visual_inspection/preflight.py`.

Runtime inputs are `config_path` and optional `board_path`. Inject repository,
interpreter, allowed data roots, bridge artifact root, optional writable data roots,
and timeout through state. Configuration and non-board artifacts may be read from
the sibling repository or allowed read roots. Board/reference reads must stay in
allowed data roots. Runtime parameters cannot override those state roots.

The worker reports configuration/mode, source revision, package versions, board and
reference headers, skips, device availability, calibration provenance, regional
prerequisites, and expected sidecar writes. It uses upstream validators and loads
checkpoint weights on CPU only after cheaper checks pass. No forward pass,
calibration, label write, or sidecar write occurs. Bridge diagnostics and import
caches are confined to the configured HEPTAPOD run directory. Python import
overrides inherited from the caller are replaced by the explicit HEPTAPOD source
root; the sibling cwd supplies its `src` package, without `sys.path` mutation.

A successful tool execution has `status: ok` even when `result.ready` is false.
Read the named `checks` for blockers and skipped checks. Missing files/dependencies,
stale calibration, checkpoint mismatch, routing gaps, and unauthorized sidecar
writes remain distinct. Regional mode selects the template reference, not the
unused legacy reference path. Compatible provenance alone does not guarantee
regional threshold support or coverage. No preflight report establishes accuracy,
GPU memory capacity, future freshness, or a blanket pass for a board.

Use the existing bridge configuration for a read-only check:

```python
report = InspectionBridge(config).execute(
    'preflight',
    {'config_path': 'configs/inspection.yaml'}
)
```

Use `VisualInspectionPreflightTool(...).execute(...)` for the Orchestral wrapper;
its state names are `visual_inspection_repo`, `visual_inspection_python`,
`visual_inspection_data_roots`, `visual_inspection_artifact_root`,
`visual_inspection_writable_data_roots`, and `visual_inspection_timeout`.
Writable roots are an explicit policy input; preflight never probes them by writing.

Run 2 verification on 2026-10-02: all 28 bridge/preflight tests pass under the
HEPTAPOD interpreter, including the Orchestral wrapper. Focused Ruff correctness
checks pass. Synthetic tests use upstream API doubles to verify readiness and
failure handling; they do not validate model accuracy. Actual wrapper calls with
the sibling environment confirm stale default legacy calibration and missing
regional manifest/artifact inputs. With default empty writable roots, both also
report unauthorized future sidecar writes. Size/mtime snapshots of 158 files under
upstream `data/`, `calibrations/`, `logs/`, and `configs/` were unchanged. No real
checkpoint weights were loaded because cheaper checks already found blockers;
no inference or recalibration was performed.

Tests use discovery; `tools/visual_inspection/tests/` has no `__init__.py`, and
production code does not import test modules. To include the agent-wrapper test,
run discovery with HEPTAPOD's interpreter where Orchestral is installed:

```bash
PYTHONPATH=. venv/bin/python -B -m unittest discover -s tools/visual_inspection/tests -p 'test_*.py' -v
```

### Run 3 implementation status

`InspectBoardTool` is available from `tools.visual_inspection.agent_tools`; it
accepts the same configuration/board inputs and state as preflight plus Boolean
`force` (default false). The bridge also accepts operation `inspect`. Package
exports must retain dependency order: sorting imports alphabetically can create
circular imports between package exports and their consumers.

Inspection runs under the sibling interpreter through upstream `inspect_board()`.
It enforces input read scope and explicit writable board-root policy before writes.
Preflight is useful for diagnostics but is not a required full second model load:
upstream inspection remains authoritative for calibration, cache validity, scoring,
and regional uncertainty. Force never bypasses calibration. Unknown routing or
missing regional coverage is not silently converted into a passing result.

Preflight now lists both the sidecar and lock as planned writes without creating
either file. Each canonical board path uses an adjacent `<board>.inspection.lock` with a POSIX
nonblocking advisory lock. Concurrent harness workers fail with `board_busy`, even
when they use different HEPTAPOD artifact directories. Symlink locks/sidecars are
rejected. The empty lock file persists; do not delete it to release ownership.
Ownership ends when the process closes or exits, including timeout termination.
Other writers, including the upstream web server, do not automatically honor this
lock; coordinate those separately. This is not an OS sandbox against hostile
filesystem changes.

Every allocated bridge run now writes `run.json` with start/end times, outcome,
execution/log paths, and available inspection context, including on failure and
timeout. Inspection updates `inspection_context.json` with resolved configuration,
source revision, selected device, input/sidecar/lock paths, force request, and stage.
Success also saves a per-run `inspection.json` snapshot of the complete upstream
result. The sidecar is validated through upstream `InspectionResults.load_json()`
and checked against the returned result, board identity, and configured mode before
success is reported. Worker tracebacks remain in `stderr.log`.

Tool responses contain mode-specific counts, flag grids, paths, and the authoritative
regional decision grid when present. Full regional evidence remains in the snapshot.
`cache_status` is `reused` only after a successful non-forced upstream call leaves
the sidecar identity/timestamps/content unchanged; `refreshed` means the sidecar
was created or changed; otherwise it is `unknown`. `sidecar_observation` exposes
the underlying evidence. Refreshed does not imply every region ran model inference.

A timeout or later artifact error can occur after an upstream sidecar was committed.
Preserve diagnostics and inspect existing results before retrying; do not promise
rollback or automatically delete a sidecar. Readiness, compatibility, and successful
execution still do not establish real-board defect-detection accuracy.

For an already configured bridge with explicit writable data roots:

```python
result = InspectionBridge(config).execute(
    'inspect',
    {'config_path': 'configs/inspection.yaml', 'force': False}
)
```

Run 3 verification on 2026-10-02: all 39 bridge/preflight/inspection tests pass,
including subprocess fixtures for both result modes, cache reuse, force, stale
calibration, sidecar/lock symlinks, concurrent callers using different artifact
roots, timeout lock release, run records, and result identity/mode validation.
Focused Ruff correctness checks pass. The baseline circular import caused by
reordered package exports was repaired.

An additional isolated smoke check exercised the actual upstream `inspect_board`
legacy and regional paths with temporary synthetic boards and a constant model.
Small-shape loading and model/store injection were test-only overrides. Both modes
persisted results, reused cache, refreshed forced calls, and rejected a changed
checkpoint signature even when forced. Regional decisions were 30 OK, one flagged,
one uncertain, and eight skipped. This verifies library interoperability, not real
checkpoint inference or detection accuracy. No sibling source or production
artifacts were modified; real-board acceptance remains pending.

Use `InspectBoardTool(...).execute(...)` for the Orchestral wrapper. Run 5 registers
all three wrappers in `toolkit.yaml`. Real-board acceptance remains
Run 7 because current production calibration is stale.

### Run 4 implementation status

`ReadInspectionResultTool` is available from
`tools.visual_inspection.agent_tools`. Its sole runtime input is `result_path`;
it uses the same configured repository, interpreter, data roots, artifact root,
timeout, and optional writable roots as the other wrappers. Reading requires no
writable board root and creates no board sidecar or lock. The bridge accepts
`read_result`; reusable reader logic lives in `tools/visual_inspection/results.py`.

Relative result paths resolve against the sibling repository. Read scope includes
configured data roots and the HEPTAPOD artifact root, so both upstream sidecars and
previous run snapshots can be read. Symlinks resolve before containment checks;
escaping links and runtime root overrides are rejected. Referenced board/model
paths are reported as saved identity, not followed or required to exist.

The reader accepts regular JSON files up to 32 MiB, rejects duplicate keys and
non-finite numbers, requires the canonical `[8, 5]` source grid, and copies the
captured bytes into a private `inspection.json` snapshot. It checks for changes
during capture and validates the snapshot with upstream
`InspectionResults.load_json()`. Errors retain bridge logs and run records;
a snapshot retained after failed validation is not a valid-result claim.

Results include the upstream schema version (missing means legacy version one),
mode-specific `summary()`, flag grids, zero-based source coordinates, source byte
SHA-256, reader repository revision, and artifact paths. Regional results include
the authoritative decision grid and coordinates for `ok`, `flagged`, `uncertain`,
and `skipped`; zero compatibility flags never imply a pass. Returned metadata,
regional context, and complete segment evidence entries share a 128 KiB budget.
Omitted fields and segment coordinates are listed explicitly; the snapshot retains
all evidence, including reference/calibration identity, coverage, and reasons.
The byte digest identifies the captured file; it is not provenance verification.

`validation.status: valid` means upstream saved-result structural checks passed.
`freshness.status: unverified` is always explicit: reading does not validate current
board/checkpoint/calibration signatures, rerun inference, or establish accuracy.
The upstream loader does not fully validate regional evidence semantics. Use an
explicit inspection request for the upstream current-calibration/cache checks;
reading never silently upgrades a historical result into a verified current one.

```python
report = InspectionBridge(config).execute(
    'read_result',
    {'result_path': '/absolute/path/to/saved.inspection.json'}
)
```

Run 4 verification on 2026-10-02: the 49-test full suite passed, followed by
all 11 reader tests after the final read-error and relative-path additions. Both
runs included the actual upstream loader integration. Focused Ruff correctness
checks passed. Adapter tests cover legacy/local summaries,
uncertainty with zero compatibility flags, strict JSON and upstream validation
failures, read scope and symlink escapes, input/evidence bounds, snapshot rereading,
relative paths, unchanged source files, and Orchestral runtime schema/calls.
The optional actual-upstream loader test uses temporary synthetic JSON, including
invalid legacy flags and inconsistent regional counts. It is enabled with
`VISUAL_INSPECTION_TEST_REPO` and `VISUAL_INSPECTION_TEST_PYTHON` set to absolute
paths. No inference, recalibration, sibling source edits, or production artifact
writes are part of these checks. Registration is implemented in Run 5 below.

### Run 5 implementation status

`toolkit.yaml` registers `VisualInspectionPreflightTool`, `InspectBoardTool`, and
`ReadInspectionResultTool` in the gated `visual_inspection` bundle. Discovery
requires configured repository, interpreter, read roots, and artifact root.
Writable roots default to empty, and timeout defaults to 60 seconds. Configuration
presence permits discovery; it does not establish valid paths or pipeline readiness.
The bundle adds no pip dependencies; ML remains in the separate upstream environment.
Existing bundles and global/project activation settings are unchanged.

Toolbase 0.16.0 supports scalar configuration fields, so read and writable root
lists are declared as strings containing JSON arrays. Shared
`VisualInspectionToolState` validates those strings into `List[str]`, including
assignment during toolbase state injection. Direct Orchestral callers can pass
lists. Invalid JSON, scalar/object roots, and non-string entries are rejected;
absolute-path and containment validation still occurs in `BridgeConfig`.
State fields are excluded from agent runtime schemas.

The real toolbase per-toolkit stdio host exposes `VisualInspectionPreflight`,
`InspectBoard`, and `ReadInspectionResult`. Toolbase's aggregate interface adds
its toolkit namespace. Configuration and activation commands are documented in
`README.md`; select `heptapod/visual_inspection` as the bundle. No global toolbase
configuration or agent connection was changed during implementation.

Registration tests validate metadata with the installed toolbase schema, bundle
gating, typed configuration injection, and runtime schemas. A real MCP client
launches the toolbase host using the registered entries and runs synthetic
preflight → inspection → saved-result reading, followed by cache reuse, forced
execution, and an out-of-scope read failure. The upstream pipeline in this served
test uses lightweight doubles; transport, Orchestral wrappers, state injection,
worker subprocesses, and artifact persistence are real. Separate Run 4 tests
exercise the actual upstream result loader. This does not establish real-model
inference or real-board acceptance.

Run 5 verification on 2026-10-02: all 53 tests passed, including the served
workflow and actual upstream result-loader checks. Focused Ruff correctness checks
and `git diff --check` passed. Verification uses toolbase 0.16.0, Orchestral
1.10.1, and MCP 2.2.0. The MCP client
uses the SDK's automatic protocol negotiation. In this execution environment,
stdio handshake testing requires execution outside the restricted sandbox; the
same sandbox timeout also affects an existing units tool. No server or scientific
code workaround is applied. Run 6 adds workflow skills/examples below; real-board
acceptance remains Run 7.

### Run 6 implementation status

`skills/visual-inspection/SKILL.md` provides bundle-scoped guidance for preflight,
inspection, evidence reading, mode-specific interpretation, uncertainty, cache
observations, and actionable failures. Training, calibration, annotation mutations,
and acquisition remain separate requests. The skill distinguishes saved-result
structural validity from freshness and synthetic operation from real-data accuracy.
Toolbase discovers this skill by directory and its `bundle: visual_inspection`
frontmatter; no additional manifest registration is needed.

`examples/visual_inspection/README.md` documents setup, expected results,
troubleshooting, reproducible synthetic served checks, and a CLI walkthrough.
`bridge.example.json` contains placeholder paths and disables board writes by
default. `scripts/visual_inspection_example.py` is an execution-only CLI with
explicit `--bridge-config` and `preflight`, `inspect`, or `read-result` subcommands.
It uses the bridge without importing CLI modules or altering `sys.path`.

The CLI emits versioned JSON with all step envelopes. Preflight blockers produce
exit 2 and stop inspection, even with force; errors produce exit 1 and success
exit 0. Explicit `inspect` runs preflight → inspect → read snapshot. Standalone
reading requires no board write authorization. The JSON configuration uses native
root arrays, while toolbase scalar settings use JSON-array strings; these are
separate configuration surfaces. Relative inspection paths retain upstream-root
resolution. Each bridge step records its usual private artifacts.

The reproducible synthetic workflow reuses the existing served contract fixture
through unittest discovery, avoiding a second copy of pipeline doubles. It needs
HEPTAPOD's toolbase/Orchestral/MCP environment, no sibling inputs or API key, and
uses temporary synthetic files. The guide explicitly states that the scientific
pipeline is mocked. A separate subprocess test runs the new CLI's successful
sequence, historical read, and blocked forced request with an unchanged sidecar.
The guide also gives regional-reader and optional actual-upstream loader checks.

Toolbase skill discovery and surfacing were checked in a temporary target with
the bundle both absent and available. The generic skill validator does not accept
toolbase's internal `bundle` key; its content check passed on an otherwise identical
temporary copy with only that key removed. Toolbase's actual surfaced copy strips
that internal field. No user/global skills or agent connections were changed.
The Additional Python Conventions section remains unchanged.

Run 6 verification on 2026-10-02: all 55 tests passed, including real MCP
transport with synthetic upstream doubles, the CLI subprocess workflow, skill
surfacing/gating, and actual upstream saved-result loading. Focused Ruff checks,
CLI help, generic skill content validation, and `git diff --check` passed. MCP
transport tests ran outside the restricted sandbox as described in Run 5.
No sibling source or production artifacts were modified.

Real-board inference, compatible production calibration, reviewed regional data,
and held-out accuracy remain pending. Run 7 is the next acceptance step.

---

## Testing Expectations

For new adapters, cover:

- missing repository, interpreter, input, checkpoint, and calibration files
- incompatible board and threshold shapes and out-of-range skipped coordinates
- structured success results and upstream exceptions
- process failure, timeout, and malformed or missing result payloads
- output containment and paths containing spaces
- legacy and local schemas, uncertainty, source coordinates, and mode-specific counts
- calibration validation before cache reuse, including forced runs
- sidecar write containment, same-board concurrency, and child-process cleanup
- imports without installing the visual-inspection ML stack in HEPTAPOD
- one representative real inspection before claiming end-to-end readiness

Use small fixtures and test doubles for adapter tests. A mocked pipeline verifies
the harness contract, not scientific correctness. Run focused existing tests when
shared framework behavior changes; external HEP tools may require separate
installations and configuration.

Run pytest from repo root with explicit import path:

```bash
PYTHONPATH=. python -m pytest path/to/test_file.py
```

---

## Additional Python Conventions

### Formatting and alignment

- Do not align equals signs.
- Do not put an unnecessary trailing comma after the final parameter,
  argument, collection element, or import name. This applies to definitions,
  calls, class instantiations, lists, tuples, dictionaries, sets, and imports,
  whether written on one line or multiple lines. Preserve commas only when
  required to retain syntax or meaning, such as a singleton tuple `(value,)`
  or a singleton tuple index `tensor[index,]`. Multiple-element tuples do not
  need a trailing comma.
- Use four-space indentation and ordinary spacing between tokens; do not
  enforce a fixed line-length limit. Wrap for readability, not solely to
  shorten a line. Keep readable expressions, messages, and short conditional
  assignments together rather than isolating individual integers or arguments.
- Keep simple `if` and `elif` conditions compact. When wrapping helps, use
  parentheses and break before each `and` or `or`, preserving logical grouping.
  Tensor masks use `&` and `|` with parenthesized comparisons; do not replace
  those operators with Python `and` and `or` during formatting.
- Function, method, and class definitions may keep their parameters on one
  line. If the parameter list is multiline, keep the opening parenthesis on
  the definition line, put every parameter on its own continuation line, and
  put the closing parenthesis on a separate line. This includes `self`, `cls`,
  class bases, and class keyword arguments; put `*` and `/` separators on their
  own lines too. Keep short tuples and other expressions within a parameter
  together; do not split their internal commas into separate parameters.
- Function calls and class instantiations may stay on one line. If a call
  is multiline, keep the opening parenthesis with the callable name, put every
  argument on its own continuation line, and put the closing parenthesis on
  a separate line. This applies even to short related arguments and keyword
  arguments. Keep nested tuples, shapes, and other individual argument
  expressions together when readable; their elements are not separate call
  arguments.
- Keep shapes, coordinate tuples, and short matrix rows together. Expand larger
  collections by logical entry, without a trailing comma after the final element.
- For a multiline method chain, keep `.method(` attached to its receiver or
  the preceding call's closing `)` on the same line; do not start a new line
  with `.method`. Place arguments on the following indented line and the
  closing `)` on the next line. Apply this layout to the initial call too.
- Within a multiline chain, put each argument of a multiline call on its
  own continuation line, including short arguments. Do not add a trailing
  comma after the final argument. Continue the chain with `).next_method(`,
  using the same layout for its arguments.
  Argument-free calls such as `.clone()` may stay inline. Short chains that
  fit entirely on one line need not be expanded.
- Short, related loop or conditional blocks may be grouped without blank lines
  between them, especially consecutive edge-case checks or guard clauses.
  Always leave one blank line after the final block in the group before the
  following statement, including a fallback `return`.
- Leave one blank line after a completed control-flow block (`if`, loops,
  `with`, `try`, or `match`) before a following statement at the same indentation.
  The grouped short-block exception above applies only between related blocks,
  never after the final block. Do not insert blank lines between a block and
  its continuation clauses (`elif`, `else`, `except`, or `finally`), or add
  trailing blank lines merely because a block ends its enclosing scope.

Examples of wrapping long conditions and function calls:

```python
if (
    images.ndim != 4
    or images.shape[1] != 3
    or images.shape[0] != expected_camera_count
):
    raise ValueError("Unexpected camera image dimensions.")

projection = project_ego_points(
    points_ego,
    camera_from_ego,
    camera_intrinsics,
    image_size=(image_height, image_width)
)
```

Example of a multiline method chain:

```python
matrix = (
    torch.eye(
        n=4,
        device=translation.device,
        dtype=torch.float32
    ).expand(*translation.shape[:-1], 4, 4).clone()
)
```

### Docstrings

- Do not add module-level docstrings or descriptive file-header docstrings.
  Start Python files with their imports (after any required interpreter or
  encoding directive); keep documentation on relevant classes and public APIs.
- In docstrings and user-facing messages, surround tensor/array shapes and
  inline variable references with one backtick on each side, for example
  `[B, T, C, H, W]` and `history_valid_mask`. Do not use double backticks or
  leave shapes unquoted. Apply this to interpolated shapes in error messages
  too. Keep NumPy-style parameter/type declarations unquoted.
- Do not add docstrings to Python files outside `src/`, including scripts
  and tests. This applies to module, class, and function docstrings.
- Inside `src/`, keep NumPy-style docstrings on major reusable classes such as
  trainers and their variants, models, datasets, and configuration objects,
  and on APIs intended for direct use from notebooks or scripts.
- Do not add docstrings to internal helpers, including functions or methods
  whose names start with a single `_`, nested implementation helpers, or
  internal-only functions even when their names have no underscore prefix.
  A public-looking name or package re-export alone does not require a docstring.
- Public entry points implemented through special methods, such as `__init__`,
  `__call__`, and `__getitem__`, may retain docstrings as part of a documented
  class interface. These are not private helpers merely because of their names.
- Put opening and closing triple-double-quote delimiters on separate lines.
  Start the description on the line after the opening delimiter, even for
  a one-line description.
- In NumPy-style parameter declarations, put one space before and after
  the colon: `param_name : Type`.
- Use annotation-style collection names in docstring parameter and return
  fields: `List[Dict]`, `Dict`, `Tuple[Tensor, Tensor]`, and `Sequence[str]`,
  rather than prose such as `list of dict` or `tuple of Tensor`. Keep scalar
  types such as `str`, `int`, and `float` lowercase. Include element types
  where useful and accurate.
- Express optional types and alternatives in docstrings using `, optional`
  and `or`: `List[Tensor], optional`, `Tensor or Tuple[Tensor]`, and
  `str or Path`. Combine them as `Callable or List[Callable], optional`.
  Use this notation in parameter and return fields instead of `Optional[...]`
  and `Union[...]`. Actual Python type annotations retain `Optional[...]`
  and `Union[...]`; this rule changes documentation notation only.
- Do not leave trailing spaces after closing docstring delimiters.
- In functions and methods, start the body immediately after the closing
  docstring delimiter, without a blank line. Leave one blank line after a
  class docstring before its attributes or methods.
- Use ordinary comments where an internal implementation needs explanation.

```python
"""
Description goes here.

Parameters
----------
param_name : Type
    Description.

Returns
-------
ReturnType
    Description.
"""
```

### Quotation rules

- Use single quotes for:
  - dictionary keys
  - stable internal identifiers
  - strict parameter values
  - configuration constants
  - file modes and encodings
- Use double quotes for:
  - error messages
  - print and log messages
  - user-facing text

### Naming

- Use `snake_case` for functions and variables, `PascalCase` for classes, and
  `UPPER_SNAKE_CASE` for constants. Prefer descriptive names.

### Typing rules

- Annotate parameters and return types.
- Do not inline type-annotate local variables inside function bodies.
- Prefer `typing` generics such as `List`, `Dict`, `Tuple`, and `Optional`.
- Prefer `Tensor` instead of `torch.Tensor`, so let's put Tensor in the statement
  `from torch import ...` along with other modules.

### Import conventions

- Import project symbols from package namespaces, not implementation filenames:
  use `from src.utils.data import open_board`, not
  `from src.utils.data.board_contract import open_board`. The same rule applies
  to relative imports, scripts, tests, notebooks, and documentation examples.
- Direct imports from explicitly private modules whose filename starts with `_`
  are allowed, for example `from tests._http import request`. An underscore on
  the imported symbol alone does not make its source module private.
- Define package exports in `__init__.py`; implementation-module imports there
  establish the package API. Keep those exports in dependency order. Consumers
  inside a package use `from . import name`; cross-package consumers import
  from the owning package. Do not rename modules solely to evade this rule.
- This rule governs repository modules, not third-party library APIs. Keep
  low-level dependencies acyclic; avoid dynamic imports as a workaround.
- Inspection score functions live in `src/utils/metrics.py` and are exported
  through `src.utils`. Do not reintroduce `inspection_scores.py` or a forwarding shim.
- Group related tests by behavior: region contracts/geometry, annotation
  persistence/API/migration, and local calibration/inspection. Reuse private
  helpers instead of importing another test module; preserve all test coverage.

- Group imports in this order:
  1. standard library
  2. third-party libraries other than PyTorch
  3. PyTorch imports in their own group to make the deep-learning dependency clear
  4. first-party or relative imports, with relative imports ordered by increasing
     depth (fewer leading `.` characters first)
- Separate each import group with a blank line.
- Preserve required initialization order, such as selecting a Matplotlib backend
  before importing pyplot. Keep deliberate deferred or type-checking imports
  where they are needed rather than moving them mechanically to module scope.
- Prioritize `import` before using `from` in each import groups.
- Use relative imports inside `src/` packages when practical.
- Use absolute `src...` imports in `tests/` and `scripts/`.
- Do not mutate `sys.path`.

### File organization

- CLI entry points belong in `scripts/`.
- `scripts/` files are execution-only and must not be imported elsewhere.

### Experiment artifacts

- Treat `logs/` and `outputs/` as repository-relative directories.
- Use `outputs/` for visualizations, result tables, final diagnostic reports,
  and decoded prediction results intended for inspection.
- Create output directories only when writing artifacts, not during trainer
  initialization. Tests that export figures must use temporary output paths.
- Use YAML for reusable experiment configuration files consumed by scripts or
  jobs, including training in tmux, with named sections such as `data`, `model`,
  `loss`, and `train`. Notebooks declare parameters directly in their cells.
  JSON remains appropriate for scene manifests and resolved run snapshots.
- Store reusable YAML configuration files under `configs/`, grouped by experiment
  when useful. Masking JSON manifests, templates, and regional findings belong
  under `calibrations/`, as requested for this project. Do not put reusable inputs
  in `logs/setup` or a run's logging folder.
- Store training logs, checkpoints, model weights, resolved per-run configuration
  snapshots, manifest snapshots, environment records, and source provenance
  under `logs/`. Shared input configs and immutable run snapshots have different
  roles; retain run snapshots for reproducibility.
- Keep corresponding experiment names and run identifiers in both trees, and
  record the result directory in the run metadata. Resume from the run in
  `logs/`; do not mix resumable state into `outputs/`.

### Visualization helpers

- Public visualization functions display the completed figure with `plt.show()`
  by default. Close it afterward and return None so a notebook does not display
  the same figure twice through its final expression.
- Accept `savefig=None`. A supplied str or Path saves a PNG at 300 DPI before
  display; create its parent directory when necessary. Use `outputs/` for plots.
- Support `show=False` for composition, tests, and headless callers. Return the
  figure (or named figure dictionary) in that mode; the caller owns closing it.
  Composed helpers must not display incomplete nested figures.
- For multi-figure helpers, document how the supplied path generates filenames.
  `plot_model_batch` uses the path stem plus `_temporal_inputs`, `_camera_targets`,
  and `_bev_targets`, each with a `.png` extension.
- Leave explicit vertical space between camera rows so lower-row camera titles
  never overlap upper-row images. Account for figure headings in the layout.
- Keep BEV plots square with equal metric axis scales.

### Training notebooks

- Instantiate Config classes with explicit parameter values in notebook cells
  so readers can see the setup. Do not load experiment parameters from YAML;
  reserve YAML inputs for scripts and jobs. Persist resolved run snapshots
  through the trainer for reproducibility.
- Use the configured project training dimensions and the 180-training-scene /
  20-model-validation-scene split by default. Do not silently reduce these for
  demonstration convenience; synchronize Markdown and code configuration.
- Display results using a named variable as the final cell expression when
  practical, including named DataFrames or Series. Put independently useful
  outputs in separate cells when needed.
- Otherwise use `print()`, including for labeled status messages, multiple
  scalar results, or live training progress. Do not construct anonymous
  dictionaries or artificial summaries merely to avoid `print()`.
- Call visualization helpers directly; avoid redundant `display()`, `savefig()`,
  and `close()` boilerplate around helpers that already handle those operations.
- Keep logging/checkpoint artifacts in `logs/` and exported figures/result tables
  in `outputs/`. Do not execute notebooks merely to edit or statically check them.

### Test invocation

- Run pytest from repo root with `PYTHONPATH=.`
