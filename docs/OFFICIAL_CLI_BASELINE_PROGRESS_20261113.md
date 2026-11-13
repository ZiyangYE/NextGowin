# Official CLI baseline and common-option evidence

Trial `results/fair_native_baseline_trial_021` preserves its original 3300-second
deadline and the full four-design objective. No own implementation changed and
no competitive denominator was frozen.

## Direct official synthesis and P&R

The actual installed `gw_sh.exe` was run with its normal existing license
verification. The first restricted launches failed license connection checks;
allowing normal verification succeeded. No license check or vendor binary was
modified. The public CLI required explicit device version C and lowercase
`gowinsynthesis`; the saved recipes record these corrections. UART/int2float top
is `top`, TMDS is `svo_tmds`, and Vex is `VexRiscv`; initial incorrect-top attempts
are retained separately and are not vendor algorithm failures.

The public default options disable input, output and bidirectional I/O-register
packing. The earlier two-policy DLL portfolio omitted this all-core profile.
Public `run all` at 200 MHz on int2float produces the same mapped Verilog as the
older explicit DLL synthesis sequence after comments are removed, and reproduces
the 134.779 MHz report with 11 hold violations. Thus that particular failure is
not explained by a different synthesis sequence.

A common extension was declared before execution: implement each design at
0.93 times its `core_inputs` 200 MHz scouting Fmax, with official synthesis and
P&R, timing-driven mode, correct-hold enabled, all I/O FF packing disabled,
unchanged pins, RTL and interface cycles. These are engineering references,
not final competitive denominators.

| Design | Implementation MHz | Reported Fmax MHz | Setup WNS ns | Hold WNS ns |
|---|---:|---:|---:|---:|
| UART | 133.119 | 133.493 | +0.021 | +0.014 |
| int2float | 125.344 | 134.432 | +0.539 | +0.003 |
| TMDS | 129.490 | 132.944 | +0.201 | +0.002 |
| VexRiscv | 70.178 | 72.800 | +0.513 | +0.002 |

All four report zero setup/hold violations. UART also reports positive recovery
and removal margins. Exact reports, executable recipes, tool-generated options,
command files, RTL hashes, netlists and encoded images are retained. The Vex
72.800 MHz result is a real public-tool run; it does not use the prohibited
63.729 MHz relaxed calibration denominator.

## Proof and calibration scope

UART, int2float and TMDS actual public mapped outputs AND actual public post-P&R
Verilog pass the existing original-pair initial-state SAT and induction gates.
The independently retained checked elaboration uses unchanged RTL inputs. The
new `--official-cli-post` entry checks executed CLI provenance, recipe and mapped
hashes before proving the actual post-P&R file. A wrong recipe hash is rejected.
Vex still rejects the unmodeled SDPB primitive; no memory/state proof is claimed.
Ten targeted tests pass, including existing initial-state and STA negative checks.

Separate rebuilds through the recorded-controls calibration engine reproduce the
three small designs' Fmax and pass same-period/target-relative four-class and
inventory guards at 1.224 times reported Fmax, with unchanged state WITHIN each
rebuild. However, their full encoded payloads differ from the public CLI images.
They therefore DO NOT establish that the original public layouts were replayed
unchanged. This difference must be classified; identical Fmax is not physical
equivalence. Vex target-relative rebuild was blocked by a storage reservation,
not by a native STA failure. See `official_cli_acceptance_progress.json`.

The earlier 16-case DLL portfolio used shared 5/10 ns implementation periods and
`core_inputs`/`native_defaults`. All 200 MHz cases that completed calibration
failed hold. Core-input 100 MHz UART/int2float/TMDS report 136.878/134.432/136.437
MHz with passing hold; these stronger alternate results cannot be ignored in
favor of a weaker public profile. Two Vex storage failures were retried under the
same original deadline. UART/int2float native-default 100 MHz runs hit a native
access violation during routing; they remain failed candidates.

## Remaining acceptance work

Freeze only the highest fully accepted candidate under a completed, common
portfolio. Preserve the distinction between vendor-reference acceptance and
unimplemented own-backend modes; permission to use the native encoder does not
prove unexamined configuration semantics. Remaining items include classifying
public/rebuilt image differences, original-layout target-relative STA, complete
fixed I/O and source-clock acceptance, and Vex SDPB/hard-state proof plus the two
unused ERR input pin/default checks. Current evidence suggests TMDS is the largest
performance gap, but no final competitive ratio is claimed.

Read-only binary inspection found that `iobuf::initFusemapAttr` clears 81 shorts
at offset 0x4c and `getPioAttr` reads this vector. Existing state capture exposes
only the first 16 named attributes. Extended capture and interpretation were not
implemented in this round; this is a concrete remaining configuration-coverage
question, not a new validated device capability.

Storage receipts preserve decoded hashes for losslessly compacted rejected
configuration/route evidence and public images. New terminal DLL-portfolio PR
serialization caches were separately inventoried and discarded after completion;
their original PR files are not retained and native PR replay requires rebuilding
from the recorded source/command. Reports, sources, configuration snapshots and
encoded images remain. Independent configuration revalidation after compression
checks 642,898 bits. The delivered archive and incumbent implementations remain
unchanged. See the completion ledger for final elapsed time and workspace size.
