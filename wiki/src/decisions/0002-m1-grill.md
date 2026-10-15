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
2. **Specs:** the HAS pages for `phy`, `mac` and `pcs` each carry a captain (or delegate) approval comment on their PR. Each of `phy`, `mac` and `pcs` has one MAS (a section per internal block) and one DV_PLAN (Q24). Citations pin PCIe Base Spec **rev 3.0** (Gen1 rules), and no spec text, table or figure is reproduced (the citation check passes).
3. **Function:** `./build regress --level 0 --level 1 --level 2` passes for `phy`, `mac`, `pcs` and the `common` library. These are the only testbenches (Q24); no leaf sub-IP folders remain. The `phy` e2e trains a downstream and an upstream instance back to back to L0 at Gen1 x1, and exchanges framed TLP- and DLLP-tagged packets in both directions under ±300 ppm, bit slips and injected symbol errors. The `mac` tests against the Python link partner enter and leave every Gen1 LTSSM state.
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
| Q21 | **Our HAS/MAS are the normative protocol spec where no official spec is legally available (captain decision, 2026-10-15).** HAS and MAS pages are self-contained: every protocol rule the design implements is written out in our own words, complete enough to implement and verify without the official document. Citations only point to the official source for cross-checking; they never stand in for the rule. Uncertain points go into the page's Open items table, not guesses. Where an official spec is legally available locally (PIPE, #117), the HAS must agree with it. The public references page (#117) lists every official source of truth. |
| Q22 | **Shared RTL vocabulary from nand2anything (captain decision, 2026-10-15).** Before any M1 RTL, adopt nand2anything's proven RTL vocabulary, adapted with provenance under a neutral prefix: the sequential DFF macros (only the macros write flops), assertion macros, unused-bits lint macro, package and struct referencing conventions, include conventions, and reusable common modules (reset synchronizer, portable RAMs). The style check enforces it, and existing RTL migrates. Implemented by #120. **Hard coding-style rules (captain, 2026-10-15), enforced by the style check:** (1) no nonblocking `<=` outside the macro files (all sequential logic via macros); (2) no assertions or `$error`/`$fatal` written directly, only assertion macros; (3) no `` `ifdef `` family in design RTL, since conditional compilation is hidden inside the macro files; (4) parameters, structs, enums and typedefs are defined in packages and referenced explicitly as `pkg::name`, with no `import pkg::*` (no `import` at all). **CDC metaflop macros (captain, 2026-10-15):** clock-domain crossings are written with macros (`METAFLOP` level synchronizer, pulse sync, reset sync) that wrap the approved `common_cdc_*` cells; outside `src/rtl/common/` the cells are only instantiated through these macros. |
| Q23 | **Functional simulation breadth first (captain, 2026-10-15).** Priority is working RTL+DV for many IPs and an end-to-end simulation as early as possible. RTL PRs get one independent review where only functional or correctness defects block; polish goes to follow-up issues. Blocks start against signed-off HAS interfaces without waiting for neighbours to merge. The `phy` back-to-back e2e testbench starts now, with arch models standing in for unbuilt blocks, which are replaced by RTL as they land. FPGA/ASIC synthesis stays at the end (#87/#88), full `phy` only, no per-module synthesis. |
| Q24 | **Hierarchy, DV scope and packages (captain grill, 2026-10-15).** (1) Only `phy`, `mac` and `pcs` own a DV environment (one testbench each; `phy` is the back-to-back e2e). The `common/` cell library keeps its one library testbench. No other block gets a testbench. (2) No sub-IPs in M1: leaf blocks (scrambler, os, frame, ltssm, enc/dec 8b10b, align, elastic) are RTL files in `src/rtl/mac/` and `src/rtl/pcs/` under one filelist, with one MAS per IP (a section per block). The sub-IP tooling stays but is unused. (3) Packages: one shared `pcie_pkg` in `src/rtl/common/` (K codes, PIPE encodings, LTSSM states, ordered-set constants), plus `pcie_mac_pkg` and `pcie_pcs_pkg` for IP-private types; `phy` has none; no duplicated constants. (4) Leaf checks (8b/10b exhaustive, scrambler known answer, OS fields, elastic ppm, LTSSM arcs) become IP-level tests driven through IP ports; leaf invariants stay as RTL assertions. (5) Delivery: in-flight leaf PRs finish as planned, then one big restructuring PR per IP (#145 pcs, #146 mac) folds the leaves in, builds the single IP testbench and moves the tests. |
| Q25 | **Structs at interfaces (captain decision, 2026-10-15; firstmate ruling on the epic).** Related signals that cross a module boundary inside an IP, and any multi-field state, are packed structs. They are declared in a package and referenced as `pkg::t_name`, with no import. Types shared between mac and pcs go in the shared package; IP-private types go in `pcie_mac_pkg` or `pcie_pcs_pkg`. Single-meaning signals, and IP top ports that mirror an external spec interface (the PIPE names in the HAS), stay flat `logic`. #146 adds the rule to the RTL BKM and applies it to mac; #145 applies it to pcs. |

## Updates

- **2026-10-15:** Q19 (integration IPs) and Q20 (physical FPGA access is
  separate) added; done criterion 6 changed to match Q20, so the board demo
  ([#89](https://github.com/amichai-bd/pcie-factory/issues/89)) is outside
  M1. Both are copied verbatim from the epic body above. Q19 then gained
  its last clarification (a `<other-ip>_<name>` id must be reached by
  `<other-ip>`'s filelist), mirrored in the epic.
- **2026-10-15:** Two readings confirmed by the firstmate, the captain's
  delegate for HAS sign-off, on the `phy` HAS
  ([#71](https://github.com/amichai-bd/pcie-factory/issues/71),
  [PR #114](https://github.com/amichai-bd/pcie-factory/pull/114)).
  **Q11:** the valid/ready of the upper stream applies to transmit; receive
  is valid-only, because the physical layer cannot stall the link partner
  and flow control lives in the data-link layer. **Q12:** the separate TX
  clock is the core clock `clk_i`, which is also PIPE PCLK; only the RX
  recovered clock is a separate clock
  ([`phy` HAS, Decisions](../arch/phy/HAS.md#decisions)).
- **2026-10-15:** Q21 (our HAS and MAS are the normative protocol spec
  where no official spec is legally available) added, copied verbatim from
  the epic body above. The wiki BKM's
  [citations](../../bkm/wiki.md#citations) rule now requires self-contained
  spec pages, and the [references page](../references.md) lists every
  official source of truth
  ([#117](https://github.com/amichai-bd/pcie-factory/issues/117)).
- **2026-10-15:** Q22, Q23 and Q24 added, copied verbatim from the epic
  body above, and Q25 from the firstmate's ruling on the epic. **Amendment
  to Q24:** the shared package is one file, `src/rtl/common/common_pkg.sv`,
  referenced as `common_pkg::item` (the layout and style checks require
  every file in `src/rtl/common/` to start with `common_`); it may hold
  design-specific shared types and constants, and the IP-private packages
  keep their names, `pcie_mac_pkg` and `pcie_pcs_pkg`
  ([`common_pkg`](../rtl/common/common_pkg.md); #146 created it, #145
  extends it).
- **2026-10-15:** Done criteria 2 and 3 updated to the epic's current
  wording (Q24: one MAS and one DV_PLAN per IP; `phy`, `mac`, `pcs` and the
  `common` library are the only testbenches, no leaf sub-IP folders).
