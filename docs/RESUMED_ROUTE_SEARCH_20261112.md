# Resumed route-policy experiment

User resumed the four-design joint goal after the frozen handoff. External Gemini proof prototypes are not present in this workspace and have not been accepted. The delivered ZIP remains unchanged.

`results/resumed_route_search_trial_010/completion_audit.json` closes a new independent experiment in 437.61 seconds, inside its original 3300-second deadline. It does not resume or reset trial009's budget. Both trial009 candidates are reused with identical packed-netlist/placement hashes, fixed CST/SDC, clock tree/capture and prior-route reference. The only search-policy change is enabling the existing `--guided-corridor` implementation. Every guide edge is remeasured, and final physical checks/native STA remain mandatory.

| Candidate | Previous route | Guided route seconds | Complete checkpoint seconds | Native MHz | Hold ns |
|---|---|---:|---:|---:|---:|
| Iteration1, 404 mapped cells before packing | timeout at 240-second allowance |66.265|145.750|94.739|+0.062|
| Iteration4, 510 mapped cells before packing | timeout at 240-second allowance |82.469|163.390|82.645|+0.143|

Both complete their routed physical/export checks; both fail setup at the unchanged 166.998888 MHz evaluation constraint and regress from the retained TMDS123.865 MHz. Neither is adopted. Vex remains90.380 MHz. No new fair official denominator or four-design improvement is claimed. Eleven guided-route/search-direction/estimator checks pass; no global default policy is changed from this two-candidate test.

For iteration1, uncached native PIP estimates fall from2,653,591 in the unsuccessful prior attempt to356,777 in the successful guided attempt; queried sites fall1794 to353. This supports restricting expensive delay exploration using native-graph guides. It does not establish a universal speedup or a timing optimum.

The mapper's apparently better iteration4 budget score produces worse actual timing than iteration1. The worst reported iteration1 path has8.224ns wire and4.108ns cell increments versus incumbent6.099/3.838ns; these are separately selected worst paths, not identical endpoints. Full reported-path details are retained in `native_path_comparison.json`. The general mapper needs physical load, selector-pin demand, placement and routed feedback in candidate selection; more budget iterations alone are not supported by these measurements.

Next: retain incumbent mapping as an explicit portfolio candidate, evaluate changes using bounded guided routing and native loaded-delay feedback, and address ranking errors across common design features. Fair baselines, Vex unbounded hard-state proof, and broader device-mode/configuration gates remain unfinished. `scripts/audit_route_search_progress.py` is a reusable diagnostic; interrupted/unlogged searches are explicitly excluded from its expansion totals.
