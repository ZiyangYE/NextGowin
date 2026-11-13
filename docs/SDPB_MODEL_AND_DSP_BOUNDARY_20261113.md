# Trial023: SDPB semantics and a concrete DSP register-boundary opportunity

The four-design goal remains unfinished. No frequency was promoted and no fair
official baseline was frozen. The selected own results remain UART 197.028,
int2float 167.613, TMDS 124.004 and VexRiscv 90.380 MHz. This round addresses
the hard-primitive proof gap and identifies a generic synthesis/packing change;
it does not claim a new routed implementation.

## What the actual netlists contain

The official Vex mapped netlist retained in trial021 contains ten SDPBs:
four 8-bit READ_MODE=1 data arrays, four 32-bit READ_MODE=0 tag/register arrays,
and two 16-bit READ_MODE=0 instruction-data arrays. All observed ports are
symmetric, RESET_MODE=SYNC, and all INIT_RAM parameters use SDK defaults.
Trial022 already established ten mapped versus fourteen exact public post-P&R
SDPBs. These are different stages, not contradictory inventories.

All four official MULTALU27X18s use OREG_CLK=CLK0. The HH instance also uses
PREG_CLK=CLK0 and a distinct output-enable selection. Lowering the exact SDK
model for each actual parameter set yields one multiplier plus respectively
two, one, one and one enabled synchronous-register cells. No initialization was
added. The SDK DSP model has global-reset handling but no explicit initial block;
constant-inactive GSR does not justify inventing zero initial values.

By comparison, the selected own Vex packed netlist has four DSPs with all of
AREG/BREG/PREG/OREG bypassed. Their DOUT consumers are 131 LUT3s with INIT=0xCA
and one LUT1 identity, followed by 132 DFFREs. The LUT3s implement feedback hold
muxes, not arithmetic. The new generic analyzer finds one compatible control
group per DSP. All four groups have the same clock and effective enable, with
constant-zero local reset and SDK-default FF initialization.

This is a concrete **candidate** for replacing external hold muxes and FFs with
DSP output registers. It is not yet an admitted rewrite: SDK initial-state
observability, unused/cascade outputs, clock/CE/reset encoding, legal packing,
route legality and final STA must still be proved. No claim is made that all 132
FFs can already be removed or that the resulting Fmax necessarily increases.
The analyzer runs without project-name branches; UART/int2float/TMDS currently
have no matching DSP banks.

Evidence: `results/native_memory_proof_trial_023/inventory/hard_instances.json`,
`dsp_sdk_lowering/result.json`, `own_dsp_boundary_inventory.json`,
`own_dsp_absorption_candidates.json`, `four_design_absorption_inventory.json`.

## SDPB model work and validation

`scripts/ownpr_sdpb_formal.py` generates an experimental word-array model from
the hash-pinned installed SDK parameter declarations. It admits only symmetric
widths 1/2/4/8/16/32, READ_MODE 0/1 and synchronous local reset; unsupported
profiles instantiate an unresolved sentinel and must fail checked hierarchy.
It is deliberately absent from production `formal_library()`.

The model preserves RAM INIT layout and explicit zero output initialization.
For widths 16/32, low ADA bits are byte write enables. RESET clears both read
register stages regardless of CEB/OCE and leaves RAM intact. OCE can advance the
second read register even when CEB is disabled. Simultaneous same-address read
and write use the previous stored word in the SDK simulation contract. GSR is
constant inactive, as in the existing proof contract; inputs must be defined
and stable around the simulated clock events.

The exact SDK's concatenated dynamic write targets initially fail the Yosys
frontend with “incompatible mix of lookahead and non-lookahead IDs”. Nine
validated contiguous concatenations can be rewritten as equivalent indexed
slices. The original unnormalized SDK remains the differential-simulation
reference; this frontend normalization is not a substitute for validation.

Completed checks:

- Exact-SDK differential simulation: twelve width/read-mode profiles and
  77,794 shared stimulus steps. Every 14-bit address is read initially, written,
  then read back; byte masks, independent/coincident clocks, reset, enables and
  block selection are covered. Nonzero INIT parameters are used.
- Six deliberate model errors are all detected: INIT bit flip, byte-mask swap,
  incorrect block selection, nonzero upper output bits, incorrect reset/OCE
  priority, and write-first behavior.
- Symbolic transition obligations for widths 8/16/32 pass in approximately
  0.66 seconds in the final run. They quantify over every 14-bit observed
  storage address and arbitrary defined controls/data. SDK write-bit mappings
  and output-register transitions are extracted from its source, with separate
  compact-model template checks. Initial layout and READ_MODE selection are
  checked as well.
- Eleven regression tests pass, including actual SAT rejection of FF initialization
  and asynchronous-polarity mismatches and unsafe absorption-candidate cases.

**The symbolic checks are local transition obligations, not a completed full-RTL
SEC certificate.** Extraction and complete inductive composition, particularly
source-array replication/splitting and DSP state relationships, remain to be
reviewed and proved. They do not remove the original-pair initial-state SAT gate.

## Failed approaches retained

All failures remain in the trial folder; none is relabeled as an equivalence
counterexample or a successful proof:

1. Monolithic local proof with no retained RAM correspondence timed out at 180 s.
2. Exporting every array word as an HDL alias before memory lowering generated
   4,194,303 intermediate read muxes. Both the basic and memory-optimization
   variants failed their bounded attempts.
3. Adding the state alias only **after** lowering avoids those artificial read
   ports. Nevertheless, the four-step original-pair SAT expanded to 79,811,705
   variables and 230,735,664 clauses and timed out at 180 s. This motivates
   compositional storage relations instead of another blind monolithic run.
4. A full Vex diagnostic using normalized SDK SDPB plus verbatim SDK DSP progressed
   beyond unsupported-hard-cell hierarchy but timed out during flatten/optimization.
   It did not reach a successful full source proof. The production prover still
   rejects unsupported SDPB rather than admitting the experimental model.
5. The first short negative-control run produced no result ledger and is not
   counted as a detected error. The six subsequent complete runs are the accepted
   negative evidence.
6. The first symbolic-kernel invocation rejected the newer Yosys `$check` cell;
   explicit `chformal -lower` fixed the frontend issue. The failed run is retained.

The reusable monolithic runner now adds RAM aliases to lowered JSON without
changing cells and applies a shared local timeout to lowering plus proof. It
remains an expensive diagnostic, not the recommended next algorithm.

## Next implementation sequence

1. Complete the all-address memory relation and its binding to the exact model,
   then compose the source arrays with duplicated and split SDPB implementations.
   Preserve arbitrary legal clock behavior and the original initial-state gate.
2. Calibrate real registered DSP profiles from the official parameter inventory,
   including native clock/CE/reset nodes and timing arcs. Distinguish modeled
   unknown initialization from hardware configuration behavior; do not blanket
   zero either side of the proof.
3. Implement generic hold-mux/FF absorption only for proved compatible banks,
   with fallback for unsupported initialization, fanout or controls. It must
   preserve interface latency, and the resulting own packing/routes must be
   newly generated and checked.
4. Evaluate all four projects under the unchanged joint policy, fixed pins,
   complete I/O timing and fair-baseline rules. This DSP change alone is not
   expected to solve all four designs: their individual constraints still apply.

Gemini independently recommended local storage-transition relations and
compositional memory/DSP matching. Its report is
`carbon-advice-memory-proof-20261113-042.md`; its observations were checked against
the actual SDK and inventories. The [official Arora V DSP guide](https://cdn.gowinsemi.com.cn/UG305E.pdf)
documents the register hierarchy, but was not used as evidence of a power-up
initialization guarantee.

## Reproduction

Run from the workspace root, with new output directories:

```powershell
.\runtime_bundle\python\python.exe scripts/validate_sdpb_word_model.py --output results/sdpb_recheck_new --timeout 120
.\runtime_bundle\python\python.exe scripts/check_sdpb_transition_relation.py --output results/sdpb_transition_new --timeout 60
.\runtime_bundle\python\python.exe scripts/analyze_hard_register_absorption.py --netlist results/vexriscv_wide_mapping_search_002/placement/packed.json --output results/dsp_absorption_new.json
.\runtime_bundle\python\python.exe -m pytest tests/test_hard_register_absorption.py tests/test_official_source_equivalence.py -q
```

For each negative control add `--quick --mutation NAME` to the differential
runner with a fresh output directory and an 80-second timeout; names are
`init_bit`, `byte_mask`, `block_select`, `high_output`, `reset_priority`,
`write_first`. A timeout is not accepted as a detected mutation.

The terminal failed full-Vex hierarchy JSONs were losslessly compacted, saving
131,655,684 bytes. The cleanup inventory records decoded hashes and dependencies:
`cleanup/native_memory_diagnostic_lossless_023_20261113.json`. Use
`ownpr_image_storage.load_json` or `copy_original` when inspecting those files.
The delivered ZIP, frozen evidence and incumbent results were not rewritten.
