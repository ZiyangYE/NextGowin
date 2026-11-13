# Route-coupled existing-wide remapping, bounded trial 004

The four-design objective remains unfinished. This trial improves the native
reported TMDS candidate Fmax from 112.368 to **114.202 MHz** (+1.632%). It does
not establish a 20% gain, fair official baselines, performance generalization,
complete bitstream semantics, or hardware operation. Vex remains 90.380 MHz.

Authoritative closeout: `results/tmds_route_coupled_trial_004/completion_audit.json`.
The original 3300-second budget was not reset. Closeout completed at 3026.103 s.
Workspace was below the 40,000,000,000-byte ceiling throughout reserved writes.

## Implemented algorithm

`scripts/ownpr_existing_wide_remap.py` adds a general operator for already placed,
private canonical Shannon macros. It preserves primitive membership, cell
positions, output identities, all registers/hard cells, the clock tree, package
pins and interface cycles. Whole-macro exhaustive truth verification and
whole-design fixed-state CEC guard each accepted structural change. Observed,
kept or externally shared internal values are rejected.

Candidate selection uses a complete setup/hold rise/fall timing DAG, including
competing paths absent from the previous worst-path report. Old-wire estimates
rank proposals only. Each candidate is actually rerouted, changed shared-source
trees are measured with the native delay machinery, and both complete timing
graphs are recomputed. Atomic batches of 3, 6 and 12 macros are supported;
individual predicted gains are never summed as an acceptance shortcut.

The first 16-candidate search accepted two transactions which ultimately
cancelled their netlist changes. Its benefit was therefore route-only; it is
not a synthesis breakthrough. The subsequent 14-candidate batch search measured
all candidates without errors and accepted two transactions containing 12 and
3 individual changes. The final netlist differs in 58 cells across 12 wide
macros, with identical placement. Fixed-state CEC covers 62 state/boundary cells
and 186 observed hard inputs/ports. This proof extends the incumbent logical
behavior; it does not replace the remaining source/physical-mode proof gates.

## Native result and feedback validation

Final own-P&R export is `selected_export_002` inside the trial. Vendor placement
and routing were not executed. Gowin performed final STA and bitstream encoding.
The electrical report and all 23 fixed I/O pins match, route connectivity and
node-conflict checks pass, 38,197 configuration bits are checked, and native
frame CRC/tail integrity checks pass. These checks are not complete bitstream
semantic verification.

The provisional STA constraint is **166.998888 MHz**, calculated uniformly as
136.437 * 1.20 * 1.02. The 136.437 MHz official calibration is **not a frozen fair
baseline**, so no competitive ratio is accepted. All existing zero min/max I/O
budgets and the 0.050 ns hold uncertainty remain unchanged.

Native STA reports Fmax 114.202 MHz, setup WNS **-2.768 ns**, setup TNS
**-24.602 ns**, 17 failing setup endpoints, hold WNS **+0.048 ns**, and no hold
violations. It displays all 66 setup and 66 hold paths; recovery/removal are
not applicable for these synchronous cells. All 76 minimum-pulse checks pass,
with minimum slack +1.561 ns. The evaluation constraint has **not** closed.
No rerun at the reported Fmax was performed, in accordance with user policy.

The initial source graph was calibrated against complete native timing states.
After final export, a fresh independent capture and full native graph build
were performed, rather than trusting the optimizer's stored graph. The candidate
has 2012 vertices and 2912 arc groups. Its fresh graph reproduces native pin
arrival/required/slack values within 0.000151 ns for setup and 0.000000422 ns
for hold. The separately rebuilt prediction reproduces all arc groups and all
finite internal-node transition values; maximum arc error is below 0.0000002 ns.

`scripts/audit_fixed_clock_model_prediction.py` validates this comparison across
the period-only constraint change. It rejects other SDC edits, clock domains,
waveforms, exceptions or changed physical clock boundaries. Setup endpoint
requirements shift by delta-period; the primary IBUF falling-clock seed shifts
by delta-period/2 for the default waveform. The initial comparison correctly
rejected a 1.458 ns mismatch at that clock seed until the physical waveform
relationship was explicitly modeled. No node was silently omitted. Independent
endpoint requirements then match within 0.00000109 ns. Three real negative
controls changing a wire delay, endpoint requirement, or falling-clock seed by
0.025 ns are rejected. All 27 focused tests pass; logs and source snapshots are
in `closeout`.

## Generalization and remaining architectural restriction

The same operator was applied to three macros each in UART, TMDS and VexRiscv.
Native geometry checks and complete fixed-state CEC pass. int2float has no
matching canonical macro and remains unchanged. This is structural evidence
only: the new algorithm has not received complete cross-design routed STA.

Only 53 of TMDS's 74 wide roots fit the current canonical operator. Of the nine
wide macros on the previous critical path, four are excluded by reduced leaf
support or nonuniform lower selectors. A pre-existing broader recofactor helper
admits seven of the 21 excluded macros, but does not solve all of them. A
whole-design state/IO/kept fanin audit found no dead combinational cells, so
adding a redundant dead sweep is not an optimization opportunity here.

The next substantial step should remove these structural restrictions: generic
cofactor decomposition for noncanonical macros, cross-macro cuts with exact
truth/CEC checks, and joint remapping/slot sharing/local placement under measured
loaded routing and complete setup/hold feedback. Ordinary LUT, carry-preserving
and reverse-wide decompositions should compete under the same graph-based
rules. The present result validates feedback accuracy, not an adequate search
space for the requested 20% minimum gain.

## Independent consultation and research

Gemini's independent report is
`carbon-advice-20261112-tmds-routing-cooptimization-25.md`. It proposes three
different paths: reverse-wide/arithmetic/mixed-CFU mapping; full-polarity
trust-region placement with loaded-routing feedback; and atomic multicone
transactions with controlled FF moves. This trial implements part of the third
and establishes the full timing feedback needed to evaluate the others.
The report's printed-zero carry-delay claim is not accepted as physical zero
delay, and its millisecond-query / hourly-throughput estimates are unverified.
The primary research checked in the preceding trial remains documented in
`docs/OFFICIAL_PORTFOLIO_ARRAY_PROOF_AND_PATH_PLACEMENT_20261112.md`; published
ASIC or different-FPGA gains are not claimed as Gowin gains.

Fair-baseline acceptance still needs complete provenance/hold/source and
physical-mode coverage. Vex additionally needs the two unused ERR input pad
realizations resolved, and unbounded SDPB/MULTALU state equivalence. No relaxed
63.729 MHz calibration denominator is used. Frozen historical evidence and
previous qualified checkpoints remain unchanged.
