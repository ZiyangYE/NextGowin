# Arithmetic boundaries, fixed-state repacking and source configuration

Trial006 makes no frequency improvement. TMDS remains119.034MHz and Vex90.380.
The four-design >=20% objective and fair official baseline acceptance remain
unfinished. `results/architecture_arithmetic_joint_trial_006/completion_audit.json`
records the original3300-second budget, terminal failures, scope checks and43
passing targeted tests. Closeout finishes at3226.336s (53min46s). No deadline
was reset.

## Independent source-mode verification

Gemini28 correctly identified that old selected FF terminal PIPs must be derived
from actual source placement, not inferred from the route being validated.
`ownpr_source_configuration.py` snapshots/hash-binds packed and placed source
bytes plus nets/routes. The router always constructs source terminal modes from
that configuration, including when the target happens to accept every old PIP.
The source nets must also match independently derived `bind()`/clock-partition
endpoints. Obsolete source modes are never enabled in new routing.

Re-audits confirm the valid119.034MHz ancestry:2626->2633->2637 PIPs,205 nets.
The final re-audit additionally rejects swapped logical-net names even though
their renamed physical PIP trees still pass connectivity. New tests reject a
forged old terminal enabled only in the target, changed staged route inputs,
wrong source placement and changed snapshots. Historical results are unchanged.

## Fixed register sites across different synthesis representations

The existing paired-FF native capture verifies1024 single-FF cases spanning all
eight slots and supported default-INIT/control/clock profiles. The initial packer
had restricted anchors to slots0/2/4. New explicitly gated type17 single-FF
anchors retain the38 exact register sites and old clock tree while replacing
the complete combinational representation; anchor CEC passes all186 observed
hard inputs/ports.

Gemini29 identified an important calibration-scope gap: a single extended-slot
FF was being packed with additional ordinary LUTs in its CFU. Initial native
pilot STA completed, but these pilots are not promoted as physically qualified.
The new guard requires an otherwise isolated CFU for ordinary extended-slot FFs;
pre-existing separately calibrated wide-FF macros retain their original scope.
The analytical placer reserves these CFUs. A repair operator evacuates only
unpaired sibling LUTs without moving registers; three LUTs moved in the final
prepared candidate. Runtime scope re-audits reject both older pilot layouts and
accept the isolated candidate.

## Architectural experiments and delay findings

The original own pre-remap graph has92 mode2 ALUs. Generic SUM-consuming-chain
preservation with COUT-only softening retains52 ALUs. LUT4 mapping yields129 LUTs,
and register-data adaptation adds20. Mixed packing places24 unpaired LUTs into
the already calibrated carry-CFU slots6/7. Complete own P&R and native STA report
54.163MHz, setup-12.475ns/TNS-168.039ns, hold-0.196ns with one violation at the
unchanged166.998888MHz constraint. It is rejected.

The worst path has about11.071ns cell delay and9.171ns wire delay. ALU cell arcs
alone contribute3.917ns; normal LUT arcs and several short arithmetic-chain
entry/exit costs accumulate in series. The native library/report checks match,
so this is not evidence of a bad ALU timing model. Resource reduction does not
imply reduced critical-path latency. The negative hold path arrives too early
after a wide input function becomes a fast ordinary LUT at the same FF site.

A second generic variant first removes23 idempotent repeated carry stages, then
softens all remaining arithmetic and maps to ordinary LUT4:175 LUTs before
packing,195 after adapters. Native P&R/STA completes in205.625s, reporting
74.901MHz, setup-7.363ns/TNS-98.087ns, hold-0.194ns. It is also rejected.

The third variant uses actual source-matched native FF/IO max rise/fall arrivals
and min active setup requirements in the Boolean mapper, replacing the previous
uniform zero arrivals/5000ps requirements. Known inactive control transitions
retain their explicit native sentinel; no missing endpoint receives an invented
budget. Unknown boundary primitives or uncovered requirements fail closed.
The generated wide/pin-aware mapping and isolated fixed-FF placement pass CEC,
but own routing times out at210s and no native Fmax is claimed. The timeout and
partial routing are retained; it is not silently retried with a new deadline.

The mapper already supports nonzero timing annotations, but preserving ALUs as
independent zero-arrival cuts hides their true incoming latency. The next major
step is a joint arithmetic/ordinary/wide-LUT choice representation with native
chain entry/exit and adapter costs, actual state-boundary timing, and measured
physical feedback. This trial does not implement a complete such choice graph.

## Research, consultation and limits

[EqMap, ICCAD2025](https://www.csl.cornell.edu/~zhiruz/pdfs/eqmap-iccad2025.pdf)
motivates retaining equivalent mapping alternatives instead of committing to one
structural form. [FuseMap author preprint, MLCAD2025](https://arxiv.org/abs/2507.10912)
combines ASIC and LUT mapping choices; it is not this implementation.
[Double Duty, accepted FPL2025](https://arxiv.org/abs/2507.11709) changes a
Stratix-like architecture for concurrent LUT/adder use; its area results do not
apply to Gowin. Our actual sharing scope remains native-calibrated slots6/7.
[FPL2025 interconnection-aware resynthesis](https://biblio.ugent.be/publication/01KMQW5B5CPZEDJMGYKMX4F8E6)
provides preliminary locality/duplication motivation; accessible abstract
evidence does not supply a Gowin frequency guarantee.

Completed consultations28 and29 are separate root Markdown reports. Their
chain-length thresholds, throughput estimates and proposed simultaneous
four-design runs are hypotheses, not acceptance criteria or measured results.
Other suggested static-node/alias-expansion/fallback risks remain to be checked.

All existing pins, electrical constraints, interface cycles, full I/O budgets
and hold uncertainty are preserved. No vendor P&R result is claimed as own P&R.
No open device database or automatic hardware programming was used. Failed
images and duplicate configuration snapshots were stored losslessly with
original hashes and cleanup inventories; canonical reference targets are
explicit dependencies. The workspace stayed below40,000,000,000 bytes.
