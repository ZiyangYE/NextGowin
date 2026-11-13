# Common official portfolio, array proof and global path-placement trial

Trial: `results/official_baseline_contract_trial_003`. Its original start/deadline remain in `budget.json`; closeout is in `completion_audit.json`. The full four-design >=20% goal remains unfinished. No new winning own result or frozen official reference is claimed.

## Common vendor experiment and acceptance boundaries

The same source-matched official mapped netlists, fixed CST, full declared I/O budgets, core-input policy and recorded-control restoration were tested at 100/150/200 MHz implementation constraints. Each candidate received same-period and 1.224*its-reported-Fmax fixed-layout STA with physical/inventory guards. A common 50/75 MHz extension was then declared and applied to all four designs because the first portfolio had no hold-clean Vex candidate. This is an explicitly adaptive common extension, not an originally predeclared five-point experiment or a project-specific policy. Completed cases were not repeated; one pre-case storage guard required dependency-checked cleanup and exact-deadline resume.

| Design | Highest timing-eligible candidate Fmax MHz | Eligible implementation constraints MHz |
|---|---:|---|
| UART | 136.878 | 50,75,100 |
| int2float | 134.432 | 50,75,100 |
| TMDS | 136.437 | 50,75,100 |
| VexRiscv | 63.729 | See the final aggregate audit; higher-constraint cases retain hold failures |

These are candidate reports, not accepted denominators. The first three have checked elaboration-to-mapped initial-state/inductive proofs from trial002. Vex still lacks a complete source-to-physical hard-resource/state proof. Endpoint/pulse and physical/configuration semantics remain separate gates. Do not claim a 20% result from the Vex low-constraint candidate. All failed candidates remain recorded.

`complete_named_io_contracts.json` independently checks distinct external names, exact pin assignments and both zero min/max bounds, including aliases/constants: UART24 pins, int2float19, TMDS23 and Vex284. Treating bit IDs as unique ports would silently merge tied/aliased outputs; new negative tests reject that omission. This check does not replace native endpoint or electrical realization coverage. Vex native capture has282 I/O instances; the two other inputs, `iBusWishbone_ERR` and `dBusWishbone_ERR`, are unconsumed in the flattened functional model. Their removal is not a waiver of the physical interface contract: retain the explicit pins/budgets and independently resolve pad/default electrical realization before freezing a full-I/O reference.

## Explicit RAM arrays and SDK validation

`scripts/gw5a_sdpb_array_model.py` supports equal read/write widths1,2,4,8,16,32, both read modes and SYNC/ASYNC output resets, with a separate inactive-GSR requirement. It preserves all memory state, initialization, aligned addresses, byte enables and output registers; unsupported parameters reject. It does not turn RAM into an opaque or free-output black box.

The final differential simulation packet is `sdpb_array_sdk_validation_final`: 24 configurations,4096 stimulus steps, independent/coincident clocks, CE/BLKSEL/byte enables/reset, compared against verbatim installed hash-pinned SDK SDPB. Six `sdpb_negative2_*` mutations (address,clock,INIT,read mode,reset mode,byte enable) each produce an observable mismatch and reject. Exhaustive address/lane arithmetic covers1,032,192 pairs. These are model validation and address-identity evidence, not unbounded whole-design equivalence.

`vex_memory_model_003` builds explicit source and mapped SMT models without memory_map: 8 RTL arrays and10 physical arrays, with the installed actual MULTALU27X18 model including its registered behavior. SMT output is106,862,648 bytes. The differing counts include split-width storage and replicated read ports; they cannot be paired by count or name alone. Array state correspondence, defined/undefined initial-state refinement and inductive coupling still need proof. No SMT theorem is claimed.

Icarus is invoked with explicit local compiler prefix (`-B.../lib/ivl`) and VPI paths (`-M- -M.../lib/ivl`). This avoids the observed Windows startup failures without modifying binaries or machine settings. The Icarus implementation's default-path calculation calls GetShortPathName and searches for backslashes without checking that call's success; explicit paths bypass it: https://raw.githubusercontent.com/steveicarus/iverilog/master/vvp/vpi_modules.cc .

Gemini consultation23 exited without the required report and was not counted as advice. Completed consultation24, `carbon-advice-20261112-sdpb-decomposition-24.md`, independently recommends shell/core separation with symbolic-address induction or SMT array coupling, and explicit wrong-address/clock/INIT/pipeline/reset negative controls. These suggestions are not verification certificates.

## Rejected whole-design path-placement experiment

The optional `--path-weighted` operator in `scripts/ownpr_global_relayout.py` accumulates complete near-critical path demand without dividing it by fanout, reduces initial-position anchoring, and then legalizes whole ordinary/carry/wide units. It uses graph features and common parameters, with no project-name branch. The original default behavior remains. The implementation also handles layouts with no hard_clusters key.

TMDS `tmds_path_global_003` moved152 of178 units, retained byte-identical logical netlist and fixed pins, and reduced the surrogate weighted wirelength from16575.93 to10444.41 after legalization. `tmds_path_checkpoint_001` completed fresh own clock/data routing, connectivity/configuration export, Gowin STA and encoding in311.172 seconds at the provisional166.998888 MHz constraint.

**Reject this candidate:** native Fmax84.842 MHz, hold+0.290 ns with0 hold violations, setup WNS-5.799 ns/TNS-71.092 ns and16 setup violations. It is below the incumbent112.368 MHz and does not replace it. These are reported Fmax values; no Fmax rerun was issued. Hardware/full-bitstream functional verification is not claimed.

The critical witness changed from pad ng_pad_6 to ng_pad_8. The old witness had12 logic levels,4.138 ns cell and6.607 ns route delay; the new one has11 levels,3.166 ns cell and10.475 ns route delay. Capture-clock skew stayed nearly unchanged (1.837 versus1.843 ns). Thus this regression is principally increased routed data delay and competing-path change, not loss of useful clock skew. Fewer levels and smaller global line cost were misleading predictors. Next optimization must use full pin-level competing-arrival feedback and loaded routing/congestion information, with min/max transaction acceptance, rather than promote Manhattan-cost reductions.

Research checked in this trial: E-Layout's author version identifies ICCAD2026 and proposes joint restructuring/physical decisions across disjoint subcircuits under design-level timing feedback (https://arxiv.org/html/2610.26432v1); its ASIC results do not establish Gowin Fmax gains. TD-Placer describes graph-aware delay prediction, quadratic placement and finer timing-contention weights (https://arxiv.org/abs/2512.00038); its FPGA architecture and reported improvements cannot be transplanted as our expected result. The present optional operator is an independent experiment, not either paper's implementation.

## Storage and next work

Cleanup inventories preserve source/configuration/reports/outcomes/hashes while deleting only checked, superseded, non-frozen PR serialization caches: `cleanup/obsolete_failed_trial001_pr_removal_20261112.json`, `superseded_trial002_failed_pr_removal_20261112.json`, `nonselected_low_portfolio_pr_removal_20261112.json`, and `superseded_scout_and_calibration_pr_removal_20261112.json`. Those removed cache bytes are no longer directly verifiable. Current candidate caches, frozen own evidence and delivered handoff ZIP remain. The 40,000,000,000-byte limit was enforced before outputs, including the storage guard that stopped a pending case.

Remaining: prove hard-resource/array state and initialization correspondence, independently finish full native I/O/endpoint/configuration acceptance, freeze competitive common references, and obtain all four >=20% own-flow gains. Prioritize the currently weak TMDS ratio while applying one general algorithm and checking the other designs. Own Vex remains90.380 MHz; own TMDS remains112.368 MHz.
