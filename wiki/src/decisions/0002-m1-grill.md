# 0002: M1 grill decisions

- **Status:** accepted
- **Source:** captain grill session for M1, recorded in the M1 epic
  ([#65](https://github.com/amichai-bd/pcie-factory/issues/65)). The goal and
  decision table are copied verbatim from the epic body: "you" is the
  captain, and "this epic" and "below" refer to the epic. Later decisions are listed under Updates. Waves and their issues
  are on the [design roadmap](../roadmap.md).
- **Numbering:** decision records are numbered once across the repository;
  [0001](../../project/decisions/0001-m0-grill.md) is the factory's M0 record.

## Goal

**Deliver a Gen1 x1 PCIe physical layer as two IPs, `mac` and `pcs`, joined by PIPE and stacked in a thin `phy` IP, verified to every Gen1 LTSSM state with FPGA and ASIC evidence, on a factory hardened for multi-clock, coverage-driven design.**

M1 is done when all of the following hold on `main`:

1. **Flow:** every sub-issue of this epic is closed, each delivered through issue → branch → worktree → draft PR → independent `REVIEW-VERDICT: APPROVE` at the merged head → squash merge → cleanup.
2. **Specs:** the HAS pages for `phy`, `mac` and `pcs` each carry a captain approval comment on their PR. Every IP and sub-IP has its MAS and DV_PLAN. Citations pin PCIe Base Spec **rev 3.0** (Gen1 rules), and no spec text, table or figure is reproduced (the citation check passes).
3. **Function:** `./build regress --level 0 --level 1 --level 2` passes for `phy`, `mac`, `pcs` and every sub-IP. The `phy` e2e trains a downstream and an upstream instance back to back to L0 at Gen1 x1, and exchanges framed TLP- and DLLP-tagged packets in both directions under ±300 ppm, bit slips and injected symbol errors. The `mac` tests against the Python link partner enter and leave every Gen1 LTSSM state.
4. **Coverage:** LTSSM transition coverage is 100% of the arcs listed in the `mac` HAS at L2. Verilator line and toggle coverage is in the nightly record and on the tracker, and the floor set by its issue is enforced.
5. **CDC:** the clock-crossing check passes on every IP, and every crossing goes through an approved `common_` cell.
6. **Physical:** nightly reports Quartus DE2-115 timing (125 MHz core clock and RX clock met) and ORFS sky130hd metrics for `phy` on the tracker. (The board loopback demo on physical hardware is tracked separately in #89, outside M1; see Q20.)
7. **Factory:** the M0 follow-ups below are closed, the seed `top` IP is retired, `./build check` (including neutrality) passes, and ci, nightly and pages are green.

## Decisions

| # | Decision |
|---|---|
| Q1 | M1 delivers the **Gen1 x1 physical layer** (2.5 GT/s, 8b/10b) bottom-up. Transaction and data-link layers come later. |
| Q2 | The seed `top` IP is **retired** once the new IPs cover everything it proved. The x^16 scrambler LFSR is reused (adapted) in `mac`. |
| Q3 | The LTSSM implements the **full Gen1 state set**: Detect, Polling, Configuration, L0, Recovery, L0s, L1, L2, Hot Reset, Disabled, Loopback and Polling.Compliance. |
| Q4 | DV uses an **in-house Python link-partner model** at the PIPE level, written from the spec with error, timeout and corner-case injection, shared with the arch model. Also a **back-to-back e2e** of two `phy` instances. |
| Q5 | The PCIe Base Spec is **paraphrased and cited by section**, never copied: no spec text, tables or figures in the repo. A check and a reviewer item enforce this. |
| Q6 | **Two top-level IPs** joined by a real PIPE interface. `mac` holds the LTSSM, scrambler, ordered sets and framing. `pcs` holds 8b/10b encode and decode, comma alignment, the elastic buffer, and PIPE control. |
| Q7 | MAC↔PCS is **PIPE, 16-bit at 125 MHz**: the Gen1 subset of a pinned PIPE revision (named in the `phy` HAS). |
| Q8 | A thin **`phy` top-level IP** instantiates `pcie_mac` and `pcie_pcs`. It owns the stack-level HAS, the arch model, e2e DV, FPGA and ASIC. `mac` and `pcs` keep their own HAS, arch model and unit DV. |
| Q9 | Reference revision: **PCIe Base Spec rev 3.0, Gen1 rules only**, so later Gen2 and Gen3 work extends the design instead of restructuring it. |
| Q10 | One LTSSM supports **both port roles** through a static `port_role` input (downstream or upstream). Both are regressed, and the e2e connects downstream to upstream. |
| Q11 | The MAC's upper interface is a **framed packet stream**. The MAC inserts and strips STP, SDP, END and EDB, inserts idle and SKP, and reports framing errors. Upward it exposes a 16-bit valid/ready stream with start, end and a TLP-or-DLLP tag, plus control and status: link_up, the LTSSM state, and requests for hot reset, disable, loopback and power state. DV uses a stub upper layer. |
| Q12 | The PCS lower edge is a **2×10-bit symbol interface** with **separate TX and RX recovered clocks**. The elastic buffer adds or drops SKPs across into the 125 MHz core clock. The DV channel model applies ±300 ppm offset, bit slips, symbol errors and electrical idle. |
| Q13 | FPGA evidence is nightly **synth and timing** for `phy` on the DE2-115, plus a **board loopback demo** that you program manually: two `phy` instances back to back over an on-chip channel, with the LTSSM state on the 7-segment displays and link_up on an LED. CI never needs the board. |
| Q14 | Done bar: **100% LTSSM arc coverage** at L2 and every DV_PLAN item passing. **Verilator line and toggle coverage** is collected nightly and tracked, with a floor set once measured. Coverage is a design-neutral factory feature. |
| Q15 | **You sign off each HAS** (`phy`, `mac`, `pcs`) on its PR before the RTL waves start. After that, MAS, RTL and DV run under the normal agent review, and you're asked again only on interface or scope changes. |
| Q16 | **One epic.** Wave 0 holds the factory work: the open M0 follow-ups, coverage, the CDC check and cells, the citation check, and the design-level record home. |
| Q17 | **CDC:** crossings may only go through approved `common_` cells (2-flop sync, pulse sync, async FIFO). A design-neutral structural check over Verilator's netlist flags any other crossing. DV adds randomized clock ratios and ppm. |
| Q18 | **Kind-first layout (captain decision, supersedes M0's IP-first layout):** sources live at `src/<kind>/<ip>/` with `<kind>` in `rtl`, `dv`, `arch`, `fpga`, `asic`. Sub-IPs are **flat siblings with prefixed names** (`src/rtl/mac_scrambler/`); parentage comes from filelist `-F`. `arch`, `fpga` and `asic` exist only for top-level IPs. `wiki/src/` mirrors the tree exactly (`wiki/src/rtl/<ip>/MAS.md`, `wiki/src/dv/<ip>/DV_PLAN.md`, `wiki/src/arch/<ip>/HAS.md`, `wiki/src/fpga/<ip>/FPGA.md`, `wiki/src/asic/<ip>/ASIC.md`). Migration is #92, which lands first. |
| Q19 | **Integration IPs (captain decision):** an IP is a sub-IP only when its id is `<parent>_<name>` and that parent's filelist reaches it. A top-level IP (such as `phy`) may include other top-level IPs (`mac`, `pcs`) through its filelist; they keep their own HAS, arch, FPGA and ASIC, and `--affected` still selects the integrating IP. An IP whose id is `<other-ip>_<name>` but that `<other-ip>`'s filelist does not reach is a layout-check finding (include it with `-F`, or rename it). Implemented by #107. |
| Q20 | **Physical FPGA access is separate (captain decision, 2026-10-15):** work that needs the real DE2-115 connected to the captain's computer (programming the board, on-board evidence) is tracked in its own issue (#89), outside M1. M1's FPGA evidence is nightly synthesis and timing only. |

## Updates

- **2026-10-15:** Q19 (integration IPs) and Q20 (physical FPGA access is
  separate) added; done criterion 6 changed to match Q20, so the board demo
  ([#89](https://github.com/amichai-bd/pcie-factory/issues/89)) is outside
  M1. Both are copied verbatim from the epic body above. Q19 then gained
  its last clarification (a `<other-ip>_<name>` id must be reached by
  `<other-ip>`'s filelist), mirrored in the epic.
