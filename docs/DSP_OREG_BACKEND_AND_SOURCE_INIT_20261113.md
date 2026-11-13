# Trial024: source-bound DSP OREG mapping and own-backend support

The joint four-design objective remains active and unfinished. No result from
this round is eligible for frequency promotion or a fair-baseline freeze.
Final run status is recorded in `results/dsp_register_joint_trial_024/completion_audit.json`.
The own incumbents remain UART197.028/int2float167.613/TMDS124.004/Vex90.380MHz.

## Source initialization was the decisive distinction

The existing `dsp_output_register_pack.py` already supports an exclusive,
undefined-initial-state output FF bank. It was reused, not replaced by a
post-mapping INIT waiver. The retained elaboration is byte-identical to the
original own frontend output:
`1da48468c7c85ee8fa25cb5c67ba4ff3bc125fb369e91824a8a952b620e66bce`.
The 132 multiplier pipeline state bits have no source initialization. The
mapped DFFRE default INIT=0 is a later implementation refinement.

A fresh resource inference from that elaboration, before FF technology mapping,
packs all four banks (32/34/34/32 bits) into OREG. Both ordinary and active-low
enable transition proofs pass. The SDK model is unchanged except for the
existing constant-inactive GSR contract; no DSP initial state was added.

The new `prove_dsp_oreg_initial_pair.py` additionally checks the original pair
with arbitrary clock/input behavior, `clk2fflogic`, four SAT frames,
`-set-init-undef`, defined inputs and Gold-only X masking. The undefined-state
positive case passes. A defined-zero source-INIT negative case and an incorrect
enable-polarity negative case both produce proof failures. These are local
primitive checks, not a complete CPU proof.

The physical diagnostic reuses the incumbent combinational implementation and
transplants the identified banks. Its source-to-incumbent-to-candidate
correspondence is **not yet fully proved**, even though the fresh source rebuild
has the local OREG refinement certificate. The candidate manifest explicitly
sets `diagnostic_only=true`, `whole_source_equivalence_proved=false`, and
`frequency_promotion_allowed=false`.

## Real registered-DSP backend data

Independent native binding at two sites confirms that OREG enables actual
CLK[0], CE[0] and RESET[0] nodes. The original timing collector filtered only
A/B-to-DOUT arcs, so it omitted registered timing; that filtering has been
extended while preserving the old bypass capture behavior.

The four actual multiplier geometries, with two-site comparisons, provide one
consistent parameter profile and 128 unique registered arcs:

- 34 CLK[0]-to-DOUT[0..33] C2Q matrices;
- setup and hold matrices for 27 A bits, 18 B bits, CE[0] and RESET[0];
- identical relative pin locations and 248 physical mode fields across sites.

Dynamic-CE and constant-one-CE captures agree. The profile is strictly limited
to OREG=CLK0/CE0/RESET0, other stages bypassed, synchronous reset and the observed
output-bit range. Used CASO/SOA, unmeasured output bits, PREG and different CE
selection are rejected. The second control lane is unselected; serialization
may project only that unused clock, never CLK[0].

`ownpr_registered_dsp.py` binds these observations to the pinned native source,
DLL, fixtures, graph-build results and restored associations. The architecture,
timing endpoints, native edge capture, hard STA audit and propagation model now
understand this restricted profile. Existing bypass/BRAM matrix checks remain
unchanged. The relevant regression suite passes 28 tests; a further six-test
rerun passes after provenance checks were tightened.

## Physical diagnostic and why it did not yet produce a result

The candidate removes 132 fabric FFs and 132 LUTs. Removing packed partners
initially leaves odd-slot FFs without their even-slot partner; the first
configuration check correctly rejects this. `placement_002` moves eighteen
orphaned LUT/FF pairs into the now-free even slot and passes FF, carry, wide-LUT
and hard-site legality checks. Actual selected hard-pin rebinding and new own
clock-tree configuration also pass.

The first data-routing attempt fails its bounded budget. Negotiation reduces
resource overuse from28 to4 to0, but nine sinks repeatedly hit the100001-node
expansion limit. All nine failing nets are driven by FFs among the eighteen
orphan relocations. This is **not evidence of DSP-clock routing failure or LONG
WIRE exhaustion**. It exposes a gap between capacity/configuration legality and
routing feasibility of neighboring FFs after a structural packing change.

A bounded recovery reuses the checked partial trees and increases the generic
search bias from5 to12, retaining root-arrival weight0.03 and guide delay0.05.
It also fails its120-second routing budget. Combined retained route time is
989.280seconds. No export or final STA was reached. Neither budget is reset. The
original round deadline remains3200 seconds. No native STA/Fmax may be claimed
unless complete routing and export actually finish.

The diagnostic uses110MHz for direct engineering comparison with the incumbent;
it is **not** a new joint-policy evaluation. Final competitive evaluations still
require fair-baseline*1.20*1.02 constraints and all admission gates.

The next packing fix should avoid unnecessary orphan relocation: investigate
whether the existing odd-slot single-FF calibration can be safely composed with
the other live LUTs/control groups, and independently verify the combined
configuration. Otherwise legalize the remaining pair using explicit source
escape and sink reachability, with routing feedback. Do not merely raise search
limits indefinitely or relax the current shared-control checks.

## Timing evidence and cross-design scheduling

Current Vex setup slack through the four DSPs is -1.335/-0.163/-0.948/+1.307ns;
the corresponding downstream FF-Q worst slack is +6.964/+3.315/+3.285/+3.424ns.
Global WNS is -1.973ns. Thus the first bank is close enough to critical to justify
the experiment, while post-register paths have headroom. These are measurements
of the incumbent, not predictions of the transformed result.

The exact direct-feedback LUT3(0xCA) inventory finds541 instances in Vex and none
in the other three current netlists. This limited recognizer does not prove that
other kinds of enable/next-state optimization are absent. None of the other
three has a DSP absorption candidate. The minimum-ratio objective therefore
still requires a separate general improvement that benefits TMDS.

TMDS's retained worst full-I/O path has9.903ns data delay:3.653ns cell delay and
6.250ns routing, eleven logic levels, and -2.076ns slack at166.999MHz. The first
two routed connections contribute1.248+1.213ns. The path starts at the fixed
input buffer `ng_pad_6`, not a source register. Useful clock skew or DSP changes
alone cannot remove this combined mapping/routing bottleneck.

## Research and independent review

Gemini043 independently confirmed the source-INIT distinction and recommended
testing CE extraction and movable FF placement as alternatives. The checked
criticality measurements justify this bounded OREG trial, but do not justify
treating it as the solution for all four designs. Its report is
`carbon-advice-dsp-absorption-20261113-043.md`.

The [DAC2025 DSPlacer paper](https://www.cse.cuhk.edu.hk/~byu/papers/C263-DAC2025-DSPlacer.pdf)
combines datapath identification, DSP graph construction, assignment and
cascade legalization for CNN accelerators. The relevant lesson is to reconsider
hard-block placement after changing pipeline boundaries; its CNN results do not
predict this CPU's gain.

[Lakeroad, ASPLOS2024](https://doi.org/10.1145/3620665.3640387) uses primitive HDL
semantics and synthesis templates for configurable hard-block mapping. That
supports our use of the exact SDK and explicit state contracts, rather than
assuming a multiplier model is sufficient for every DSP mode.

[FAPO v2, October2026 preprint](https://arxiv.org/abs/2610.06937) jointly evaluates
sink assignment, cut selection and recovery using final live area and full-network
timing; its critical-graph proposals address all critical paths in a restricted
action family. It is a useful next direction for the TMDS/high-fanout bottleneck,
not a verified top-conference result or an implemented feature here.

The [FPGA2026 official program](https://isfpga.org/past/fpga2026/program/) confirms
RND's region-based net-decomposition routing work. Its linked presentation could
not be read through the web tool, so no detailed algorithm or performance claim
is inferred from the title. No open device database or GPU was introduced.

## Evidence and reproduction

All new evidence is under `results/dsp_register_joint_trial_024`:
`source_rebuild`, `primitive_initial_*`, `registered_profile_audit.json`,
`native_oreg_*`, `placement_002`, `implementation_001`, `recovery_route_001`,
`dsp_criticality.json`, and the final completion ledger.

```powershell
.\runtime_bundle\python\python.exe -m pytest tests/test_registered_dsp_backend.py tests/test_ownpr_hard_timing.py tests/test_inactive_hard_clocks.py tests/test_ownpr_hard_sta.py tests/test_ownpr_hard_placement.py tests/test_hard_arrival_edges.py tests/test_dsp_output_pack.py -q
.\runtime_bundle\python\python.exe scripts/prove_dsp_oreg_initial_pair.py --out results/oreg_initial_new
```

For negative controls add `--negative defined_init` or `--negative wrong_enable`
with a fresh output directory. Native calibration commands and the first own
implementation invocation are retained in their scripts/manifests. The
diagnostic must not be promoted by bypassing its source-proof flag.

Eleven superseded auxiliary JSONs were losslessly compacted, saving892085149
bytes. Their original decoded hashes and scope are recorded in
`cleanup/dsp_register_headroom_lossless_024_20261113.json`. Historical route and
clock readers now use transparent storage helpers. The selected58 source
artifacts and delivered archive were excluded.
