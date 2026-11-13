# Across-ALU mapping and joint register-packing trial

Trial008 is complete as a bounded experiment, without an adopted frequency
gain. The four-design goal remains unfinished. Keep the selected own results:
UART197.028MHz, int2float167.613MHz, TMDS123.865MHz and Vex90.380MHz. None of
the still-unfrozen official calibration candidates is promoted to a fair
competitive denominator by this trial.

## Implemented general operators

`scripts/ownpr_heterogeneous_timing.py` places retained mode2 ALUs and ordinary/
wide LUTs in one rise/fall late/early DAG. State, I/O and hard internal native
timing contexts remain boundaries; retained ALUs do not receive invented zero
arrival times. The model includes native carry entry/exit cell arcs, the
backend's dynamic-head/terminal bridge costs and FF identity-adapter costs.
External wires remain an explicit0.672ns slow/0.25ns fast development prior.
Consequently its scores are mapping heuristics, never routed STA or Fmax.

Fixed primitive/parameter/port and clock-driver identity checks precede budget
transport. The experiment additionally proves the original source against the
selected mapped reference across every named hard/state input boundary, then
proves each actual resulting JSON against the original source. Native endpoint
requirements stay unchanged. Only the mapper's internal requirements may be
uniformly shifted to make its current optimization problem feasible. Active
requirement transitions are propagated explicitly: a native inactive1e10
sentinel shifted by reverse wire propagation is not mistaken for a new timing
constraint or silently replaced by zero.

`experiments/run_heterogeneous_mapping.py` explores retained-chain sets by
actual mapped covers, performs bounded across-box budget updates, and retains
the best measured proxy iteration instead of always accepting the last pass.
There are no design-name conditions or special RTL edits. In the24-state
mapped beam, relatively good alternatives retain one8-stage chain; the earlier
raw-function proxy preferred retaining60 or65 ALUs. This is evidence that raw
chain estimates are insufficient for choosing the final mapped cover.

`scripts/ownpr_hold_aware_pins.py` evaluates exact LUT4 pin permutations against
both complete proxy DAGs, with no global proxy setup-WNS degradation. It retains
existing identity LUT1 adapters. Final selected cases accepted zero permutations;
do not attribute the resulting frequency or hold behavior to a pin change.

`scripts/pack_joint_register_boundaries.py` automatically considers calibrated
wide-root/FF identity absorption and existing shared-control pairs. In the
chosen case it removes9 identity LUTs, moves the corresponding FFs into native
wide macros, proves the transformed logic, replans their actual clocks, and
completes own data routing. It selects no shared FF pairs. Some inherited
placement-manifest counts describe the preceding placement; the completion
audit records the independently counted post-pack inventory explicitly.

## Full own-P&R and native results

| Candidate | Reported MHz | Setup WNS ns | Hold WNS ns | Decision |
|---|---:|---:|---:|---|
| Existing selected implementation |123.865|-2.085|+0.048|Keep|
| New mixed mapping, fixed FF sites |90.449|-5.068|+0.131|Reject regression|
| New mapping plus9 wide-root FF absorptions |91.166|-4.981|-0.022|Reject hold failure and regression|

All use the same fixed pins, electrical constraints, cycles and provisional
166.998888MHz STA contract. Each native run retains23 I/O ports,66 setup and66
hold endpoint paths,0 recovery/removal paths for this synchronous-reset design,
and76 pulse-width checks. Gowin supplies final STA/encoding only; vendor P&R,
open chip databases and hardware programming are not used. Reported91.166MHz
is raw information and is NOT accepted under the hold-gated frequency policy.
No Fmax-confirmation rerun was performed.

`native_path_comparison_012.json` compares complete native worst-path witnesses
with the same parser. Including the input-buffer arc, the selected baseline has
3.838ns cell and6.099ns wire delay. New fixed-state mapping has5.789ns cell and
7.901ns wire delay. Wide-FF absorption reduces those to5.274ns and7.561ns but
introduces a hold failure. These are different worst paths, not a same-endpoint
causal experiment. Both mapping depth/pin costs and routed interconnect worsen;
adapter removal alone does not close the gap.

## Evidence and reproducibility

All paths below are relative to `results/heterogeneous_choice_joint_trial_008`:

- `tmds_mapped_beam_004`:24 actual mapped retention states and their proxies.
- `mapped_retention_selection.json`:source-hash-pinned follow-up states.
- `tmds_bridge_pin_retry_006`:final mapping, across-box budgets and source CEC.
- `tmds_joint_place_007` and `tmds_joint_checkpoint_008`:fixed-state own P&R.
- `tmds_joint_register_place_009`:proved9-buffer absorption and exact profiles.
- `tmds_joint_register_checkpoint_010`:new own clock/data routes; export stopped
  at the storage preflight, before writing its export directory.
- `tmds_joint_register_export_retry_011`:complete export/STA using those routes,
  after lossless compaction and under the same original attempt deadline.
- `closeout_sources`, `source_archive_audit_013.json`:current complete sources
  and a byte-exact candidate006 timing-engine snapshot, restored only after its
  SHA256 matched the originally recorded hash.
- `completion_audit.json`:test log, rejection decisions, inventory, provenance,
  original deadline and pending requirements. The combined checkpoint keeps
  the earlier selected implementations and marks this algorithm unadopted.

Earlier failures are retained:001 used a relative ABC path;002 used the wrong
native LUT6 mux slot;005 rejected a reverse-propagated inactive sentinel. Their
source hashes/configurations and conclusions remain. Not every transient
prototype source version has retained bytes; those versions are not claimed
to be independently replayable. Subsequent runs now snapshot their sources at
entry. Final native inputs, source proofs and current code are retained.

Lossless FS/JSON storage receipts in `cleanup/trial008_*` retain original decoded
bytes/hashes. Raw canonical native partial configurations remain available.
Legacy direct readers must materialize archived inputs first with reserved
space. The delivered handoff ZIP and frozen evidence remain unchanged.

## Generalization limits and next work

The complete new mapping/P&R experiment was applied to TMDS. UART/int2float
received no new routed-performance application in this trial. Vector hard pins
are scalarized distinctly, but a Vex self-model admission test rejects undriven
DSP clock bit284. Its unused-clock semantics require independent native/mode
evidence; the operator does not invent a zero clock or hide that failure.
Vex's existing implementation remains usable at90.380MHz; this new mapper is
not yet a verified four-design flow. Unbounded RAM/DSP source proof and unused
official ERR-pad/default electrical matching also remain open.

Gemini consultation32 is retained in the root report. Its deadline-clamping
explanation is a hypothesis, not established here by inspection of ABC's
implementation. The trial demonstrates different area/timing outcomes under
internal budget updates, not proof of that proposed mechanism.

The current two updates are not proven converged: the selected mapping's proxy
setup WNS improves from-7.799ns to-5.885ns between passes. Next compare further
bounded feedback updates and an area/timing portfolio against the protected
incumbent, with FF-packing-aware costs and loaded-routing feedback. Neither
more preserved ALUs nor more aggressive adapter absorption is by itself a
successful general solution. Finish the fair baseline audit and require all
four individual>=1.20 ratios before declaring the goal complete.
