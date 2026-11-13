# Trial026: exact eight-input mapping across CFUs

The candidate is **rejected**. TMDS native Fmax falls from 124.004 MHz to
109.560 MHz. Hold remains +0.048 ns, but setup WNS worsens from -2.076 to
-3.139 ns at the unchanged 166.998888 MHz constraint. TNS worsens from
approximately -19.998 to -37.723 ns; there are still 17 setup violations.
No selected implementation, competitive denominator or project objective changes.

## Implemented and checked

`scripts/ownpr_large_cut.py` constructs exact eight-variable truth tables,
decomposes them into calibrated LUT4/5/6/7 cofactors and an ordinary LUT4
selector, shares identical/complementary cofactors and recovers dead logic.
State, hard resources, clock endpoints and fixed I/O remain unchanged.
There is no physical LUT8 backend in this operator; this is not a claim that
the silicon cannot support wider functions.

Native mux child geometry reverses some logical leaf groups. The first
candidate passed boundary CEC but failed static geometry validation. That
failure is retained; the corrected second candidate passes both checks.
The second candidate selects roots 29, 36, 43 and 65, creates seven cofactor
groups, recovers seven dead cells and adds 41 LUTs. The complete mapped
state/hard-boundary CEC passes. This does not establish full RTL-to-bitstream
equivalence or remove the project's existing production coverage gaps.

Own routing completes in 35.250 s. The export retry completes in 145.234 s,
including measured wire delays, clock/data route checks, configuration merge,
native final STA and native bitstream encoding. No vendor placement or routing
was used. All 23 I/O assignments and electrical report rows match. Hold and
the export's other timing checks pass; setup closure and full production
signoff remain false. Do not relabel the generated image as hardware verified.

Evidence is under `results/multicfu_large_cut_trial_026`:

- `candidate_001`: rejected geometry and CEC evidence.
- `candidate_002`: admitted logical/physical proposal, CEC, source snapshots.
- `implementation_002/route`: completed own routes.
- `export_retry_003`: authoritative native timing/configuration evidence.
- `structural_diagnosis.json`: input load counts, sink-most frontier and exact
  disjoint decomposition class counts.
- `bounded_acd_inventory.json`: independently reconstructed functional
  decomposition candidates, not placed/routed implementations.
- `tests.log`, `incumbent_preservation.json`, `completion_audit.json`.

## Why this attempt regressed

The selection policy ranks endpoint coverage before excluding serial roots.
It therefore chooses early ancestors and excludes downstream roots such as
188/233. An antichain is not a guarantee of removing the dominant logic chain.
The native worst-path report changes from 11 to 12 logic levels, from 3.653 to
4.222 ns cell delay and from 6.250 to 6.768 ns wire delay. These are each
implementation's worst paths, not an assertion that their endpoints match.

Some raw input loads increase substantially: bit365 18->54, bit350 20->55,
bit341 18->45 and bit270 26->53. Both logic and routing deteriorate. This
experiment does not isolate fanout as the sole cause: root choice, decomposition,
placement and routes all change together.

Gemini consultation `carbon-advice-large-cuts-20261113-045.md` independently
recommended a downstream frontier, shared functional encoders and explicit
input loading costs. Its frequency/benefit estimates are hypotheses. No such
estimate is counted as a measured gain.

## Next architectural experiment

The existing exact ACD reference can represent shared-variable decompositions.
`scripts/analyze_large_cut_acd.py` examines all 27 retained eight-input roots
with encoder support <=5, <=1 shared variable and root support <=7. It checks
all 256 assignments for each of the best three retained candidates. The
bounded inventory finishes in 6.185 s; this is not exhaustive Boolean
minimization, and its LUT-slot count excludes routing and packing costs.

For TMDS root150 the best searched structure uses two LUT5 encoders and a
LUT5 root (six LUT4 slots). Root188 has a shared-variable structure using
three LUT5 encoders and a LUT7 root (14 slots); roots233/219 have four LUT5
encoders plus a LUT7 root (16 slots). Simple disjoint 4+4 compression alone
does not cover these latter critical roots. These structures have not yet
been physically implemented or timed.

Next: rank sink-most critical frontier choices jointly with encoder sharing,
input loads, root-to-sink geometry and min/max timing. Compare these against
low-fanout intermediate cuts. Reuse the existing ACD implementation rather
than starting another disconnected Boolean prototype. Require full boundary
CEC and actual complete native STA before adopting any result. The general
algorithm must contain no project-name branches or RTL patches.

## Failures, storage and reproduction

An initial checkpoint invocation used a nonexistent SDC and failed before
native stages. The corrected invocation is archived. Its export then failed
the 1 GB storage reservation: the checkpoint's 800 MB reservation was not
enough for that child's preflight. Terminal, non-promoted trial025 outputs
were losslessly compressed with decoded-hash verification and cleanup
inventories. Frozen evidence and the delivered archive remain unchanged.
The export-only retry reused the completed routes; neither routes nor the
original round deadline were restarted.

Reproduce the proposal using `scripts/prepare_large_cut.py` with the retained
trial012 packet, `--case tmds --count 8`, and a fresh output directory. Preserve
the candidate's executed source snapshots when comparing future modifications.
The exact corrected physical/export argument lists are in the invocation JSONs.
Reserve at least the complete export's 1 GB before launching, and preserve
the 40 GB workspace bound. Run `python -m pytest tests/test_large_cut.py
tests/test_shared_ff_packing.py tests/test_acd.py -q`: 18 tests passed.
All 58 incumbent artifact hashes were independently rechecked unchanged.
