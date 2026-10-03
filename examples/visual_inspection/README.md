# Visual inspection harness example

Run commands from the HEPTAPOD repository root. The examples exercise processed
board inspection, not scanner acquisition. No API key is needed.

## Reproduce the synthetic workflow first

Use HEPTAPOD's environment with Orchestral, toolbase, MCP, and PyYAML installed.
The served check was verified with Orchestral 1.10.1, toolbase 0.16.0, and MCP 2.2.0.
It creates a temporary upstream API fixture, synthetic result JSON, and temporary
logs/sidecars; it removes its temporary directory afterward. No sibling repository
or production configuration is needed. Pipeline calculations are test doubles.

```bash
PYTHONPATH=. venv/bin/python -B -m unittest discover \
  -s tools/visual_inspection/tests -p 'test_inspection.py' \
  -k served_synthetic_workflow -v
```

Expected: one passing test. It launches the actual toolbase stdio host, discovers
all three tools, checks ready preflight, inspects a synthetic legacy board, reads
the saved snapshot, verifies cache reuse and forced execution, and rejects an
out-of-scope result read. It does not activate a bundle in your user settings.
The MCP handshake needs working local process pipes; in the restricted execution
sandbox used during development it timed out, while the same test passed outside.

Verify the CLI example's preflight → inspect → read sequence separately:

```bash
PYTHONPATH=. venv/bin/python -B -m unittest discover \
  -s tools/visual_inspection/tests -p 'test_inspection.py' \
  -k documented_example_cli -v
```

Expected: one passing test. This uses the same temporary API fixture and runs the
CLI below as a subprocess, including a blocked forced request with no sidecar write.
For regional uncertainty, including zero compatibility flags, run the reader tests:

```bash
PYTHONPATH=. venv/bin/python -B -m unittest discover \
  -s tools/visual_inspection/tests -p 'test_results.py' -v
```

One actual-upstream loader test is skipped unless
`VISUAL_INSPECTION_TEST_REPO` and `VISUAL_INSPECTION_TEST_PYTHON` contain absolute
paths to the sibling repository and its Python interpreter. That optional check
uses temporary JSON; it does not run inference.

## Configure your own inputs

Copy `examples/visual_inspection/bridge.example.json` to a private location and
replace its example paths. The CLI expects JSON arrays for roots. All configuration
paths must be absolute; preserve the upstream venv interpreter symlink. The
`data_roots` must cover board and reference tensors. `writable_data_roots` defaults
to empty; add only the intended board directory, inside a read root, when inspection
is authorized. The artifact root receives logs and snapshots but does not relocate
the upstream `<board>.inspection.json` sidecar or `.inspection.lock`.

The upstream environment must already provide its scientific dependencies and
compatible inputs/calibration. Do not install that ML stack into unrelated bundles.
Use the upstream inspection YAML, not a training YAML with incompatible model
settings. The last audited production calibration was stale; preflight reports
current conditions and this example never repairs them.

```bash
PYTHONPATH=. venv/bin/python -B -m scripts.visual_inspection_example \
  --bridge-config /absolute/path/to/bridge.json \
  preflight --config-path configs/inspection.yaml
```

Preflight runs without inference. The CLI emits JSON with `steps` holding the full
bridge responses. Exit 0 means ready/success, 2 means preflight blocked, and 1 means
an execution/configuration error. Inspect the checks even if the individual bridge
envelope has `status: ok`. The configuration argument belongs before the subcommand.

Once ready and authorized to write the board result:

```bash
PYTHONPATH=. venv/bin/python -B -m scripts.visual_inspection_example \
  --bridge-config /absolute/path/to/bridge.json \
  inspect --config-path configs/inspection.yaml \
  --board-path /absolute/path/to/board-data/board.npy
```

This CLI runs preflight, stops if blocked, then inspects and reads the returned
snapshot. `--board-path` is optional; omitting it uses the board in the YAML. Add
`--force` to request recomputation; calibration validation still applies. The CLI
performs an extra preflight model check; direct tools need not repeat preflight
before every unchanged, already-diagnosed request.

Read a historical result independently, without requiring its original board:

```bash
PYTHONPATH=. venv/bin/python -B -m scripts.visual_inspection_example \
  --bridge-config /absolute/path/to/bridge.json \
  read-result /absolute/path/to/inspection-runs/run/inspection.json
```

Reading accepts files inside data roots or the artifact root and marks freshness
as unverified. It preserves full evidence in a new snapshot while bounding the
response. Regional `decision_grid` is authoritative: `uncertain` is not a pass,
and neither are zero compatibility flags. Coordinates remain stored zero-based
`(row, col)` order.

## Agent and MCP setup

Use the configuration commands in [the repository README](../../README.md#visual-inspection-bundle)
for the `visual_inspection` bundle. Toolbase root settings are JSON-array **strings**;
the CLI JSON file uses native arrays. These are two explicit configuration surfaces,
not automatically synchronized settings. The packaged
[workflow skill](../../skills/visual-inspection/SKILL.md) is scoped to this bundle.
Normal toolbase connection/surfacing handles its installation; these examples do
not change an agent connection or global skill directory.

## Troubleshooting and acceptance limits

| Symptom | Next action |
| --- | --- |
| Tools absent | Check bundle activation and the four required config values; discovery is not pipeline readiness. |
| `ready: false` | Read named checks; resolve missing files, dependencies, model mismatch, or write policy in the relevant environment. |
| Stale calibration | Arrange a separate, explicit calibration action; do not force past validation or replace canonical calibration as an incidental repair. |
| Missing regional inputs | Obtain reviewed board identities, templates, labels/visibility, and compatible thresholds. Do not assume unknown regions are normal. |
| `board_busy` | Wait for or investigate the owner. Never delete the persistent lock file to release it. |
| Timeout/artifact failure | Read `run.json` and stderr. A sidecar may already exist; read it before retrying. |
| Evidence omitted | Follow `result_path` to the full snapshot; omitted evidence is not a negative finding. |

Keep acceptance claims separate: bridge tests check the adapter; served tests check
MCP discovery and execution with doubles; actual-upstream loader checks validate
saved-result interoperability. None establishes real-board inference or held-out
defect accuracy. Run 7 remains the real-data acceptance step. Training, calibration,
annotation mutations, and scanner operation are separate requests.
