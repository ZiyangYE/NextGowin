# Exact public-tool identity and native UserCode policy

Trial `results/official_payload_identity_trial_022` keeps the original 3300-second
deadline. The preceding trial made real progress but left public/rebuilt bitstreams
different. This round resolves that difference without masking or editing images.
Own Fmax incumbents and the four-design goal remain unchanged and unfinished.
The technical checks completed, but the first closeout assertion detected that
the unchanged 3300-second internal deadline had expired. This is an internal
closeout-budget failure, not an on-budget round. The completion ledger separately
records the user's 3600-second limit; no deadline was reset.

## What differed

UART, int2float and TMDS already had identical 33,245,440 configuration data bits,
all 21,872 frame CRCs, and every other non-comment word. Only footer word 21886
differed. Public CheckSum/UserCode values were 4215/94EB/ABCB; the bare DLL encoder
reported the same CheckSum but UserCode 0. The public default device.cfg was absent
from the earlier reconstructed command.

An isolated native control confirms the interpretation. Adding only the public
device.cfg makes the complete int2float payload identical. Native runs with
`set user_code = 0x12345678` and `0x89ABCDEF` change only the low 32 bits of the
0x0A command and its UserCode comment; configuration data, frame CRCs, checksum,
other commands and timing remain unchanged. No bitstream bytes were manually
patched. This is calibrated behavior of the pinned SDK, not a claim about all
Gowin versions. UserCode 0 is a valid explicit value; it must not be confused with
this public tool's default checksum policy.

## Complete command reproduction

`official_command_template.py` validates source netlist/CST/SDC identities, device,
I/O-register policy and bitstream request, then rebinds only paths. It preserves
all public non-path options, including bare `-o`, `-init_all`, SDP conversion,
retiming/clock order/global-frequency and the exact device.cfg.

All four resulting non-comment payloads now match the actual public `gw_sh run all`
images word for word. Their post-P&R Verilog bodies also match after comments are
removed, and their initial public/rebuilt setup, hold, recovery and removal
envelopes match. The retained-state replays then pass the original same-period,
changed-period, analysis-inventory and physical-snapshot guards.

| Design | Native Fmax MHz | Evaluation MHz (1.224×) | Setup WNS ns | Hold WNS ns |
|---|---:|---:|---:|---:|
| UART | 133.493 | 163.395432 | -1.371 | +0.014 |
| int2float | 134.432 | 164.544768 | -1.361 | +0.003 |
| TMDS | 132.944 | 162.723456 | -1.377 | +0.002 |
| VexRiscv | 72.800 | 89.107200 | -2.514 | +0.002 |

The negative setup margins are expected at the comparison target; they are not
closure at that constraint. Reported Fmax recognition follows the user policy
after the remaining required gates, without a redundant Fmax-confirmation run.

Bare Vex reconstruction was 73.248 MHz. Exact command reconstruction returns
72.800 MHz and exact public bytes. The actual current mapped representation has
10 SDPBs; public/exact post-P&R has 14, while bare reconstruction retains 10.
Missing SDP conversion is a concrete representation difference. The frequency
difference is not attributed to that single flag without a separate ablation.
This is vendor-reference calibration, never own-P&R performance.

## Own export integration and negative controls

The own-P&R checkpoint/export/import entries can carry an explicit, hash-bound
device.cfg. Native UserCode observation verifies the footer command and its
comment, and checks the configured default-checksum or explicit-hex policy.
Existing routing, configuration, I/O, four-class timing and frame guards remain.
The profile is explicit; historical files and defaults were not rewritten.

Re-exporting the retained own int2float placement/routes with the public default
cfg changes only UserCode (0 to 0xB494). Native Fmax remains 167.613 MHz, setup
+0.004 ns and hold +0.002 ns; all non-setup checks pass. No vendor placement/routing
was executed, and no frequency improvement or complete hardware verification is
claimed. Wrong configuration hashes, wrong source constraints/policies, changed
frame data, changed footer commands/width/opcode, and wrong UserCode claims are
rejected by targeted tests.

## Fixed I/O and remaining baseline gates

The public pin report is HTML, unlike the text-format report used by the original
own importer. A strict header-checked adapter now reuses the same pin/electrical
checks. Public reports match all 24/19/23/284 constrained ports. Vex's two ERR
inputs are explicitly reported at C17/5 and A5/6, both input/LVCMOS33/NONE/3.3V.
This closes report-format/coverage confusion; it does not substitute for outstanding
unused-pad native readback or all 81 physical I/O attribute semantics.

No fair denominator is frozen yet. Stronger eligible candidates such as the
100MHz core-input UART136.878 and TMDS136.437 must remain in the common selection.
Vex source/hard-state proof still rejects unsupported SDPB. Source proofs for the
other three public designs remain valid, but completing the full acceptance ledger
and selecting the strongest eligible common-portfolio result are still required.

Evidence: `exact_public_payload_identity.json`, `public_initial_sta_identity.json`,
`usercode_controls_audit.json`, `own_export_policy_audit.json`,
`public_fixed_io_audit.json`, and `completion_audit.json` in trial022. Initial raw
HTML-reader/text-parser failures and storage-reservation failures are retained.
Superseded configuration, route and endpoint witnesses were only losslessly
compressed with inventories; incumbent decoded hashes and the delivered archive
remain unchanged. Gemini041 completed and was independently checked; its older
8-to-10 SDPB count is superseded by the measured current 10-to-14 count.
