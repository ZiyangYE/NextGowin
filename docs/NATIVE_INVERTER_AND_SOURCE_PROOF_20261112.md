# Native routed-STA representation repair and source proof

This is vendor calibration, not a new own-P&R performance result. The authoritative own VexRiscv result remains 90.380 MHz. No official denominator is frozen.

## Calibration evidence

Trial `results/native_inverter_sta_trial_002` retains its original start and 3300-second deadline. Each listed case executes official P&R once and recalculates STA on the same retained physical state.

| Design | Native case | Reported Fmax MHz | Source proof case |
|---|---|---:|---|
| UART | uart100_control_proof | 136.878 | uart_source_acceptance |
| int2float | int2float_control_proof | 134.432 | int2float_source_acceptance |
| TMDS | tmds_control_proof | 136.437 | tmds_source_acceptance |
| VexRiscv | vexriscv_recorded_control_detached | 63.729 | Rejected: unsupported SDPB |

All native cases pass four-class envelope guards, exact same-period checks, changed-period arrival/required/slack invariants, and unchanged native Paths/Endpoints/Falling-Endpoints inventories. Sampled physical-instance headers, routed parent nodes, PIO and VCCIO remain unchanged. UART retains 73 recovery and 73 removal endpoints; Vex proves 76 local control identities. Displayed effective I/O delays remain zero. These checks do not replace independent full endpoint/configuration/physical or hardware validation. UART's older local snapshot predates the later explicit Reg INIT getter; repeat with the final common engine before acceptance.

## Cause and guarded repair

`executePnrTask` unconditionally calls `restoreTheMiddleInsInNetlist` at RVA 0x232e22 after cached final STA. A restored explicit INV can coexist with its retained pin-absorption annotation. XORing both counts one NOT twice. The local proof admits only one unplaced inverter recorded before and after, unchanged nonzero root, unchanged Reg control pin/annotation and primitive modes, with the binary identity explicit_NOT(x) == inverted_control(x). Unknown, physical, mixed-fanout, data or clock transitions reject.

Stock `sepInvInst` repairs the first three designs but deletes two real routed Vex LUTs (`n18265_s2`, INIT 00FF; `n20295_s2`, INIT 3333); guards reject it. Skipping repair loses 74 analyzed paths. The narrow `recorded_controls` engine preflights every fanout and uses exported typed Net/Pin mutators to reconnect already-absorbed control pins. No inversion flag, routed LUT, placement or route is changed. Merged INV objects remain proof witnesses. Their obsolete input connections must be detached: leaving those inputs adds two spurious analyzed paths, correctly rejected by the unchanged-inventory guard. The detached-input variant passes all inventories. ABI disassembly is retained in `abi/`.

Gemini reports 21 and 22 were independently checked. Report 22 incorrectly attributes restoration solely to output generation: the aggregate driver's unconditional restore means omitting output does not avoid it. Earlier snapshots require a separately validated staged driver. Its projected 25–45 minute architectural runtimes are unmeasured advice. Current own TMDS has zero ALUs; arithmetic experiments must establish actual representation first.

## Initial-state-aware source proof

`scripts/audit_official_source_equivalence.py` verifies official synthesis/source hashes and restores Verific annotations. It uses hash-pinned installed SDK combinational models and explicit binary mux/FF adapters preserving INIT, clock, enable and control priorities. Global configuration reset must be constant inactive; unknown modules and black boxes reject. `clk2fflogic` retains arbitrary clock transitions.

Induction alone failed a real wrong-INIT counterexample. An observation miter after `equiv_make` also failed to detect it. The corrected procedure first proves four initial steps on the original separate gold/gate pair, then proves matched-observation base conditions and induction. Real SAT tests now reject wrong INIT and wrong asynchronous reset polarity and accept the valid case. Undefined RTL initial state uses undef-aware refinement; explicit INIT remains checked. No extra reset sequence or interface cycles are assumed.

Only the three `*_source_acceptance` cases listed above contain the final initial-state gate, interface/black-box checks and runner snapshot. All earlier `*_source_proof*`, `*_source_base_proof`, and `*_source_initial_and_induction` diagnostics are superseded. Vex SDPB is unsupported and rejected. These proofs cover checked native elaboration to mapped cells, not an independently verified elaborator or physical bitstream equivalence.

## Remaining work

Repeat the final common engine across the cohort and independently audit complete I/O, endpoint/pulse-width and physical/configuration semantics. Validate Vex SDPB semantics. Compare a predeclared common vendor implementation-constraint/options portfolio before freezing competitive references: Vex's relaxed 55.85 MHz implementation constraint and 63.729 MHz calibration Fmax must not be used to manufacture an own-flow advantage. Then resume full synthesis/packing/global-placement/routing experiments, focusing on arithmetic representation, state/control-set and wide-LUT boundaries and per-sink routing, evaluated by the minimum of four verified ratios.

Lossless compression receipts: `cleanup/native_inverter_sta_pr_lossless_20261112.json` and `cleanup/native_inverter_sta_terminal_cache_lossless_20261112.json`. Only terminal non-frozen serialization/presentation caches and superseded failed snapshots were compressed. Original decoded bytes/hashes remain; direct readers must restore gzip artifacts. No handoff ZIP or frozen evidence was deleted. Targeted verification: 29 tests pass; overall goal unfinished.
