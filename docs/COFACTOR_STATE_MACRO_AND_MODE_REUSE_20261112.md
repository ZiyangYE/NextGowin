# General cofactor mapping and state-aware physical optimization

The four-design goal remains unfinished. Trial005's best native TMDS candidate
is **119.034 MHz**, versus112.368 before trial004 (+5.93%). Vex remains90.380.
Fair official baselines are not frozen, so this is not a competitive20% claim.

Evidence and continuation entry points:

- `results/tmds_cofactor_joint_trial_005/completion_audit.json`
- `results/tmds_cofactor_joint_trial_005/reported_fmax_checkpoint.json`
- `results/tmds_cofactor_joint_trial_005/all_designs_reported_fmax_checkpoint.json`
- Best placement: `post_state_trust_search_012/placement` under the trial.
- Best routes: `post_state_trust_search_012`; native STA/encoder: `post_state_export_012/native`.
- Clock encoding: `joint_macro_checkpoint_010/clock_encoding`. Do not substitute the old pre-state-move clock encoding.
- Fresh complete timing feedback: `post_state_bidir_model_012`.

## What changed

`ownpr_general_wide_recompile.py` reconstructs a private physical mux hierarchy
from its exact Boolean function. Different branches can have different supports
and selectors; reduced support and noncanonical input wiring are accepted.
The previous operator admitted53/74 TMDS macros; the new inventory admits74.
All external unions are at most7, including18 macros with unnecessary connected
variables and one LUT6 hierarchy with a genuine7-variable heterogeneous function.

Gemini report27 identified defects which were independently verified and fixed:
`register_control_map.logic` now recognizes LUT7 mux semantics; late leaf inputs
use actual native per-slot pin delays; constant leaves retain one timed dummy
input. Native slow/max I3 is about0.260ns versus I0 about0.521ns in slot0.
The mapper no longer assumes all LUT pins have equal delay. Constant tying is
allowed only for proven independent pins, with exact INIT reconstruction.

`ownpr_cross_macro_recompile.py` expands cuts across upstream LUT/mux boundaries
and absorbs their function into an existing macro without removing upstream
logic used by other consumers. Global cut truth and full fixed-state CEC guard
the change. Private dedicated links are not exposed as ordinary route endpoints.

Neither mapping experiment improved the TMDS incumbent: the initial generalized
search measured12 candidates, cross-macro search4, and corrected costed search12.
Most critical macro boundaries cannot be absorbed into the current bounded
physical tree. This argues for broader decomposition/arithmetic/mixed packing,
not a claim that exact timing feedback alone solves the architecture.

`ownpr_wide_trust_region.py` permits measured local translation of unshared wide
macros while keeping all registers/clocks fixed. The whole physical hierarchy
and slots translate rigidly to legal vacant type17 CFUs. Full graph timing
weights rank geometry; actual loaded rerouting and complete min/max propagation
decide acceptance. Native STA improves114.202 to115.463MHz, hold+0.048ns.

`prepare_state_macro_trust_region.py` additionally prepares whole-CFU moves that
include registers while preserving the exact packed netlist, register grouping,
INIT, controls, fixed package pins and interface cycles. These candidates use
fresh own clock retargeting, native-derived clock encoding, own routing and final
native STA; old fixed-state timing predictions are not reused for moved state.

## Incremental routing across changed primitive modes

The first state pilot failed before STA: the source route contained an old
selected `OF0 -> DI0` FF terminal which correctly does not exist in the new
configuration. Source-tree validation was incorrectly using the new graph.

`ownpr_incremental_routes.py` now validates source trees in a separate graph
with their calibrated historical terminal selections. The new routing graph is
never extended with obsolete terminals. Frozen trees and retained branches are
independently rejected when any PIP is illegal in the new graph. Canonical RRG
identities, actual PIP connectivity and resource conflicts remain checked.
New negative tests reject invalid source PIPs, stale-mode reuse and mismatched
graph identities. The completed native pilot exercises this fix on real data.

## Complete native experiments

All runs retain the same166.998888MHz provisional evaluation constraint, zero
min/max I/O delays,50ps hold uncertainty, pins and electrical settings. This
constraint derives from an unfrozen official calibration; no fair ratio is used.

| Candidate | Native Fmax MHz | Hold ns | Setup WNS ns | Outcome |
| --- | ---: | ---: | ---: | --- |
| Previous canonical joint mapping |114.202|+0.048|-2.768|Source for this trial|
| Fixed-state local macro move |115.463|+0.048|-2.673|Improved candidate|
| Two state macros, complete data reroute |90.256|+0.314|-5.091|Rejected regression|
| Twelve macros/one FF, incremental data route |117.757|+0.048|-2.504|Improved candidate|
| Local measured move after refreshed state timing |119.034|+0.048|-2.413|Selected native candidate|

The full-reroute control's worst data wire delay increases7.117 to9.232ns;
cell delay3.486 to3.696ns. That witness's capture-clock cell/wire delay stays
0.614/1.222ns. Its regression principally comes from data rerouting, not the
capture clock. The successful joint pilot preserves151 complete source trees,
reroutes54 nets and retains1033 PIPs to462 common sinks across37 changed nets.
It finishes complete own clock/data P&R and export in300.172s.

The final candidate has setup TNS-22.054ns and17 failing setup endpoints at the
evaluation constraint. All66 setup and66 hold paths are displayed; recovery and
removal are not applicable for these synchronous cells. All76 pulse checks pass
with minimum+1.561ns. No rerun at reported Fmax is performed. The constraint has
not closed; broader configuration semantics and hardware are not certified.

Fresh complete native captures validate the moved-state timing model, and another
fresh capture validates the final fixed-state data move. The final2012-node,
2912-arc model reproduces native internal AT/RT/slack within0.000151ns for setup
and0.000000450ns for hold. Independent changed-route reconstruction matches all
finite node transitions and every reported endpoint envelope (report rounding
error below0.000624ns). Models and source snapshots are retained.

## Generalization, provenance and remaining work

General cofactor transforms pass geometry and complete fixed-state CEC on three
macros each in UART/int2float/TMDS/Vex. Cross-macro guards pass three macros each
in Vex/TMDS and find no matching guarded structure in UART/int2float. This is
structural evidence, not cross-design routed performance. Algorithm selection
uses graph/device features, without project-name rules or RTL patches.

Physical pilots preserve the packed netlist byte-for-byte relative to the
trial004 selected netlist. Its upstream CEC proof remains the functional lineage;
inherited placement proof fields are not a newly performed proof. The closeout
independently checks current packed-byte identity and native configuration.

Vex own output already realizes284 pads, including explicit unused ERR IBUFs.
The282-pad gap belongs to the official calibration capture; official unused-pad
defaults/electrical realization still need verification. Unbounded SDPB/MULTALU
proof and complete fair-baseline acceptance remain unfinished. Do not use the
relaxed63.729MHz calibration as a denominator.

Gemini report27 supplies independent architecture alternatives. Report26 exited
without a report and is not advice. Follow-up28 is tracked separately; if still
running at closeout it is future architecture review, not an acceptance gate.
Its unmeasured projected gains are not experimental results.

Closeout passes65 focused tests and finishes at3164.536s under the original
3300-second trial budget, without resetting it. The workspace remains below
40,000,000,000 bytes. A15MB reservation shortfall for the final export was handled
by losslessly compressing the rejected full-reroute FS image, with original hash
verification and a cleanup inventory; no evidence bytes were discarded.

Next work should compare generic arithmetic-preserving synthesis plus mixed-CFU
packing against broader shared-DAG/state-aware remapping, while transporting
good physical trees across legal configuration changes. Apply candidate
algorithms across all four actual routed designs and finish the fair-baseline
proof/physical-I/O gates before claiming the requested minimum20% gain.
