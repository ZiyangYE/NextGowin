# Trial028: coupled physical groups and extended I/O observations

The latest selected TMDS result is **137.743 MHz**, hold **+0.048 ns**,
setup WNS **-1.272 ns**, TNS **-6.727 ns**, with ten setup violations at the
unchanged 166.998888 MHz constraint. This is a 5.53% frequency improvement over
trial027's 130.522 MHz, without changing its mapped logic, state or interface
cycles. It is not closure at the evaluation constraint. Fair official baselines
remain unfrozen and the four-design 20% objective remains unfinished.

Use `results/encoder_physical_feedback_trial_028/all_designs_reported_fmax_checkpoint.json`
for the cohort, or `reported_fmax_checkpoint.json` for the single TMDS selection.
Other selected frequencies remain UART197.028/int2float167.613/Vex90.380 MHz.

## Physical search and measured outcomes

| Search | Budgeted candidate limit | Native Fmax MHz | Setup WNS ns | TNS ns |
|---|---:|---:|---:|---:|
| Single wide macro move/remap control | 24 / 2 rounds | 130.522 | -1.673 | -7.967 |
| Connected wide-macro groups | 24 / 2 rounds | 135.807 | -1.375 | -7.449 |
| Groups including private ordinary LUTs | 48 / 3 rounds | **137.743** | -1.272 | -6.727 |
| Radius12 mixed groups | 36 / 3 rounds | Not exported | model -1.2718426 | model -6.7359225 |

The last search did not beat the best measured model result and was screened
out. Do not assign it a native frequency. The mixed-group search also had a
larger candidate budget than the wide-only search: its incremental gain cannot
be attributed solely to ordinary LUT inclusion.

`ownpr_wide_trust_region.py` now offers connected groups of two or three
combinational atoms, using either a rigid group translation or independently
contracted positions. Each wide macro retains its native internal geometry.
Optional ordinary atoms are unchanged unpaired LUTs occupying private CFUs;
protected cells, registers, hard resources and I/O remain fixed. Physical
destination overlap, tile type, slots and dedicated links are checked before
whole changed-net routing. Vacated group sites can be reused atomically.

Weights use the full timing graph. Geometric cost only proposes candidates;
actual native loaded-wire measurements and full setup/hold propagation decide
each transaction. This is not an all-critical-path minimum-cut algorithm or
a calibrated predictor for unmeasured sites. Groups remain bounded and may
miss larger rearrangements or arithmetic/state movement opportunities.

The trust region is anchored to the original reference, not the latest
committed location. This fixes proposals that previously stepped beyond the
six-CFU admission bound after an earlier accepted move. Radius12 is an explicit
search option, not a new device capability or a relaxed physical legality rule.
Default radius6 and the old geometry guards remain intact.

`evaluate_joint_lut_graph.py` now carries the recorded ordinary-group and radius
policy into independent reconstruction. The selected netlist object is exactly
equal to the source object; its serialized JSON bytes differ only in formatting.
The empty CEC-check list in this placement-only case is backed by independent
whole-netlist equality, not a vacuous equivalence claim for changed logic.

## Full timing and physical verification

The selected candidate has a fresh native timing-state capture and independently
built setup/hold model. Comparing this with a graph rebuilt from the actual
routes verifies all 3,876 arc groups, node inventories, active/inactive transition
states and finite arrival/required/slack values in both corners. Maximum errors
are below 0.000002 ns. Independent 25 ps late/early mutations are rejected.
See `tmds_whole_graph_audit.json` and `tmds_model_003`.

Fixed I/O rows, CST/SDC, clock topology, state positions, all four timing-class
path counts and pulse checks match the previous selected implementation. Native
encoding and full STA complete without vendor placement or routing. Full
production bitstream/source proof and hardware validation remain separate gaps.

The first attempt to use the fixed-layout prediction auditor correctly rejected
changed placement. That auditor was not weakened. The appropriate complete-graph
comparison was then used on the independently reconstructed moved-layout model.

## Extended physical I/O vector

Read-only inspection of the pinned DLL independently verifies:

- `iobuf::getPioAttr`, RVA0x23f30, reads shorts at offset0x4c.
- `iobuf::initFusemapAttr`, RVA0x2197c0, initializes exactly81 shorts.
- Logical `Iobuf::getPioValueByAttr`, RVA0x2d14c0, uses the original16 entries
  plus explicit dispatch for enums50,55..59,71,72,77,78. It is not a continuous
  81-entry logical attribute array. Other indices must not be blindly queried.

`native_io_extended_state.py` observes all81 physical entries through both the
typed getter and the raw vector and checks equality. Logical observation is
limited to the26 supported indices. The full physical vector includes fields
such as PADDI/DATAMUX/TRIMUX and UNKNOWN names; observing them does not establish
their electrical or fuse semantics.

After fresh-process single encoding, TMDS before/after comparisons match all
1,863 physical and598 supported logical values across23 ports. The replayed
non-comment bitstream payloads exactly match their respective source exports,
and all legacy first16 values match. The selected candidate's authoritative
receipt is `extended_io_selected_tmds_audit_002.json`.

`native_state_io_probe.py` gathers timing pairs before encoding and extended I/O
afterward in one import. It writes separate payloads for the two observations.
This does not run vendor P&R or program hardware. Standalone extended-I/O probes
remain available for independent comparison.

The first probe failed because ctypes loaded before the isolated worker's DLL
directories were initialized. Lazy loading after native initialization fixed it;
the failed log/source is preserved. An initial pair-audit receipt mislabeled
the capture hash with the last source filename. Comparison values were correct,
but the receipt is superseded by `_002`; regression tests cover both standalone
and combined payload filenames. Never use the superseded receipt for provenance.

This work closes an observation gap for these own-P&R TMDS variants. It does
not verify every field's meaning, all unused package pads, official baseline
vectors, or all I/O fuse bits. In particular it does not resolve Vex A5/C17
unused-pad acceptance or automatically freeze any vendor denominator.

## Generalization and remaining work

The same atom rules identify wide/private-ordinary counts of45/376 for Vex,
4/16 for UART,4/12 for int2float and74/21 for TMDS. This is applicability evidence,
not a four-design performance result. Vex search005 rejected a rebuilt-graph
model as an unvalidated native reference. Search006 uses the existing independent
native model `hard_clock_and_budget_convergence_trial_009/vex_bidir_model_004`;
its bounded routed search improves TNS but not WNS. Final native export status
and values are recorded in the completion audit. The incumbent is retained.

Vex's completed native export confirms90.380MHz, hold+0.133ns, setup-1.973ns,
TNS-274.785ns and341 setup violations. There is no frequency or resource gain,
so this round retains its previous physical selection. The new cohort packet
binds the exact independently validated native model009004 for future search,
replacing only the prior rebuilt-model schema binding. Historical packet bytes
and all physical source artifacts remain unchanged. UART/int2float were not
physically rerun in this round; their prior results are retained explicitly.

Gemini consultation `carbon-advice-physical-feedback-20261113-047.md` supports
testing connected encoder/consumer groups, ordinary LUT coverage and complete
critical-path coverage. Its suggestions are hypotheses. The recommendation to
blindly compare81 logical values is not adopted: the binary getter has a more
restricted logical domain. Complete vectors from different I/O-register modes
also require interpretation, rather than unconditional equality against vendor
implementations with different allowed packing choices.

The ACD/FAPO research reviewed in trial027 remains context for this work; this
operator is an independently implemented physical heuristic, not a reproduction
of an entire published mapper. The next major gaps are broader joint placement
and mapping choices, explicit all-critical-path coverage, hard-boundary/state
opportunities, fair official candidate selection, full source/hard-state proof,
and remaining configuration/unused-pad acceptance.

## Reproduction and resource limits

Search manifests retain source snapshots, source/model/route hashes, proposed
and accepted transactions, actual loaded timing and routing policy. Use the
recorded `--wide-group-move`, optional `--group-ordinary-luts`, and explicit
`--trust-radius` with `ownpr_joint_lut_search.py`; native export uses the retained
fixed pins and constraints. Source snapshots matter when comparing variants.
`tests_final.log` records35 passing focused tests including atomic swap,
state/geometry rejection, explicit-radius bounds, full81-entry comparison,
logical-index restrictions and capture-hash regression controls.

The original3200s round deadline is never reset. Storage reservation failures
were resolved with inventoried lossless compaction of terminal, nonselected
artifacts, preserving decoded hashes. Original selected packets, frozen evidence
and delivered archives remain unchanged. The workspace stays below40GB.
