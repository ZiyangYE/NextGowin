# Trial025: routed OREG diagnostic and the next large-cut bottleneck

The joint four-design objective remains active and unfinished. This round
completes the physical OREG diagnostic, but produces **no Fmax increase** and
does not complete its full source-correspondence proof. No fair vendor baseline
is frozen or candidate promoted.

## Actual outcome

The candidate removes132 fabric FFs and132 LUTs, preserves sixteen of the
eighteen remaining odd-slot FF/LUT pairs in place, and conservatively relocates
two pairs at one site whose other FF has a reset combination outside the new
calibration. The nine previously failing source nets are all in the preserved
set. There are no package-pin or interface-cycle changes.

Own routing completes with6203 reused nets and140 rerouted nets. Negotiation
goes from21 overused nodes/0 unrouted sinks to0/0. Router worker time is63.875s;
including its wrapper,66.281s. The complete checkpoint-to-clock/data-routing,
native encoding and STA flow takes425.969s.

| Metric at the diagnostic110MHz constraint | Incumbent | OREG diagnostic |
|---|---:|---:|
| Native reported Fmax |90.380MHz|90.380MHz|
| Setup WNS |-1.973ns|-1.973ns|
| Setup TNS |-275.506ns|-254.609ns|
| Setup violated endpoints |341|308|
| Hold WNS |+0.133ns|+0.133ns|
| Hold violated endpoints |0|0|

Thus the transformation improves some paths and resource usage, but not the
limiting path. It does not close110MHz. The experiment is not a new final
joint-policy evaluation: future competitive evaluation still uses a verified
fair baseline times1.20 times1.02.

The independent routed audits check fourteen hard cells and1964 observed hard
timing edges, including1544 required dynamic/output edges. They also match2902
pulse-width checks and5804 clock-wire observations. Native readback verifies
the sixteen retained odd-slot FFs' CLK/CE/RESET/Q nodes and direct paired-data
connections, in addition to218 shared pairs. The full native report covers all
284 I/O ports and the existing four timing classes.

These are physical/backend results. The diagnostic's full
source-to-incumbent-to-transformed-implementation correspondence is still
unproved, as documented in trial024. Native encoder success is not an
independent hardware-functional certificate. Do not use
`checkpoint_handoff_complete` to override `diagnostic_only` or the source-proof
gate. Incumbents remain UART197.028/int2float167.613/TMDS124.004/Vex90.380MHz.

## Why the earlier routing failure was avoidable

Two relevant changes were made together, so their individual performance
contributions are not isolated by this experiment:

1. Removing one FF of a packed pair no longer forces its remaining partner to
   change from Q1/Q3/Q5 to Q0/Q2/Q4 when the exact new calibration admits it.
   Keeping the original source preserves long routed trunks for reuse.
2. The original source routing policy is restored explicitly:
   `search_bias=30`, giving heuristic scale0.03; `root_arrival_weight=1.0`;
   `guided_corridor=true`; and `guide_edge_delay=0.05`. Trial024 incorrectly
   used root-arrival weight0.03 and omitted the corridor switch. The archived
   search code establishes the intended policy; it was not inferred from the
   old manifests that lack those fields.

An independent geometric search on the old moved layout finds directed paths
for all nine failing connections, using133–6139 expansions, with the actual
clock/IOREG reservations. Every returned PIP is checked against the native RRG.
Other data nets are not reserved in this diagnostic and no loaded delay is
claimed. It establishes single-net path existence, not simultaneous routability
or a proof that one policy setting alone caused the earlier failure.

Gemini044 independently identified loss of source-tree reuse and the omitted
guide configuration, and warned against inferring mixed-CFU legality from
isolated FF attributes. Its report is
`carbon-advice-orphan-routing-20261113-044.md`.

## Mixed-CFU odd-slot calibration and admission

`native_orphan_ff_encoding.py` runs twelve independent native cases:
three base control groups, CE=one/dynamic and two sites. Each fixture contains
a shared pair, an independently enabled paired FF in another group and an
unrelated LUT. It encodes the pair, removes the even FF while retaining its LUT,
then removes that LUT too, then restores the original physical associations.

All cases match the predicted final4x19 CLS fields, exact projected bit changes,
paired-data predicates, and full restored non-comment payload including frame
integrity/trailer commands. The completed campaign is independently audited
and bound by admission SHA:
`fe6753150e11105f0483332b2de12611c2c0bcf9cb434b1d4b806d5d88ee1548`.

Scope is deliberately limited to ordinary type17 CFUs, paired DFFREs with
default INIT0, RESET0, positive clock and CE1/dynamic, odd slots1/3/5, and the
admitted mixed ordinary logic. Arithmetic, wide-macro and independent-data
sharing, unsupported reset/types and slot7 are rejected. Candidate preparation
checks each site and falls back to the previous legal placement method when
the new evidence does not cover it. In this case one site has an independently
reset FF, so its two orphans still move.

Failures preserved during implementation:

- The first encoding check used the stale RAM word of an unbound LUT as its
  emitted INIT. Native unused LUTs emit the inactive-fuse pattern corresponding
  to FFFF. That expectation was corrected and the full campaign rerun; the
  failed capture remains intact.
- The first campaign auditor compared raw image hashes, which include changing
  creation-time comments. It was corrected to compare all non-comment payload
  words while still checking each file's raw decoded and compressed hashes.
- The first all-preserved candidate was correctly rejected for the unsupported
  other-group reset. Selective per-site admission fixes this without loosening
  the calibrated scope.

The final regression suite passes34 tests, including wrong INIT/reset/CE,
unsupported geometry, missing admission and an actual native Q-node corruption
negative control. The incumbent four-design static backend guard is separately
recorded; unchanged-design checks are not claimed as new performance gains.

## Next architecture: cuts spanning multiple CFUs

Read-only analysis finds exact eight-input cuts at five eligible Vex wide roots,
one int2float root and twenty-one TMDS roots; none in UART under this particular
bounded recognizer. Each truth table and every one-variable cofactor are
exhaustively reassembled over256 assignments. This is a logical analysis, not
a physical candidate or complete enumeration of all optimization opportunities.

Several TMDS roots on the current worst path (`ng_coopt_150`, `ng_coopt_188`,
`ng_coopt_79`) have eight-input functions and -2.076ns through-slack. The existing
cut search is limited to seven external variables. More importantly, these
functions fail the tested depth6/7 Shannon-tree templates and fit depth8.
Simply increasing the cut-enumeration limit is therefore insufficient. This
does not prove a global minimum over all Boolean decomposition families.

The next implementation should compare a general larger-cut realization using
two independently legal LUT7 trees and an ordinary final mux, or another
verified decomposition, against the existing staged network. It must select
input roles using all critical-path budgets, place the new groups jointly,
retain shared upstream logic where still needed, perform full boundary CEC,
and measure the routed setup/hold result. It must not assume that physical
cross-CFU LUT8 hardware is already supported. The existing direct LUT8 macro
backend explicitly rejects that geometry.

This direction addresses TMDS and potentially critical Vex cones with one
general rule. It preserves the joint objective and does not treat the OREG
resource reduction as success on the four-design frequency target.

## Reproduction and evidence

Evidence is under `results/dsp_orphan_routing_trial_025`: `campaign.json`,
`admission.json`, native `encoding_*_v2` folders, `placement_002`,
`implementation_001`, `orphan_binding_audit.json`,
`registered_full_sta_audit.json`, `geometric_reachability.json`,
`eight_input_macro_inventory.json`, `large_cut_tree_feasibility.json` and the
completion ledger. The implementation manifest retains exact commands and
source/constraint hashes.

```powershell
.\runtime_bundle\python\python.exe -m pytest tests/test_orphan_ff_packing.py tests/test_shared_ff_packing.py tests/test_ownpr_ff_configuration.py tests/test_registered_dsp_backend.py -q
.\runtime_bundle\python\python.exe scripts/analyze_eight_input_macros.py --packet results/joint_wide_move_map_trial_012/all_designs_reported_fmax_checkpoint.json --out results/eight_input_analysis_new.json
```

Use fresh output paths for native calibration or physical replay. Preserve the
source-proof restriction even if replay completes. No vendor placement/routing,
open chip database or hardware programming was used in this experiment.
