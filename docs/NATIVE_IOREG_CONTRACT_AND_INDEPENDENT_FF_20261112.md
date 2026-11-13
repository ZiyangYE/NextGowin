# Native output registers and independent FF input observations

Trial016 continues the fair-baseline audit; it introduces no new performance claim. The main four-design objective remains active and unfinished. `results/native_ioreg_contract_trial_016/completion_audit.json` records the original 3300-second deadline and closure. The official implementation is used only for calibration.

`--capture-ioreg` captures native register initial values, inversion annotations, physical bindings and direct output arcs after target-relative STA, with an unchanged-state check. The independent output-register auditor predicts all 171 Iologic attributes from the logical primitive and controls, checks exclusive physical ownership and exact pin nodes, and compares the resulting FSE requirements with the encoded image. Unknown/nondefault initialization, unresolved inverter sources and uncalibrated interfaces are rejected.

| Design | Output registers | Checked IOREG bits | Remaining endpoint records | Checked routed-parent bits | Unexplained core FF modes |
|---|---:|---:|---:|---:|---:|
| UART | 11 | 1100 | 12 | 13436 | 2 |
| int2float | 6 | 600 | 0 | 7070 | 2 |
| TMDS | 10 | 1000 | 86 | 12315 | 0 |

The endpoint counts are unresolved records, not a resource-coverage percentage. Parent-bit checks cover paths reaching their declared drivers; dedicated links and primitive modes remain separate gates. int2float has no unresolved captured endpoint, but its baseline is not frozen while core mode semantics remain unexplained. UART/TMDS still require carry and mode-dependent connection rules. Vex hard-state proof and unused-pad acceptance remain open.

Official captures include output-I/O tile types245 and63 beyond the existing production whitelist. `observed_requirements` checks their captured attribute vectors and encoded bits for readback only. The own placement backend's whitelist is unchanged. No arbitrary placement capability is inferred from these observations.

The constant-inactive GSR rule checks exact logical source, polarity, binding/reference state and two native encoded features. It introduces no reset sequence or zero-initialization assumption. Actual in-memory mutations of GSRI polarity and an encoded GSR bit reject. Output-register attribute and encoded-bit mutations also reject. Twenty-six focused tests pass after integration, including wrong physical pad, uncertified output contract and incorrect GSR source/identity/polarity. Original files are unchanged by negative controls.

All three current encoded payloads and noncomment post-P&R Verilog match trial014 exactly. Raw hashes differ because generated metadata differ; both are retained. The trial013 logical proof chain therefore applies to identical logical content. Parent/fuse checks were rerun directly on trial016 rather than assuming pointer identities persist across processes. They match all32,821 examined bits without examined canonical ownership conflicts.

The core-register diagnostic compares all19 fields against the isolated-FF model, excluding only the other FF's INIT field. Control-only fields match for62/12/28 registers, but two UART and two int2float registers differ in fields11/12 (observed1, isolated prediction2). The current shared-FF calibration does not explain these fields. They are not waived.

The int2float observations expose a promising architectural opportunity: `r_8_s0` at native(96,159,slot0) and `r_10_s0` at(96,159,slot1) receive external data through STA nodes SEL0/SEL1 while the corresponding physical LUTs are live logical cells `q_2_s5` and `q_2_s9` with truth tables20AA/FAD8. Their raw FF D nodes remain DI0/DI1. Thus these are not merely stale truth words in unused LUTs. UART also contains two independent-data/live-companion observations at native(96,149,slots3/2), matching its two unexplained field pairs. An earlier exploratory comparison matched repeated local names alone and attributed the UART mismatches to the wrong physical registers at column167; the saved diagnostic corrects this by matching both name and physical header. Fields11/12 still must not simply be renamed as independent-input selectors based on correlation: controlled mutation is needed.

Next experiment: use reversible native configuration and timing probes to identify independent FF data selection, shared-control constraints and competition with wide-MUX select resources. Check both input routing and occupied companion LUT behavior, including negative resource-sharing cases. Only then add a common packing/search operator that can place unrelated LUT logic beside a register, jointly scoring routing and timing. Potential benefit is fewer identity-LUT adapters and better packing/locality; no frequency gain is established yet.

The first closeout scan raced with a test deleting its temporary directory; it failed rather than reporting an incorrect capacity total. After tests completed, the scan and closure succeeded. Workspace is approximately39.470GB, below40GB; reserve before further writes. Historical ZIP and frozen evidence remain unchanged.
