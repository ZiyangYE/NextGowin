# Native logical endpoints and encoded control modes

Trial014 closes in1296.32s within its original3300s deadline. No own frequency changes or frozen fair baselines are claimed. See `results/native_endpoint_correspondence_trial_014/completion_audit.json`.

The old snapshots contained logical pin addresses and physical parent trees but omitted the pin-to-node association. The optional `--capture-connectivity` capture now records each pin's owner, label, RTTI class, net, inversion annotation and all physical nodes returned by the native API. A single additional official P&R/encoding capture for each of UART/int2float/TMDS preserves their previous constraint policy and timing invariants. Encoded payloads and noncomment post-P&R Verilog match trial013 exactly; therefore the previously established logical proof chains remain relevant. Raw file hashes differ due to generated comments and are retained separately.

`audit_native_endpoint_correspondence.py` traces every captured sink node toward its declared driver. It checks native logical-net identity, detects missing parent records/cycles and cross-driver ownership of examined native node addresses. It does not assume native node addresses establish cross-coordinate alias ownership; the independent RRG/PIP check is still needed.

| Design | Physical endpoints reaching declared driver | Captured physical endpoints | Static logical connections justified | Unresolved records |
|---|---:|---:|---:|---:|
| UART |638|703|82|25|
| int2float |286|328|23|19|
| TMDS |488|642|60|117|

The columns have distinct meanings: static logical connections can have no ordinary physical node, and some constant-control endpoints are included in the physical count. Do not add these columns to manufacture a coverage percentage. No examined native-node conflict or logical-net-identity error was found.

Static admission is narrow: same-slot LUT→FF requires matching companion LUT/header/truth and input_mode0; dedicated mux links require calibrated level/slot child geometry and the actual native logical cache; constant CE/reset control requires the correct native physical port and an encoded CLS vector whose control attributes match the logical rail. Unknown/null port labels stay unsupported. `audit_vendor_cls_payload.py` checks212 captured CLS groups and3763 encoded configuration bits, with zero mismatches. These vectors do not automatically certify every used primitive mode.

Nineteen focused tests pass, including wrong driver roots, cycles, missing parents, incorrect logical-net identity, shared-resource conflicts, changed LUT companion mode/truth, changed constant-control attributes and inversion. Truncating a parent in the actual UART snapshot reduces correctly matched endpoints and is detected. The in-memory mutation leaves original evidence unchanged.

Remaining records concentrate on output IOREG data/control links, constant rails/mode-ignored ALU inputs and dedicated carry links. Some primary Iobuf owners are not present in the ordinary hierarchical-instance vector; the existing I/O inventory provides separate site evidence, but current rules do not silently treat that as complete IOREG binding. Ordinary PIP/fuse validation and the remaining dedicated mode rules still prevent baseline freezing. Vex hard-state/unused-pad gates are unchanged.

Lossless compaction of superseded non-frozen trial003 and trial013 PR serialization caches saved59,018,931 and59,020,446 bytes. Cleanup receipts preserve dependency rationale and original/decoded hashes. Direct PR-file consumers must materialize the gzip payload first. No delivered ZIP or frozen evidence changed. Workspace at close is39,763,896,645 bytes; additional large native runs require another dependency-checked compaction first.
