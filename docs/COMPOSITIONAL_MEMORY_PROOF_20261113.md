# Trial029: array-boundary proof and INIT transport diagnosis

The production Vex source proof is **still incomplete**. No frequency, selected
physical implementation or fair vendor baseline is changed. Production
`formal_library()` remains unchanged and still rejects unsupported SDPB.

The experimental builder preserves eight source arrays and ten mapped word
arrays instead of applying `memory_map`. It builds both transition systems in
24.29 s. Most of the mapped JSON size is zero-port `$scopeinfo` metadata, not
functional logic. The original224MB JSON is losslessly gzip stored with its
decoded hash retained; functional abstractions remove only checked zero-port
scope metadata and nonfunctional source-location attributes.

`compositional_memory_cut.py` proposes memory groups using restored-array and
hierarchy provenance. Names are proposals, never equivalence evidence. It
checks72192 logical storage bits and1024 replicated-bit initial correspondences.
All source arrays here are uninitialized; their values are not modified or
forced to zero. Physical replica initialization must agree even when the
logical source has undefined initialization.

The conditional controller exposes common raw-read inputs plus every normalized
write clock, per-bit write mask, masked write data, write/read address, high
address and padding obligation. Read pipeline registers and ordinary state remain
in the controller. A proof of this abstraction alone is not a memory/source
certificate: exact SDK model admission, storage-transition induction,
well-founded composition and complete original initialization composition remain
required. Global GSR is the existing inactive model contract; full typed source
binding and composition are not newly certified by this experiment.

The four-step initial check on the **unmerged abstract controller pair** passes,
including the original interface and added memory command observations. This
must not be relabeled a four-step proof of the unabstracted complete memory design.
The next matched-observation check fails; induction is not reached.

## A concrete tool-construction issue

Snapshots show4368 initialized wires/5020 annotated defined bits in the matched
source design, but zero initialized wires in `equiv_miter`'s generated miter.
Some annotations alias the same bit. The [Yosys implementation](https://raw.githubusercontent.com/YosysHQ/yosys/main/passes/equiv/equiv_miter.cc)
creates new wires without copying their attributes. This confirms actual INIT
loss in this conversion, rather than a reason to zero unknown state.

`restore_equiv_miter_init.py` restores only existing source initialization through
unchanged same-name state cells, bijective copied-cell port topology and matching
wire-name/offset witnesses. It restores5015 distinct defined state bits in4363
state cells, including original zero and one values; unknown bits remain unknown.
Logic, interfaces and state cell bodies are unchanged. Conflicting INIT or altered
state identity/wiring rejects. The first alias-only mapping correctly rejected
ambiguous unused alias positions; the final implementation checks actual copied
cell ports and wire witnesses together.

Restoring INIT removes the padding-write failures and all observed later-frame
failures in the retained counterexample, but four DCache write-high observations
still fail in the initial frame. The matched-base gate remains FAILED; no
inductive certificate or production admission is claimed. Next inspect matched
signal substitution and undef-aware masked-address relationships against the
original pair; do not remove the checks, assume reset, or initialize Gold to zero.

Evidence is under `results/compositional_memory_proof_trial_029`:

- `array_pair_001`: exact source/model provenance and preserved array systems.
- `controller_cut_001`: initial relation, abstraction, passing original-controller
  base and failing matched-base log.
- `match_diagnostic_003/counterexample_summary.json`: pre-restoration failures.
- `init_transport_004`: exact transport certificate, failed restored check and
  `restored_counterexample_summary.json`.
- `tests.log`: twelve passing INIT-transport and existing source-proof tests.

`match_diagnostic_002` is a retained relative-path invocation failure, not a
counterexample. No timeout is called a proof. The round preserves its original
3200s deadline and the40GB workspace limit. The closeout assertion rejected
the round as late against that unchanged internal deadline. Final accounting is
3297.05s (about54.95 minutes), within the user's3600s limit. The internal deadline
miss is recorded explicitly; it was not reset or relabeled as on-budget success.

Gemini's report is `carbon-advice-compositional-memory-20261113-048.md`. Its
composition warnings are useful hypotheses, but some factual details are wrong:
the SDK alignment shifts are `$clog2(W)` (W16 uses ADA[13:4], W32 uses ADA[13:5]),
and the restored DCache already contains four separate8-bit arrays. Suggested
completion times and unverified post-P&R width changes are not accepted evidence.
The [Yosys memory-cell documentation](https://yosyshq.readthedocs.io/projects/yosys/en/v0.57/cell/word_mem.html)
was checked for array-port contracts. No opaque memory output or bounded result
has been promoted into a full sequential proof.
