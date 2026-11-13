# Clock/data cooptimization and coordinated-copy limits

The four-design objective remains unfinished. Trial007 improves the own TMDS
reported native Fmax from119.034 to123.865MHz (+4.059%), with hold+0.048ns.
This is neither a verified20% gain over a fair official baseline nor closure
at the provisional166.998888MHz evaluation constraint. Fair references remain
unfrozen; own Vex90.380MHz, UART197.028MHz and int2float167.613MHz are unchanged.

## Selected, reproducible state

Use `results/clock_data_cooptimization_trial_007/reported_fmax_checkpoint.json`
and `all_designs_reported_fmax_checkpoint.json`. The latter explicitly separates
historical qualification from unfinished joint-goal acceptance.

- Packed netlist and placement: `clock_data_trust_search_011/placement`.
- Own ordinary routes and clock: `clock_data_trust_search_011`.
- Complete measured wire trees: `clock_data_export_013/wire`.
- Final native STA/encoder: `clock_data_export_retry_014/native`.
- Clock differential encoding: `clock_encoding_003`.
- Fresh validated complete min/max model: `final_bidir_model_019`.
- Main bounded completion audit: `completion_audit.json`.

All paths above are relative to the trial007 folder. CST/SDC/I/O-reference
paths and decoded source hashes are explicit in the checkpoint. The logical
packed netlist, fixed pins, electrical contract, all FF positions and interface
cycles match the119.034MHz source. Vendor placement/routing was not executed.
No open chip database or hardware programming was used.

| Implementation | Native Fmax MHz | Setup WNS ns | Hold WNS ns | Setup TNS ns |
|---|---:|---:|---:|---:|
| Previous own implementation |119.034|-2.413|+0.048|-22.054|
| Actual nonuniform clock-tree alternative |122.940|-2.146|+0.048|-21.201|
| Same clock plus measured wide-macro movement |123.865|-2.085|+0.048|-21.140|

All three rows use the same provisional166.998888MHz STA constraint. The final
candidate has17 setup failures,0 hold failures,66 setup/66 hold endpoint paths,
0 recovery/0 removal paths for its synchronous-reset FFs, and76 pulse-width
checks with minimum+1.561ns. All23 external pins and electrical reports match.
Native encoding and known frame/configuration checks pass; broader configuration
semantics, hardware behavior and full source/hard-mode acceptance are separate
unfinished gates. No Fmax-confirmation rerun was performed.

## Full-clock feedback and independent validation

`ownpr_clock_data_model.py` updates every FF launch and capture boundary in both
native-calibrated rise/fall DAGs. Slow/fast clock-path changes are independently
measured with merged clock loading. Competing data launches and fixed external
output requirements remain in the model. Generated/inverted/multiple clocks,
primary clocks used as timed data, unknown FF parameters and unmodeled hard
launch sources reject. `FullClockSearchModel` additionally rejects asynchronous
FFs: recovery/removal and pulse width still require complete final native STA.
This is a synchronous optimization operator, not an all-design signoff engine.

`full_graph_prediction_010.json` independently compares the real clock change
with freshly captured native timing:2912 matched arc groups in each mode and
every finite node transition. Maximum AT/RT/slack disagreement is below0.000002ns.
`final_prediction_audit_022.json` checks the following data move against another
fresh native model. Its requirements are unchanged, and full-graph errors are
also below0.000002ns. Real25ps late-arc, late-launch and early-launch mutations
are rejected; this does not rely on checking only the worst reported path.

The measured clock search considered512 merged-tree alternatives and accepted
one. A following16-candidate macro search accepted one position change. These
are bounded searches, not proofs of global physical optimality.

## Opportunity bounds and remaining data delay

`ideal_bound_001.json` assumes a common identical slow/fast insertion delta.
Its best hold-ignoring model frequency136.710MHz creates29 hold failures.
`ideal_per_ff_bound_006.json` solves all684 source-to-endpoint setup/hold
difference constraints with independent ideal per-FF shifts. The unrestricted
same-slow/fast-shift result is131.931MHz, reproduced by the full DAG with hold
at zero. It assumes fixed data paths and ignores clock-route realizability,
dispersion, pulse width and physical hold repair. It is NOT an upper bound for
arbitrary independently changing slow/fast clock paths or added repair logic,
and is never an implemented/native Fmax.

The actual final worst witness in `final_data_diagnosis_023.json` has6.099ns
wire delay and3.219ns nonwire delay after its input-buffer seed. Other nearby
endpoints still have about6.25ns wire delay. Several serial wide-macro selectors,
ordinary LUTs and their physical separation remain. This supports prioritizing
data mapping/packing/interconnect work; it does not prove every possible clock
optimization exhausted.

## Coordinated critical-copy experiment

[FAPO v2](https://arxiv.org/html/2610.06937v2), an October2026 author preprint,
motivates coordinating copy actions across all current critical paths and
remeasuring shared upstream loads. It is not established here as a top-venue
publication, and its other-architecture results are not a Gowin prediction.

`ownpr_critical_cover.py` independently implements a minimum unit-vertex cut on
a conservative pin-merged critical graph. It offers atomic exact-copy sets at
several near-critical windows. Rise/fall merging may introduce extra paths:
coverage is conservative, and absence of a finite cut does not prove physical
optimization impossible. Candidates still require actual legal routing, complete
min/max propagation, CEC and native acceptance before frequency recognition.

Retry018 measures2 ordinary-copy candidates, passes fixed-state CEC, and improves
the earlier122.940MHz model by only0.016875ns. It is below the123.865MHz incumbent
and is not promoted or assigned a native Fmax. Selector021, run on the actual
best layout, finds no finite covering set at windows0/0.1/0.3/0.6ns. No new
performance result is inferred from that no-candidate outcome.

`copy_generalization_020` applies the same exact-copy primitive to all four
designs and proves fixed-state combinational equivalence. This checks the copy
primitive, not full critical-cover selection, four routed performance gains,
or Vex's unbounded memory/DSP state theorem.

Gemini consultation31 identified a dependent-copy composition problem and
overconservative clone identity checks. The follow-up creates every copy first,
then reconnects the actual copied critical child independently of name order.
Recursive structural clone checks support exact copied LUT DAGs and reject
changed truth tables. Dedicated mux data/output links stay fixed; only proved
equivalent ordinary-LUT sources may replace external selectors. Original pin
criticalities follow copies, duplicate windows use stable action identities,
actual private-leaf membership replaces a same-tile guess, and isolated extended
FF CFUs are excluded from new-copy placement. Separate follow-up validation
preserves the original closeout/source snapshots rather than rewriting them.

## Retained failures and next architectural step

Export013's optional alias-only prediction API rejected macro movement. Native
retry014 omitted that inapplicable optional API, reused the exact measured wire
trees, and stayed within the original trial budget; independent full-model
validation followed. Cover015 failed audit serialization after its measurements.
Retry018 corrected the schema and completed routing/CEC. Failed directories and
their conclusions remain; neither failed run is claimed as successful export.

Consultations30/31 are retained as root Markdown reports. Their estimates and
some snapshot observations require independent checking. Shared-control FF
pairing already exists for calibrated type17 DFFRE pairs0/1,2/3,4/5 with RESET0
and CE1/matching dynamic CE; it must not be proposed as newly implemented.

The next main direction is a common alternative-choice mapper spanning ALU,
ordinary/wide LUT and FF boundaries, with real chain entry/exit/adapter costs,
slow/fast constraints, pin density and measured physical feedback. Word-level
arithmetic reassociation can supply competing choices. Existing bounded
`gw5a_cut_choices.py` is not that complete heterogeneous mapper. Retain clock
and copy operators as supporting stages. Audit competitive official references
and Vex hard-state/pad gaps in parallel with algorithm development, without
using relaxed calibration frequencies as denominators.

The final compatibility check reproduced a regression introduced by trial006:
`compile_initial` applied the isolated extended-single-FF rule before dispatching
already calibrated shared pairs, rejecting a legal0/1 pair. Shared-site
calibration is now validated first; only sites actually admitted by that exact
shared calibration bypass the isolated-single rule. Existing unsupported
shared controls/last-group profiles remain rejected. This restores backend
compatibility and does not add a new frequency result. See the separate
`shared_ff_compatibility_audit.json` and the original shared-FF tests.

The original3300s budget is preserved. Main closeout completed at2514.191s;
follow-up audit fixes also use that original deadline. Space reservations and
lossless storage inventories preserve original decoded hashes. At closeout the
workspace is approximately39.1GB, below40,000,000,000bytes. Reserve capacity
before any next output; a new1GB export reservation may require further
dependency-checked lossless compaction first.
