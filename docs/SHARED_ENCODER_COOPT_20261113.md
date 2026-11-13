# Trial027: shared functional encoders and physical input ordering

The four-design 20% objective remains unfinished. Competitive denominators
are still pending the fair-baseline audit. The authoritative outcomes and
selected artifacts are in `results/shared_encoder_joint_trial_027`.

The new continuation packet is `all_designs_reported_fmax_checkpoint.json`.
It selects TMDS005 and the equal-frequency, smaller int2float candidate;
UART and Vex retain their prior selections. Single-design packets are
`tmds_reported_fmax_checkpoint.json` and `int2float_reported_fmax_checkpoint.json`.
Use those for tools expecting exactly one design. The original trial012
packet and all its 58 artifact hashes remain unchanged. The combined old/new
selected-artifact audit checks 90 unique paths.

## Measured TMDS results

All completed trials retain the original pins, electrical constraints,
interface cycles, clock tree and 166.998888 MHz evaluation constraint.
Mapped state/hard-boundary CEC and native physical/export checks pass.
They do not establish full RTL-to-bitstream proof or hardware verification.

| Implementation | Native Fmax MHz | Hold ns | Setup WNS ns | TNS ns | Setup violations |
|---|---:|---:|---:|---:|---:|
| Previous selected implementation | 124.004 | +0.048 | -2.076 | -19.998 | 17 |
| 001: independent ACD, identical encoder sharing | 125.226 | +0.048 | -1.997 | -17.848 | 17 |
| 002: multi-output joint encoding | 125.226 | +0.048 | -1.997 | -17.848 | 17 |
| 003: joint encoding, load-aware mux inputs | 125.403 | +0.048 | -1.986 | -17.670 | 17 |
| 005: exit frontier and complete decomposition fallback | 130.522 | +0.048 | -1.673 | -8.212 | 10 |

The last result is about 5.26% above the previous own implementation. It is
not closure at 166.999 MHz and is not a demonstrated 20% win over a fair vendor
baseline. Native final STA and encoding are vendor operations; packing,
placement and data routing in these attempts are our own.

## Algorithm and evidence

`ownpr_shared_encoder.py` uses the existing exact ACD reference to map a
structural eight-input cone into native LUT4/5/6/7 functions. It normalizes
actual supports, shares identical or complementary encoder functions, checks
all 256 input combinations, then runs complete mapped-boundary CEC. It
preserves state/hard/IO cells and existing state positions. New logic uses
calibrated native mux geometry and per-slot input delays. Dead-logic recovery
preserves observed values, state anchors and complete live wide trees.

`ownpr_multioutput_acd.py` partitions assignments using tuples of output
cofactors, giving compatible outputs one shared class encoding. The first
joint TMDS experiment reduced encoders from 17 to 13 and LUT increase from
41 to 33 without a measured Fmax change. This isolates an area result; no
frequency gain is attributed to sharing alone.

Load-aware input ordering keeps heavily loaded sources out of the repeated
leaf positions where possible. It reduces primary-input load growth, but
this is a proposal heuristic, not an inferred timing law. It must compete
against arrival-oriented ordering using routed timing and hold.

The sink-most antichain was incomplete: a parent could have both a branch
through a selected descendant and another branch escaping it. The exit
frontier now admits the parent for that escaping branch. Further, structural
eight-input cones whose actual function depends on <=7 inputs must be allowed
to collapse directly. A five-input encoder restriction also excluded some
critical eight-input functions; exact encoders up to seven inputs now provide
a fallback. These changes use graph/function properties, not project names.

The aborted candidate004 proposal exposed excessive subset/partition search.
It was intentionally stopped before routing after coverage deficiencies were
identified. Its sources and frontier are preserved. The replacement evaluates
at most 256 groups of 2..4 outputs, using partitions common to their retained
top-three choices. This is bounded exploration, not exhaustive optimal mapping.

The new preparation entry point snapshots its source files at startup and
rejects mid-run source changes. Earlier candidate source snapshots remain
separate and describe their actual variants. The immutable round deadline
applies to retries and the abandoned proposal too.

## Generalization and scope

The same final settings are `--method acd-joint --pin-policy load --frontier
exit --count 16`. Structural analysis finds five Vex, zero UART, one int2float
and 26 TMDS eligible-size macro cones before detailed safety checks. UART is
a structural no-op for this operator, not a failed design or a new timing run.

Vex and int2float candidates pass mapped-boundary CEC and static guards and
remove six and seven LUTs respectively. Their final physical outcomes are
recorded in the round completion audit; area reductions alone are not gains.
All designs retain the old candidate as a fallback when raw new timing is worse.
Fallback and raw results must be reported separately.

The raw Vex candidate finishes in 383.719 s but falls to 75.108 MHz from
90.380 MHz, with hold +0.133 ns, setup -4.223 ns and TNS -624.696 ns. It is
rejected. Its worst path changes to a cache RAM address path: 6 logic levels,
2.384 ns cell and 10.652 ns wire delay, compared with the incumbent's distinct
11-level worst path (4.520 ns cell, 6.139 ns wire). The new path includes data
segments of 3.314 and 2.518 ns. This is not an isolated proof that one particular
pin-order change caused the regression. `vex_regression_diagnosis.json` retains
the paths, boundary loads and individual wire observations. Clock segments in
the arrival trace are separate from the quoted data-path wire total.

The raw int2float candidate finishes in 86.875 s, keeps 167.613 MHz, setup
+0.004 ns and hold +0.002 ns, and removes seven LUTs. It passes the actual
constraint. This is an area improvement at equal reported frequency, not a
frequency win. UART remains unchanged at 197.028 MHz. Only TMDS demonstrates
a frequency improvement in this round; the raw method has a significant Vex
regression, so performance generalization is explicitly not established.

The first int2float preparation correctly rejected a packet lacking a bound
model. `joint_lut_structural_guards_001/int2float_model_002` was then found and
verified against every retained source hash, current placement and native SDC.
The augmented reference packet adds those model hashes without changing the
original packet or constraints. A newly computed arrival graph independently
matches the retained native report within 0.001 ns.

Fresh native timing-state captures and rebuilt setup/hold models for both
new selected implementations pass all finite arrival/required/slack checks.
Maximum setup error is about 0.000151 ns; hold error is below 0.000001 ns.
These captures do not run vendor placement/routing. The new packets bind the
fresh model and native source hashes, rather than reusing stale graph topology.
The portfolio recorder additionally checks exact CEC artifacts, candidate
netlist/layout hashes, native constraints, clock topology, all four timing
class inventories, pulse checks and fixed IO rows. Vex raw regression is
retained explicitly alongside its fallback selection.

This operator still only covers a bounded class of combinational cones. It
does not replace global packing/placement, hard-resource configuration proof,
source equivalence, or the remaining official-baseline audit.

## Research and independent advice

The author manuscript [Enhancing Delay-driven LUT Mapping with Boolean
Decomposition](https://people.eecs.berkeley.edu/~alanmi/publications/2024/tcad24_acd.pdf)
motivates moving late variables into the free set and generating structures
that exploit dedicated connections. Our bounded Python reference and physical
operator do not reproduce the entire published mapper or its performance.

[FAPO v2, October 2026 preprint](https://arxiv.org/abs/2610.06937v2) evaluates
sink assignment, cuts and recovery using final live area and full-network
timing. Its all-critical-path condition is relevant to the observed branch
coverage failure. It is a preprint; its reported VPR gains are not Gowin
performance evidence. [EqMap, ICCAD 2025](https://ieeexplore.ieee.org/abstract/document/11240672)
provides another source of equivalent mapping choices, but reduced LUT count
or unchanged depth does not establish routed timing improvement here.

Gemini's final report is `carbon-advice-shared-encoders-20261113-046.md`.
Its first invocation exited without a report; the terminal process was
confirmed before a new authorized invocation, which completed successfully.
Only the final report was read. Useful suggestions were joint class encoding,
existing-signal reuse and preventing excessive leaf broadcasts. The report's
baseline ratios are not accepted because the fair baselines remain unfrozen.
Its suggested fixed fanout thresholds are hypotheses, not device rules; its
written WNS score also needs a sign correction if minimized. Replication
changes a netlist and still needs CEC, even when described as a physical test.

An exact inventory found no existing signal equal or complementary to the
13 selected encoder functions. Extending the explored shared-variable count
from one to three did not improve the best slot bounds for the principal
TMDS roots. These negative results narrow the next experiment; neither rules
out different encodings, different cuts, multi-level reuse or arithmetic
restructuring.

## Reproduction

Use a fresh output directory and the source snapshots for the desired variant.
`scripts/prepare_large_cut.py` accepts the reference packet, design selector,
method, pin policy, frontier and root limit. Exact physical commands are in
the per-implementation `*_invocation.json` files. The corrected checkpoint
preflight reserves 1.3 GB for routing plus its export child. Preserve the
40 GB global bound. Lossless cleanup inventories record every compacted file
and both compressed and decoded hashes; no selected historical evidence or
delivered archive was deleted.

`tests.log` contains the successful focused regression run. The earlier
`tests_initial.log` records a nonexistent test filename and ran zero tests;
it must not be counted as a pass. The tests cover exact reconstruction,
complements, shared outputs, native leaf geometry, escaping branches, support
reduction, fallback encoders, negative controls and state-preserving cleanup.
