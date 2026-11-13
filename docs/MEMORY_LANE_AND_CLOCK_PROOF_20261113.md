# Trial031: clock abstraction, explicit memory lanes and padding observability

The general synthesis/own-P&R goal remains unfinished. No selected placement,
route, pin, RTL, timing constraint, frequency or fair-baseline status changes in
this round. Selected native reports remain UART197.028, int2float167.613,
TMDS137.743 and Vex90.380MHz. Production source-proof SDPB admission stays disabled.

## Actual clock eligibility and proved state relations

Both abstract controllers have a single positive-edge scalar primary clock with
no clock-as-data use, latch or asynchronous state. Gold has1702 state bits in1702
cells, all without defined INIT; Gate has1780 bits in1338 cells, with1543 defined
INIT bits. `formal_single_clock.py` rejects other profiles. Normalization uses
`dffunmap` and `formalff -clk2ff`, without `-assume`, reset restrictions, invented
INIT or blanket defined-state assumptions. State-cell identities, Q-bit bijection,
ports and all existing defined INIT values are audited after normalization.

The edge model checks combinational observations for arbitrary inputs at every
reachable state, including the original initial state. Between clock edges the
audited controller state holds; no independent clock or asynchronous transition
is discarded. This argument applies to the conditional controller, not to a
completed original-memory composition theorem.

The initial245-observer edge product passes its four-step base and fails induction
lengths1 through4 within the phase budget. Adding45 more common-name relations
also passes the base but does not close induction. These failures are arbitrary
induction states, not proven reachable hardware counterexamples.

`formal_defined_state.py` conservatively proposes closed definedness sets using
three-value propagation. It starts with1543 initialized Gate bits and retains163
after rejecting unsupported dependencies and potential unknown propagation.
These163 bits pass a separate original-initial-state SAT check and one-step
temporal induction. They are proved assertions, never assumptions. The joint
model retains every original IO/memory assertion. A four-step induction trace
with these lemmas exposes an instruction-tag write-data mismatch.

## A real abstraction error: compressed constant lanes

The earlier memory relation assumed each logical word occupied consecutive low
physical lanes. Read-state aliases and write-data cones refute this for both
instruction and data cache tag words. Their logical22-bit words contain an unused
constant bit at position1. The mapped32-bit physical word packs the other21 bits
downward. The declared logical read-word alias leaves the removed bit undriven
and unused; it is not a driven hardware input.

The new generic proposal is:

| Logical lane | Physical lane |
| --- | --- |
| 0 | 0 |
| 1, proposed zero lane | 21, physical zero-on-write padding |
| 2 through21 | 1 through20 |
| Remaining physical padding | 22 through31 |

`formal_memory_lanes.py` derives this from common logical bit coordinates and
direct registered-read provenance. It accounts for vector offsets and ascending
declarations. Compacted-bit proposals require a constant alias or a demonstrably
unused undriven alias, plus spare physical padding whose data is syntactically
zero when its write enable is active. These are proposals, not certificates:
original initialization and all masked write-data/mask/address/clock obligations
remain mandatory. Conflicting, missing, live-undriven or nonbijective mappings
reject. Both cache-tag permutations were found automatically; other banks retain
their identity/slice relations. No project-name branch or RTL patch was added.

`lane_controller_004` rejects the initial literal-constant-only proposal because
the bit is an unused alias. `lane_controller_005` rejects literal-zero padding
matching because the array lowering masks inactive write data withX. Both failed
attempts remain recorded. `lane_controller_006` successfully builds explicit-lane
views and checks the original72192 logical storage-bit and1024 replica initial
correspondences. It does not certify complete memory/source equivalence.

Previous conditional proofs on the consecutive-lane abstraction must not be
promoted to proofs of the original design. Their logs remain evidence of their
specific, now-superseded abstraction. Existing physical frequency evidence is
unaffected by this proof-model correction.

## Latest conditional result and remaining padding issue

`formal_relation_candidates.py` matches public bit coordinates, including partial
vectors. For example, a32-bit source address and a27-bit mapped declaration with
offset5 can now propose corresponding bits5..31. It excludes undriven candidates
and unmatched constant coordinates from these optional auxiliary relations.
Common read-pipeline candidates are rebuilt after the corrected lane mapping.

`lane_edge_product_007` has463 auxiliary observations totaling2569 bits. Structure,
state/INIT audits and the original four-step base pass. Four-step induction still
fails; its retained final trace differs only at the data-cache physical padding
write observation. This is an extra abstraction condition, not an original IO.

`lane_defined_product_008` independently re-proves the163 definedness invariants
for this corrected model. The combined induction reaches length4 and times out
at180s. It is NOT a completed controller certificate, and its timeout does not
establish that its eventual counterexample would equal the007 trace.

`formal_padding_observability.py` then proves a conservative dependency result on
the original Gate array model: each tag bank's unused physical read lanes22..31
reach only20 bits including their read registers, and no original output, memory
input or state clock. Vector registers and bitwise mux/logic preserve bit
dependencies; unsupported operators conservatively connect all inputs to all
outputs. A negative test that connects padding to an output correctly rejects.

No padding assertion has been deleted or relaxed in this round. The next task is
to turn this original-model noninterference certificate into an explicit,
hash-bound compositional projection. That can replace the unnecessarily strong
"unused physical padding always writes known zero" obligation with a proof of
unobservability, while preserving all original IO and logical memory commands.
Only then rerun joint induction. Exact SDK primitive, array-transition,
initialization and well-founded composition obligations still remain afterward.

## Replay and retained evidence

The result root is `results/clock_relation_proof_trial_031`. The original3000s
budget is immutable. Each solver uses a bounded phase budget. `completion_audit.json`
records final tests, hashes, storage and elapsed time; no failed proof is promoted.

Relevant entry points:

- `experiments/build_lane_controller.py`: rebuild the explicit-lane abstraction
  from trial029's retained array pair, preserving source hashes and INIT.
- `experiments/prove_controller_product.py --clock-mode edge`: independent
  source/Gate product, normalization audit, original-base and temporal induction.
- `experiments/prove_product_definedness.py`: reusable replay of the separate
  definedness lemma and unchanged joint goal. Added after the recorded008 run;
  its same proof sequence is retained in008's actual Yosys scripts and logs.
- `scripts/summarize_product_counterexample.py`: classify traces without claiming
  original-initial-state reachability.

For a new replay use a fresh result directory and a new attempt budget; do not
edit the recorded budget or frozen evidence. Source snapshots and actual command
files retain provenance. Some terminal large JSON/RTLIL views are losslessly
gzip-stored. Python hashes/readers are transparent; direct Yosys reads require
`ownpr_image_storage.copy_original` to restore the exact original bytes, after
reserving workspace capacity. `clean -purge` removes unused aliases from new
product views and reduces one JSON view to19.5MB without changing functional
state. Canonical logs remain; only byte-identical console duplicates were removed.

Gemini consultations050 and051 each exited1 without a report. They supplied no
advice used in this round. Their private stdout/stderr logs were not read. This
does not change the active objective or justify declaring it blocked.
