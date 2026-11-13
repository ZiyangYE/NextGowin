# Clock/data boundary experiments and route-policy provenance

Trial: `results/clock_cone_joint_trial_020`. The unchanged round budget is 3300 seconds,
including preparation, implementation, validation and closeout. The four-design
120%-of-fair-vendor objective remains unfinished. These are engineering experiments
at their source constraints, not evaluations against newly frozen competitive targets.

## What the experiments establish

The six-IOREG int2float candidate from trial019 has a five-LUT upstream path and
shared fanout cones between right-edge and bottom-edge outputs. Pulling only its
last three levels toward output pads created spatial backtracking: one critical
path's routing grew to 6.089 ns. Global wirelength relaxation also failed to predict
timing: weighted wirelength fell from 58,999.5 to 21,498.1, but setup and hold worsened.
Neither a lower wirelength nor a lower maximum clock insertion delay establishes a
better implementation. Native routed timing is the acceptance test.

| Candidate | Native Fmax MHz | Setup WNS ns | Hold WNS ns | Decision |
|---|---:|---:|---:|---|
| Trial019 int2float source | 157.744 | -0.369 | +0.005 | Below retained 167.613 |
| 40 LUT output-cone moves | 131.201 | -1.652 | +0.005 | Reject |
| Latency-only clock search | 150.328 | -0.682 | +0.006 | Reject |
| Whole soft-logic/FF relaxation | 107.953 | -3.293 | -0.156 | Reject, including hold |
| Setup/hold-aware clock search | 163.009 | -0.164 | +0.005 | Improves trial019, below incumbent |
| Fixed-state minmax geometry | 159.843 | -0.286 | +0.005 | Reject; surrogate predicted +0.2857 setup |
| Clock search with external-launch proof | 163.459 | -0.148 | +0.002 | Below incumbent |
| UART latency-only clock search | 196.941 | -0.002 | +0.038 | Below retained 197.028 |
| Vex minmax geometry, default route policy | 73.883 | -4.444 | +0.133 | Reject; policy mismatch identified |
| Vex identical geometry, restored policy | 73.006 | -4.607 | +0.133 | Reject; policy mismatch does not explain regression |
| Vex whole changed-net tree rebuild | 75.164 | -4.213 | +0.133 | Reject; below retained 90.380 |

The complete round closed in 2894.865 seconds (48.25 minutes), within its original
3300-second budget. Workspace size at closeout was 39,359,480,735 bytes, below the
40,000,000,000-byte limit. Reserve additional headroom before another large attempt.
No incumbent or competitive denominator was changed.

TMDS's fixed-state minmax search found no improvement of worst modeled slack and
made zero moves; no new native frequency is claimed for it. Final Vex policy-control
metrics and the round elapsed time are in `completion_audit.json`.

## General implementation changes

- `prepare_ownpr_ioreg_cones.py` makes cone-only placement experiments reproducible.
- `ownpr_global_relayout.py` now represents calibrated IOREGs as fixed endpoints,
  includes their data/control edges, and verifies their placement after legalization.
  Core FFs remain movable as complete packing units. The logical netlist is unchanged.
- `ownpr_placement_feedback.py` maps the native OBUF/O wire report to the logical I
  input, consistently with the existing path-demand parser.
- `ownpr_minmax_geometry.py` scores candidate moves by repropagating competing paths
  through the combinational graph. State, clock endpoints, LUT slots, hard macros,
  IOREGs and package pins stay fixed. Distance-delta wire estimates are explicitly
  ranking surrogates; they do not model hold or all native endpoint transitions.
  The initial experiment used alphabetical enumeration. The current version orders
  proposals by propagated slack and uses `ownpr_arrival_evaluator.py` to avoid
  rebuilding topology and diagnostic path dictionaries for every proposal. Exact
  baseline propagation is checked against the original engine.

The fixed-state restriction is an important search limitation, particularly for
TMDS input-to-register paths. It must not be described as complete joint placement.

## Safe tightening of the clock hold prefilter

Gemini039 suggested that the global launch-delta bound unnecessarily constrains
external-input hold paths. The old bound was conservative, not an unsafe timing
calculation. Merely seeing `launch=None` in one reported path is insufficient to
exclude unreported clocked competitors.

`external_launch_proof` walks the entire structural fanin of the exact hold endpoint.
Only non-clock primary IBUFs, constants, ordinary LUTs and supported wide muxes are
admitted. Any state, hard primitive, ambiguous driver, cycle or clock-as-data input
retains the old bound. All LUT inputs are walked regardless of Boolean sensitivity.
For proven external-only endpoints the launch-clock delta is zero; capture-clock
changes still affect hold. Recovery/removal retain their original conservative guards.
Eleven of 25 int2float hold endpoints received this proof. Mixed-state and cycle
negative controls reject it. Native validation of the selected tree reports hold
+0.002 ns and setup -0.148 ns, consistent with the approximate search margins at
report resolution. Full native STA, pulse-width, connectivity and encoding checks
remain mandatory.

## Route-policy inheritance defect and controlled comparison

The retained Vex route was generated by `ownpr_joint_lut_search.py`, which explicitly
sets heuristic scale 0.03, root-arrival weight 1, guided corridors and guide-edge
delay 0.05. Its historical manifest omitted those fields. `run_ownpr_checkpoint.py`
therefore used scale 0.001 and guide-edge delay 1 when replaying a changed placement.
The first Vex trial retained 6538 nets and rerouted 69; it is a confounded layout
experiment, not evidence that the layout alone caused the full regression.

`vex_route_policy_provenance.json` checks the archived operator against its recorded
hash and extracts its literal policy assignments without executing archived code.
The second Vex run uses the identical candidate placement and original source
routes, explicitly restoring bias 30, root weight 1 and guide delay 0.05. This
isolates routing-policy impact. It regressed further to 73.006 MHz, so the missing
policy was a reproducibility defect but not the main explanation of this candidate's
regression. Historical manifests remain unchanged.

One new worst path has 10.356 ns data routing, including 4.087 ns from a fixed FF
at R72C96 to a moved LUT at R70C98. This demonstrates why a small Manhattan change
cannot predict the routed result. Independent endpoint queries show that this same
`ng_coopt_1003/I1` wire was 0.347625 ns at the original R71C98 site; moving it one row
made it 4.087125 ns under both route policies. See
`vex_critical_input_wire_comparison.json`. A third controlled run keeps the same candidate
and restored policy but rebuilds entire changed-net trees, preserving only fully
unchanged nets. `--no-reuse-branches` exposes this generic experiment explicitly;
it reaches 75.164 MHz with hold +0.133 ns. That improves the rejected 73.006 MHz
candidate but remains well below the 90.380 MHz incumbent. Branch reuse alone also
does not explain the full regression.

New joint-search manifests serialize the actual routing policy. Checkpoint reuse
now rejects absent policy fields unless explicit values are supplied, before native
binding/capture work. A silent default is no longer presented as inherited policy.

## Evidence, reproducibility and limitations

Each implementation/clock runner manifest contains its exact commands and source
hashes. Placement candidates retain `operator_source.py.txt` or the cone-preparation
source snapshot in the round sources. The clock experiments retain their helper
snapshots, search alternatives, captures, data trees and full export evidence.
For an exact historical proposal, use its archived operator with the project
`scripts` directory on Python's module path; the current minmax search has a newer
ordering/evaluator and is not claimed to reproduce the earlier alphabetical search.

The compiled arrival evaluator was additionally cross-checked on the retained Vex
full late graph: all 24,435 node vectors match exactly across 37,666 arcs. Five-run
median evaluation time was 18.807 ms versus 364.800 ms for the diagnostic propagator
(approximately 19.4x). This excludes graph compilation and measures only fixed-DAG
arrival evaluation; it is not a synthesis/P&R runtime or FPGA Fmax claim. The
benchmark is in `compiled_arrival_benchmark.json`.

The first direct clock-candidate entry rejected missing placement constraint-hash
fields; subsequent searches used the already source-bound clock-closure entry.
The external-launch clock export and the first generalization driver were stopped
by storage reservations before exceeding capacity. The exact clock tree was later
exported in `int2float_clock_external_export_002` under the original round deadline.
Do not relabel the initial failures as successful.

Four cleanup inventories preserve lossless decoded hashes while compacting 54
superseded HTML presentations, saving 1,612,073,127 bytes. Current incumbent STA,
native calibration/reference, source-proof artifacts and the delivered archive
were excluded. The 58 incumbent source hashes were revalidated with no changes.

The final focused suite has 38 passing tests. A first added test incorrectly equated
IOLOGIC tile coordinates with the OBUF routing exit; the native exit may be one tile
inward. The test now checks both independently against their recorded physical
coordinates. This was a test assumption correction, not pin relocation.

Next work should use routed, loaded timing inside placement/mapping transactions
and expand legal moves across FF/wide-macro boundaries with refreshed clock and
hold models. In particular, distinguish changed-branch repair from changed-tree
rebuild and selective release of neighboring frozen routes; the latter may be
needed when a locally moved sink cannot reach a short path under frozen occupancy.
This is a hypothesis to test, not a claim of proven congestion causality.
Do not continue promoting distance-only improvements. Fair official
baseline provenance and source/hard-state proof remain separate unfinished gates;
the 63.729 MHz relaxed Vex calibration is never a competitive denominator.
