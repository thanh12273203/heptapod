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

## Whole-Segment Inspection Contract

HEPTAPOD supports whole-segment inspection only. The three MCP tools remain
`VisualInspectionPreflight`, `InspectBoard`, and `ReadInspectionResult`.
Their runtime arguments and injected configuration stay stable. Unsupported
configuration and saved-result schemas must fail explicitly, never be silently
interpreted as whole-segment results.

The pipeline stays in the sibling repository. `tools/visual_inspection/upstream.py`
resolves package exports and the audited legacy implementation locations when an
export is absent. This narrow compatibility boundary may import
`src.configs.inspection_config`, `src.inspection.run`, `src.inspection.model`, and
`src.utils.data.board_contract`. Dependency failures must propagate unchanged.

Stored boards are BGR uint8 arrays of shape `(8, 5, 1060, 1882, 3)`.
Use upstream memory-mapped board validation. Coordinates are zero-based `(row, col)`
in stored order; the UI reverses presentation only. The skip map is authoritative;
the canonical mask leaves 32 of 40 positions inspectable.

Use `InspectionConfig.from_yaml()` followed by
`inspect_board(config, board_path=None, force=False)`. YAML paths resolve against
the configured upstream root; explicit board overrides resolve to absolute paths.
Do not bypass provenance or cache policy through lower-level inference APIs.
Calibration is validated before cache reuse. Force recomputes but never repairs
stale calibration. The board sidecar is `<board>.inspection.json`; an artifact
root does not relocate it. Writable board roots must be explicitly authorized.

Whole-segment scoring uses `1 - SSIM` for pixels and MAE after sigmoid for
reconstructions. Flags use `score >= threshold`; hybrid is the intersection.
Preserve `pw_flags`, `ae_flags`, `hybrid_flags`, `baseline_path`, `inspected_path`,
and `metadata`. Flags encode `-1 = skipped`, `0 = OK`, `1 = flagged`.
Absent upstream schema version means version one, independently of tool schemas.
Keep the existing `mode: legacy` response value for client compatibility.

Preserve checkpoint-compatible model settings. The demo checkpoint uses latent
dimension 64, initial filters 128, and layers `[2, 2, 2]`; training configuration
is not automatically interchangeable. Model loading and scientific dependencies
belong in the separately configured worker environment.

Record inputs, upstream revision where available, checkpoint/model settings,
calibration, selected device, timing, outcome, and diagnostics. Keep tensors and
weights out of responses and Git. Saved-result loading validates structure but
reports freshness as unverified; it does not recheck the current scientific inputs.
Flags are pipeline decisions, not confirmed physical defects or accuracy evidence.

## Execution Boundaries

All task edits and persistent writes stay inside HEPTAPOD. Treat the sibling as a
read-only source of code and inputs; do not switch its branch or write source,
sidecars, calibrations, caches, labels, or environments there. Keep copied demo
inputs and generated outputs under the ignored
`examples/visual_inspection/artifacts/` directory. Use independent copies, hashes,
and an original-to-copy manifest rather than symlinks or hard links. Regenerate
calibration for copied paths; never rewrite provenance to make it pass.

Inspection must not silently train, recalibrate, scan, or change annotations.
Those are distinct actions. Real-data readiness and held-out accuracy are separate
from adapter readiness. Retain logs and failures rather than plausible empty results.

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
2. Reuse upstream whole-segment calibration validation. Distinguish stale
   artifacts, missing inputs, and missing dependencies.
3. Do not run inference or repair calibration during preflight. Do not use board
   metadata HTTP endpoints that can trigger inspection.
4. Account for `<board>.inspection.json` beside the board. Require an explicitly
   configured writable data root before inspection; a HEPTAPOD output directory
   alone does not relocate the sidecar. Add an upstream destination option if
   read-only datasets must be supported, without moving data to bypass provenance.

Completion check: stale calibration and missing inputs are reported correctly
without changing data or calibration.

### Run 3: Board inspection tool

1. Implement `InspectBoardTool` around `src.inspection.inspect_board()` with config,
   board, and force inputs. Preserve upstream provenance and caching.
2. Validate board-sidecar write containment and prevent simultaneous harness jobs
   from writing the same board result.
3. Reuse bounded subprocess execution and save logs plus a run record containing
   resolved inputs/configuration, revision, timing, and outcome.
4. Return compact results and artifact paths. Report cached versus recomputed
   execution only when evidence supports the distinction.
5. Do not automatically recalibrate on failure or bypass validation with force.

Completion check: synthetic whole-segment inspections work, stale calibration fails
when forced, and errors/timeouts retain useful diagnostics.

### Run 4: Result reading and interpretation

1. Implement `ReadInspectionResultTool` around upstream validated result loading.
2. Support whole-segment payloads with upstream schema absent or equal to one.
   Reject unsupported schemas before loading them as whole-segment results.
3. Return pixel, autoencoder, hybrid counts, source coordinates, and metadata.
   Keep responses bounded and retain full evidence in artifacts.
4. Distinguish a structurally valid saved result from a verified current result.

Completion check: counts and flags survive serialization; malformed and unsupported
results fail explicitly.

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
   reading, interpretation, calibration errors and caching.
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
4. Document label support, physical-board overlap, and calibration limitations.
   Do not fabricate missing review data.

Completion check: record reproducible real-board results and limitations.
Operational acceptance is not held-out detection-accuracy validation.

### Run 8: Calibration and evaluation tools

1. Wrap existing `calibrate()` as separate explicit actions.
2. Return artifact locations, provenance, sample support, and support gaps.
3. Wrap legacy `analyze()` and `evaluate_reconstruction()`; distinguish reconstruction
   loss from defect-detection metrics and identify evaluation inputs.

Completion check: generated calibration can be consumed by inspection and reports
state their method, dataset, and limits accurately.

### Run 9: Training job management

1. Launch training from an explicitly selected YAML and return a job identifier.
2. Provide status/log retrieval, cancellation, and checkpoint/output locations.
3. Handle failed jobs, interruption, and supported resume behavior.
4. Validate a short bounded target-device run before longer experiments.

Completion check: an agent starts, monitors, stops, and reads artifacts from a short
training job. Scanner operation and annotation mutation remain separate scopes.

## Implementation Status and Verification

Runs 1–6 provide the subprocess bridge, preflight, board execution, saved-result
reader, gated MCP registration, workflow skill, and examples. The original nine-run
sequence above remains the roadmap; calibration/evaluation tools and training job
management are separate future work.

The whole-segment demo on `visual-inspection-main-demo` established operational
acceptance using frozen upstream revision `1e995de87cb4758c6d1e145702e6ce5bb3c316a7`.
Generated artifacts can be present locally across branch switches, but do not imply
that every branch contains that branch's preparation and reporting scripts.
Keep detailed acceptance and recovery records with those artifacts. Do not rewrite
historical evidence to imply execution by subsequently modified code.

Run the focused bridge, preflight, inspection, reader, and registration tests after
adapter changes. Verify stdlib-only host imports, path containment, timeouts,
locks, cache/force behavior, stale calibration rejection, and saved-result freshness.
Use the served synthetic workflow to check MCP schemas and execution; optional
actual-upstream reader tests use `VISUAL_INSPECTION_TEST_REPO` and
`VISUAL_INSPECTION_TEST_PYTHON`. Test the configured real demo separately and report
whether it reused cache or performed inference. A transport restriction in a sandbox
must not be mistaken for pipeline failure.

The test folder is not a package: do not add `tests/__init__.py` or import tests
from production code. Run discovery from the repository root with `PYTHONPATH=.`.

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
