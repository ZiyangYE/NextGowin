# WIP checkpoint: conditional padding projection and remaining address obligations

This checkpoint is incomplete. The four-design20% objective, fair official
baselines and complete source/configuration proof remain unfinished. Native
reports remain UART197.028, int2float167.613, TMDS137.743 and Vex90.380MHz. No
hardware implementation, fixed pin, interface cycle or timing constraint changes
in this round. The user's requested pause/publication follows this checkpoint.

## Substantive result

The new padding projection is rebuilt from the original array-model hashes and
explicit lane mappings. It re-proves that physical read lanes22..31 of both tag
banks cannot influence any original output, memory input or state clock. Only
two artificial `padding_write == known zero` monitors are replaced by this
noninterference proof. Original functional cells, fanouts, INIT, original IO and
all logical memory masks/data/addresses/clocks remain unchanged. Both actual
array models reject negative controls that expose padding as an output.

`projected_controller_001` contains the projection certificate and transformation
source hashes. `prior_controller_projection_audit.json` independently compares
the new views with trial031's unprojected views. This is a conditional projection
inside the array/controller model, not SDK primitive or full-source admission.

The463-observer product passes structure, state/INIT preservation and its original
four-step base. Adding independently proved definedness does not close induction.
Backward tracing from paired successor registers discovers33 previously hidden
pipeline-state relations, bringing the observer count to496. These are assertions
to prove, never assumptions or functional substitutions. Their original four-step
base passes too. Whole-product induction still times out at180s.

`formal_state_values.py` adds conservative reachable three-value domains. It
preserves unknown initialization: for example `{0,X}` is not INIT0. On the actual
model it proposes183 invariants:163 bits in `{0,1}` and20 in `{0}`. Separate
original-initial-state and one-step induction checks prove all183. Deliberately
asserting initial zero for an uninitialized test register correctly fails.

## A decisive remaining counterexample

`direct_induction_007` reuses the proved four-step prefix plus the independently
proved state invariants and executes an actual four-step induction query. It
returns a model, rather than merely timing out. All retained comparison outputs
agree in its history; its final frame fails exactly:

- `ng_mem_3_part_0_write_high`
- `ng_mem_4_part_0_write_high`
- `ng_mem_5_part_0_write_high`
- `ng_mem_6_part_0_write_high`

These are the four byte-array write-address upper-bound obligations. This is an
arbitrary-state induction trace, **not an original-initial-state reachable
hardware counterexample**. No write-high obligation is removed. Next investigate
the correlation between write enable and inactive-branch address don't-cares,
and validate any guarded-command normalization against the actual array
transition semantics. Do not force initialization, assume reset, or declare
unknown address bits harmless without that proof.

The preceding006 diagnostic never invoked an induction solve: the installed
engine skips lengths `<= initsteps`, so `initsteps4/maxsteps4` skipped everything.
Its VCD contains declarations only.007 uses `initsteps3/maxsteps4` and checks the
actual solve marker. Two native-Yosys regression tests cover this boundary. The
[Yosys SAT source](https://raw.githubusercontent.com/YosysHQ/yosys/main/passes/sat/sat.cc)
was checked against the installed engine's log behavior.

## Physical optimization context

The current native Vex report still contains two nearly tied bottlenecks: an
internal path at setup−1.973ns, with6.139ns reported data wire and4.852ns cell
delay; and a fixed reset-input path at−1.957ns with13.023ns data wire and1.916ns
cell delay. `vex_current_wire_overlap.json` records their sources and reported
path overlap. This samples reported paths, not the complete timing graph, and
does not establish a recoverable-gain bound. Both IO and internal timing must
improve; reset timing exceptions remain disallowed.

Recent author preprints were rechecked. [FAPO v2](https://arxiv.org/html/2610.06937v2)
coordinates fanout partitioning, cut changes and recovery, but its critical-cover
idea is already implemented here. Existing negative/no-candidate results must
not be relabeled as a new optimization. [Local physical remapping](https://arxiv.org/abs/2610.01918)
supports validating multiple structural alternatives after legalization and
routing. [TD-Placer](https://arxiv.org/abs/2512.00038) motivates global timing and
load interactions. These sources do not prove20% gains on Gowin; publication in
a top venue was not established by this review. No external chip database was
used. Gemini052 exited1 without a report; no advice or private logs were read.

## Evidence and handoff

Results are under `results/padding_projection_proof_trial_032`.65 focused tests
passed before adding the two direct-induction parameter regressions; final test
counts and hashes are in `completion_audit.json`. The original3000s attempt
budget remains unchanged. Terminal large proof views are losslessly compacted
with cleanup inventories; direct Yosys readers must materialize exact bytes with
`ownpr_image_storage.copy_original`. Selected packet028 and its76 bound artifacts
are rechecked at closeout. The forthcoming publication manifest describes the
GitHub snapshot and cleanup separately; this file does not claim publication
has already succeeded.
