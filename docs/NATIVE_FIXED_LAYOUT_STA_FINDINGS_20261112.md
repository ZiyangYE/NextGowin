# Native fixed-layout STA: findings and unresolved coverage

Later trial002 resolves fixed-layout timing recalculation for all four designs; see `docs/NATIVE_INVERTER_AND_SOURCE_PROOF_20261112.md`. The historical failures below remain valid records. No fair official baseline has yet been frozen.

This work advances the fair-reference requirement of the joint four-design goal. It produces no new own-P&R frequency gain and does not freeze a fair official baseline.

## Space and retained evidence

`cleanup/lossless_historical_configuration_space_20261112.json` records lossless compression of superseded historical configuration JSON, saving 1,247,162,198 bytes. Decoded SHA256 values were rechecked; source, constraints, reports and failure conclusions remain. Current loaders read these JSON gzip files transparently; legacy direct readers require restoration. No handoff ZIP or decoded frozen evidence was deleted.

Diagnostic `.pr` files in `results/native_fixed_layout_sta_trial_001` were also compressed losslessly, saving 236,332,843 bytes; restoration instructions and hashes are in `pr_lossless_storage.json`. The raw `.pr` paths are no longer directly readable. This storage operation does not alter historical timing scores.

## A native default that mattered

The bare DLL's option behavior must be measured rather than inferred from IDE defaults. The earlier unqualified scouting runs packed input IOB FFs: UART and int2float resource reports show 19 and 17 I/O FFs. Their worst input paths both have 0.610 ns data delay and -1.927 ns skew; those zero-routing branches cannot acquire fabric detours. Explicitly supplying `-ireg_not_in_iob -ioreg_not_in_iob` while permitting output packing restores UART's official 100 MHz implementation to reported Fmax **136.878 MHz**, hold **+0.016 ns**, with recovery/removal passing. The tightest removal witness is +0.006 ns. Pins, electrical settings, min/max I/O delays and interface cycles are unchanged.

This is a common calibration profile, not a rule forbidding input IOREG forever. A general optimizer should evaluate I/O register placement against actual clock/data and hold budgets. The existing own backend still needs broader input IOREG/clock calibration.

## Why a plausible fixed-layout replay was rejected

`experiments/native_fixed_layout_sta.py` executes official P&R once, retains its in-memory state and attempts to change only timing constraints. It snapshots all native design-net-vector connections, physical instance headers, physical endpoint parent chains, PIO and bank VCCIO. This is explicitly vendor calibration, never own P&R.

The early `uart100_core_inputs_retry_001` appeared to preserve setup/hold envelopes while updating the report clock. A subsequent four-class audit found that all recovery/removal paths disappeared. `async_coverage_reaudit.json` rejects that old result without rewriting its original files or claiming a valid baseline.

The `uart100_stage_isolation` experiment established the following on the retained native state:

| Operation | Original setup/hold witnesses | Original recovery/removal witnesses |
|---|---|---|
| Regenerate report only | Preserved, 0.000 ns error | Preserved, 0.000 ns error |
| Evaluate without rebuilding | Preserved, 0.000 ns error | Preserved, 0.000 ns error |
| `restartTiming` then evaluate | Preserved | Both disappear |

The native analysis inventory changes from 195 paths to 123 while the physical snapshot stays equal. The defect is therefore not established by a merely missing report heading; actual analyzed path count decreases. Keeping the original monitor flags, resetting the corner selector, creating a fresh monitor or rebinding register pins did not resolve the coverage failure. Resetting selector 0 also changes setup delay and is rejected.

Other rejected alternatives:

- Rereading timing data on an already initialized library reports a timing-file read failure.
- Reevaluating without restart retains all four classes but leaves the old clock period in the timing model.
- Reapplying clock constraints without clearing their old objects creates duplicate native clocks.
- Updating the primary clock period and falling edge in place changes the clock summary but leaves cached required times stale. The native report jumps to **305.388 MHz** while required times remain from the 100 MHz run. The period-shift gate rejects this; it is not a performance result.
- Rebuilding the clock relationships over the old graph creates duplicate endpoint checks and is rejected.
- Omitting the `-o` argument still does not restore asynchronous coverage. It does not demonstrate that post-P&R Verilog output was suppressed. `getVlgFlag` must not be interpreted as an output-enable flag; its getter tests the `-pdl` option, and its interpretation needs the relevant call-site evidence.

The final prototype records worker outcomes accurately and verifies that copied input netlist/CST bytes remain unchanged. It is a diagnostic tool with multiple deliberately failing calibration alternatives, not a production replay backend.

## Scope beyond UART

Using the same explicit core-input policy and initial implementation constraint `0.70 * scouting_Fmax`, int2float passed the fixed-layout calibration: reference and final reported Fmax **134.432 MHz**, derived STA constraint **164.544768 MHz**, passing hold, unchanged native physical state, unchanged full-analysis inventory and 26 setup plus 26 hold envelopes. It has no recovery/removal endpoints. Same-period maximum error is zero; changed-period setup/hold errors remain below 0.0011 ns; displayed min/max I/O delays remain zero. Source/RTL equivalence and complete physical/configuration baseline acceptance are separate, still unfinished gates. This is a candidate reference, not a frozen denominator or evidence of a 20% own-flow gain.

TMDS failed the same protocol because the native analyzed-path count changes **73 -> 64**, despite endpoint count staying 124. The new inventory guard prevents accepting that result. UART and TMDS demonstrate why a successful replay on a synchronous benchmark cannot establish general replay correctness. VexRiscv was not newly qualified by this calibration packet.

## Independent consultation and next investigation

Completed reports `carbon-advice-20261112-baseline-clock-19.md` and `carbon-advice-20261112-async-reload-20.md` were reviewed against the actual reports and disassembly. Report 20 identifies a concrete hypothesis: official post-simulation netlist generation restores the fused reset inverter `sender/n87_s2` with no independent physical location. The original timing report directly traverses the fused polarity, while the post-task logical snapshot contains that restored unplaced inverter. Rebuilding timing from the latter may break reset propagation. This is a hypothesis supported by netlist evidence, not yet a proven causal repair.

Next work should isolate the native post-output netlist restoration, or capture/reimport the original routed representation into a clean timing context with complete primitive/IOREG/macro support. Do not guess unverified ABI calls or normalize an unplaced inverter without a functional and physical polarity proof. Keep fixed pins and all real I/O/reset paths. A reliable reference needs all four timing classes, complete endpoint/pulse-width and effective-I/O coverage, source equivalence, unchanged configuration, and self-consistent target-relative clock constraints. Do not freeze the four denominators or compute accepted ratios before those gates pass.

`scripts/ownpr_sta_invariants.py` now provides shared fail-closed envelope validation. Regression tests cover lost asynchronous paths, stale required times after a clock edit, valid period shifts and report expansion preserving every original witness. The prototype additionally requires native path/endpoint/falling-endpoint inventory to remain unchanged. These gates do not replace full endpoint semantics or physical/configuration/equivalence verification.
