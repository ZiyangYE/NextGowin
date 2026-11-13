# Trial030: independent controller product and proof-construction failures

The four-design performance objective remains unfinished. This round changes
proof infrastructure only. The selected physical packet remains
`results/encoder_physical_feedback_trial_028/all_designs_reported_fmax_checkpoint.json`:
UART197.028, int2float167.613, TMDS137.743 and Vex90.380MHz. These are retained native
reports, not newly accepted competitive ratios. Fair official baselines and full
source/configuration acceptance remain incomplete.

## What was actually wrong

The trial029 matched comparison loses INIT during `equiv_miter`. The existing
exact identity/port/wire-witness transport repairs that loss without changing
undefined source initialization. Four initial DCache write-high failures remain.
`matched_write_cones.json` now demonstrates a second construction problem:
`equiv_make` replaces Gate write-mask fanout with a shared Gold/Gate equivalence
signal. Gold may be undefined while the original Gate enable is initialized0.
Replacing Gate's own enable destroys its address/enable correlation: a masked
undefined address that originally contributes0 can instead contributeX.
This is evidence about the constructed comparison, not proof that the complete
hardware design is equivalent.

The first attempted remedy adds an invertible XOR mask only at output observers.
Its local refinement algebra and unchanged functional fanouts/INIT are tested.
Both raw four-step SAT calls returned success, but a later structural audit of
the matched miter reports49 undriven bits. The matched result is **rejected** by
`observer_acceptance_rejection.json`; it is not a valid accepted certificate.
The attempted induction also times out during `equiv_simple`. The runner now
requires structural checks before accepting a constructed comparison. Mask
definedness and residual internal matching remain additional concerns.

## Independent product result

`experiments/prove_controller_product.py` keeps Gold and Gate independently
connected. It adds sink-only auxiliary observation ports, uses `miter -equiv
-ignore_gold_x`, and never applies `equiv_make` or `equiv_miter`. Required original
outputs and every memory command obligation remain present. No reset assumption,
blanket zero initialization, or assumed defined internal state is introduced.

There are247 proposed auxiliary relations, based on common state/expression
names and direct read-data register pipelines. Names only propose candidates;
each accepted relation participates in the proof. The first construction rejects
two candidates exposing unused undriven alias bits. A recorded driver-inventory
filter excludes exactly these auxiliary candidates; no required check is removed.

`product_controller_003` contains245 auxiliary observers. Both original input
models and the flattened product pass `check -assert`. The four-step check from
original initialization passes with all required and auxiliary assertions. The
independent observer audit additionally verifies unchanged original cells,
fanouts, ports, netnames and INIT on both sides; only the expected sink outputs
are added.

Joint temporal induction fails at lengths1 and2, then reaches length3 and times
out at its180s phase limit. This is **not an unbounded proof**. A separate bounded
one-step induction-only diagnostic produces12 required and25 auxiliary failing
observations. Many Gate values areX in that arbitrary-state trace. It does not
start from the original initialized design and must not be called a reachable
hardware counterexample. The VCD and classified failure list are retained in
`product_induction_diagnostic_004`.

## Next discriminating work

1. Derive and prove Gate definedness, replica consistency and pipeline-state
   relations. Never assume them merely to make induction pass. Refuted auxiliary
   guesses may be removed with evidence; all original IO and memory commands
   remain mandatory.
2. Audit clock/reset structure before considering a clock-edge transition model
   that avoids `clk2fflogic`'s duplicated sampled state. Preserve behavior before
   the first edge and asynchronous behavior; do not infer a single-clock contract
   from the design name.
3. Complete the separate array transition, initialization, exact SDK primitive
   and well-founded composition obligations. The common raw-read oracle
   abstraction alone is not a complete memory/source proof, even if its controller
   induction eventually passes. Production `formal_library()` still rejects SDPB.

Gemini report `carbon-advice-proof-observers-20261113-049.md` recommends independent
products and side-only state invariants. Its warnings are reviewed against the
actual artifacts:49 undriven miter bits are observed; suggested XOR cycles are a
risk, not an observed cycle in the structurally checked source.

## Reproduction and evidence preservation

All results are under `results/memory_match_causality_trial_030`. Retain the
original3000s budget; no timeout or deadline is reset. Focused tests cover INIT
transport, original source-proof behavior, observer algebra, exact nonintrusion,
and real Yosys positive/negative product proofs. The first new audit-test collection
failed from a missing brace; it was fixed before the passing test run.

Terminal observer/product JSON views are losslessly gzip stored with original
decoded hashes in cleanup inventories. Python artifact readers remain transparent;
historical direct Yosys `read_json` scripts need original bytes restored with
`ownpr_image_storage.copy_original`. Byte-identical terminal console copies were
removed only after comparison with retained canonical `.log` files. The delivered
archive, current selected implementation, fixed constraints and baseline evidence
are unchanged. See `completion_audit.json` for final timing, storage, test and
artifact-hash checks.
