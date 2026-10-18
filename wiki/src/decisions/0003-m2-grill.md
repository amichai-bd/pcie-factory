# 0003: M2 grill decisions

- **Status:** accepted
- **Source:** captain grill session for M2 (2026-10-15), recorded in the M2
  epic ([#203](https://github.com/amichai-bd/pcie-factory/issues/203)). The
  goal and decision table are copied verbatim from the epic body: "this
  epic" refers to the epic. Later decisions are listed under Updates. Waves and their issues are on the
  [design roadmap](../roadmap.md#m2-data-link-layer).
- **Numbering:** decision records are numbered once across the repository;
  [0001](../../project/decisions/0001-m0-grill.md) is the factory's M0
  record and [0002](0002-m1-grill.md) is M1's.

## Goal

**Add a reliable PCIe Data Link Layer as a new `dll` IP, stacked over `phy` in a new `link` integration IP. Verify it against an independently written link partner to an error-recovery, throughput and credit-correctness bar, with FPGA and ASIC timing evidence, on a factory with faster CI and review loops.**

M2 starts only after M1 (#65) closes (captain, M2 grill). M2 is done when all of the following hold on `main`:

1. **Flow:** every sub-issue of this epic is closed through the normal agent flow, with an independent `REVIEW-VERDICT: APPROVE` at the merged head.
2. **Specs:** the `dll` and `link` HAS pages carry a captain (or delegate) sign-off on their PR. `dll` and `link` each have one MAS and one DV_PLAN. Citations pin PCIe Base Spec **rev 3.0**; no spec text, table or figure is reproduced.
3. **Function:** `./build regress --level 0 --level 1 --level 2` passes for `dll`, `link`, `phy`, `mac`, `pcs` and `common`.
4. **Error recovery:** the `link` back-to-back e2e recovers through replay from injected LCRC errors, dropped and corrupted TLPs and DLLPs, and NAKs, with no lost, duplicated or reordered TLPs. A REPLAY_NUM rollover leads to a retrain, after which traffic resumes.
5. **Credits:** no receiver overflow under credit starvation and recovery, with finite and infinite credit configurations both regressed.
6. **Throughput:** sustained TLP throughput at the fraction of Gen1 x1 line rate stated in the `link` HAS, measured in e2e with ACK and UpdateFC overhead.
7. **Independent oracle:** the `dll` and `link` DV use a DLL link partner written independently of the `dll` arch model (separate author, no shared code) as the second opinion on protocol behaviour.
8. **Physical:** nightly reports met timing for `link` at **50 MHz on the DE2-115** (Quartus, M1 Q26). On sky130hd (ORFS), `link` meets the **100 MHz checkpoint** in study mode (M1 Q27) and reports 125 MHz metrics nightly; 125 MHz closure is future timing optimization (Q10, #235), shown on the tracker.
9. **Factory:** CI cancels superseded runs, PR evidence is pre-checked before review, and the tracker shows per-IP regression trends with drop alerts.

## Decisions

| # | Decision |
|---|---|
| Q1 | M2 delivers the **Data Link Layer**: DLLP framing, TLP sequence numbers and LCRC, ACK/NAK, the replay buffer and replay timer, REPLAY_NUM handling, flow-control initialisation and UpdateFC, credit accounting, and the data-link control state (DL_Inactive, DL_Init, DL_Active, DL_Up). The physical layer stays Gen1 x1. The transaction layer is later. |
| Q2 | **Hardware:** synthesis and timing evidence only, as in M1. No board bring-up in M2. |
| Q3 | **Oracle:** an **independently written DLL link partner** (a different author from the arch model, written from the spec, sharing no code with it) is the independent judge in DV. No third-party model is adopted in M2. |
| Q4 | **Layout:** a new top-level IP `dll`, and a new integration IP `link` (`dll` over `phy`), mirroring how `phy` stacks `mac` over `pcs` (M1 Q19, Q24). Each has one HAS, one MAS, one DV_PLAN and one testbench; DLL blocks are RTL files inside `dll`, with no sub-IPs. |
| Q5 | **Upper interface:** a valid/ready stream of whole TLPs as opaque bytes, plus credit-available signals per type (posted, non-posted, completion; header and data). The testbench stands in for the transaction layer; M3 adds it on top unchanged. |
| Q6 | **Configuration:** small and fixed. Max payload 128 B; one virtual channel (VC0); no ECRC, ASPM changes or extended features. Replay buffer sized for the Gen1 x1 ACK latency; credit limits are parameters with defaults in the HAS. |
| Q7 | **Exit bar:** error-recovery e2e, credit correctness, a throughput target set in the `link` HAS, and FPGA/ASIC timing met for `link` (goal items 4–6 and 8). |
| Q8 | **Factory ride-alongs:** CI cancels superseded runs (concurrency groups); a machine pre-check of PR evidence before review; nightly per-IP regression trends with drop alerts. |
| Q9 | **Sequencing:** M2 starts strictly after M1 closes. |
| Q10 | **ASIC timing gate for M2 follows M1 Q28 (firstmate as the captain's delegate, 2026-10-17, after the captain's ruling that 125 MHz is future optimization).** `link` contains `phy`, which does not yet meet 125 MHz on sky130hd (#235). The M2 ASIC gate is therefore the 100 MHz checkpoint (`--study-clock-period 10`, Q27), with 125 MHz reported nightly. 125 MHz closure stays with #235 and does not block M2. |

## Carried over from M1

The epic states that M1's rules carry over unchanged. Each is recorded in
full in [0002](0002-m1-grill.md#decisions) and applies to `dll` and `link`
as it does to `phy`, `mac` and `pcs`:

| M1 | Rule in M2 |
| --- | --- |
| Q5 | The PCIe Base Spec is paraphrased and cited by section, never copied (goal item 2). |
| Q21 | The `dll` and `link` HAS and MAS are self-contained: every DLL rule the design implements is written out in our own words; citations are for cross-checking only. |
| Q22 | The shared RTL vocabulary and the hard coding-style rules apply to all `dll` and `link` RTL. |
| Q24 | One testbench per IP; no sub-IPs (M2 Q4 applies it to `dll` and `link`). |
| Q25 | Structs at interfaces inside an IP, declared in a package and referenced as `pkg::t_name`. |
| Q26 | The FPGA (DE2-115) timing target for `link` is 50 MHz (goal item 8). |
| Q27 | The 100 MHz study checkpoint is M2's ASIC gate for `link` (Q10); 125 MHz stays the default, nightly target, and study results are kept separate from that default evidence. |
| Q28 | 125 MHz ASIC closure is future timing optimization ([#235](https://github.com/amichai-bd/pcie-factory/issues/235)); it does not block M2 (Q10). |

## Waves

Copied verbatim from the epic; the [roadmap](../roadmap.md#m2-data-link-layer)
maps each wave to its issues.

- **Wave 0, factory and record:** decision record and roadmap; CI stale-run cancel; PR evidence pre-check; nightly trends.
- **Wave 1, specs:** `dll` HAS and arch model (captain sign-off); `link` HAS (captain sign-off); the independent DLL partner.
- **Wave 2, `dll` RTL:** top, DLLP and data-link control with the `dll` testbench; then the TX replay path, the RX check and ACK/NAK path, and flow control.
- **Wave 3, `link`:** stack and back-to-back e2e with error injection and credit tests; throughput; coverage floors; FPGA and ASIC.

M1 itself closed on 2026-10-17 ([#65](https://github.com/amichai-bd/pcie-factory/issues/65)),
which satisfies Q9.

## Updates

- **2026-10-17:** Q10 (the M2 ASIC gate is the 100 MHz checkpoint, with
  125 MHz reported nightly) added and goal item 8 changed to match, both
  copied verbatim from the epic body above.
- **2026-10-17, dll transmit path ([#212](https://github.com/amichai-bd/pcie-factory/issues/212)):**
  firstmate decisions as the captain's delegate, which the captain may
  override ([epic #203 comment](https://github.com/amichai-bd/pcie-factory/issues/203#issuecomment-6233941574)):
  1. A TLP still going out when a replay starts is replayed too: a TLP is
     sent once the arbiter hands it to the `phy` (it is behind
     NEXT_TRANSMIT_SEQ and in the replay buffer). This closes the `dll` HAS
     open item raised by #213 in favour of the architecture model's
     reading; the independent partner is corrected to match (its
     `_start_replay`, with `test_nak_during_a_tlp_replays_it_too`), still
     with standard-library imports only.
  2. Amendments to the `dll` HAS and retry page, accepted with the `dll`
     HAS: (a) an acknowledged TLP still on the stream and every TLP after
     it keep their replay-buffer space until its `eop` (a refinement of
     the Q6 accounting); (b) at a REPLAY_NUM rollover a TLP taken but not
     started is withdrawn and resent by the replay (from the hold rule);
     (c) `tlp_tx_ready_o` is 0 on a `sop` in the cycle after an accepted
     `eop` (upper stream, Q5); (d) NEXT_TRANSMIT_SEQ advances when the
     arbiter takes a TLP.
  3. A `sop` that would be taken at the rollover edge itself is withdrawn
     too, as #211's link-down edge case: a rollover never lets a TLP go
     out ahead of its replay.
  4. The open item "when a running replay ends" (for a replay requested
     during it) stays open for the captain, owned by epic #203; no effect
     on the line was observed.
- **2026-10-18 (captain):** the regression CPU budgets may be raised as
  needed, after speed-ups and rebalancing
  ([#272](https://github.com/amichai-bd/pcie-factory/issues/272#issuecomment-6242089772)).
  Applied by #272: L1 600 s to 1200 s, L2 7200 s to 9600 s (measured need
  plus about 20%); L0 stays 120 s. The DevOps BKM owns the values.
  Per-IP affected L1 at #272: dll 332 s, link 123 s, mac 540 s (with
  `no_scramble` restored to L1, 48.2 s), pcs 577 s, phy 330 s.
