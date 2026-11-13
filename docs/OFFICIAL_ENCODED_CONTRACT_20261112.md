# Encoded official baseline contracts and post-P&R logic proofs

Trial013 advances baseline acceptance; it does not freeze competitive denominators or change own frequencies. All work retains the original3300-second budget, closing in928.26s. Authoritative ledger: `results/official_encoded_contract_trial_013/completion_audit.json`.

The previous portfolio captures were produced without `-bit`; physical I/O attribute arrays remained zero although logical attributes were populated. That was insufficient evidence for final electrical configuration. The new optional `--bitstream` mode in `native_fixed_layout_sta.py` performs native encoding during the single official P&R invocation, then retains the same-layout four-class recalibration. UART/int2float/TMDS reuse exactly the official mapped netlists, fixed CST, common core-input-register policy, and100MHz implementation constraint. Final evaluation remains1.224 times each reported reference.

| Design | Reported MHz | Evaluation MHz | Encoded pads checked | Physical LUT sites checked |
|---|---:|---:|---:|---:|
| UART |136.878|167.538672|24|142|
| int2float |134.432|164.544768|19|80|
| TMDS |136.437|166.998888|23|115|

All three retain passing hold and same-period/changed-period timing invariants. Every encoded logical/physical I/O attribute and GSR binding matches the own-export fixed-CST reference by physical site. All native frame CRCs/tails pass. `audit_vendor_lut_payload.py` compares all captured physical LUT truth words, including FF companion LUTs, with FSE-addressed encoded bits:337 sites and zero mismatches. A one-bit in-memory truth-word mutation is rejected. This does not claim complete clock/routing/control configuration semantics or hardware equivalence.

Fresh source-to-official-mapping proofs pass for all three. Direct source-elaboration-to-post-P&R logical proofs also pass for int2float/TMDS. UART's default naming loses useful state correspondence: both direct and mapped-stage attempts remain unproved, with initial bounded checks passing. The uniform `flatten -separator /` convention matches the flattened native hierarchy and proves mapped-to-post-P&R logic. Together with the separately checked source-to-mapped theorem and identical boundary hashes, this completes the transitive UART logical proof. No state is assumed zero, no INIT is hidden, and no reset sequence is introduced. Six real SAT positive/INIT/reset controls cover both naming conventions; combined related tests total16 passing.

The source checker labels the semantic stage explicitly and validates the originating synthesis/calibration hashes. Earlier failed attempts are preserved. The post-P&R theorem is about logical Verilog emitted by the native tool; it is not an independent proof that every physical route/configuration bit implements that Verilog.

Remaining baseline gates are now explicit: design-level logical endpoint to physical route correspondence/resource acceptance and used register/mux/carry/clock configuration consistency beyond snapshots. Vex additionally lacks unbounded memory/DSP state correspondence and realization of the two unused ERR pads. Native encoding remains permitted; no hardware programming is required or performed. These pending gates prevent freezing denominators or claiming the20% objective.
