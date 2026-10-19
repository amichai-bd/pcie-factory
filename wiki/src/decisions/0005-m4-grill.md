# 0005: M4 grill decisions

- **Status:** accepted
- **Source:** captain decisions for M4 (2026-10-19), recorded on the M4 epic
  ([#357](https://github.com/amichai-bd/pcie-factory/issues/357)) in three
  rounds: the pushback P1 to P7
  ([comment](https://github.com/amichai-bd/pcie-factory/issues/357#issuecomment-6266831128)),
  the grill Q1 to Q4
  ([comment](https://github.com/amichai-bd/pcie-factory/issues/357#issuecomment-6267121291))
  and the open points OP1 to OP5, F1 and F2
  ([comment](https://github.com/amichai-bd/pcie-factory/issues/357#issuecomment-6267334990)).
  The goal and the decision table are copied verbatim from the epic body:
  "this epic" refers to the epic. Waves and their issues are on the
  [design roadmap](../roadmap.md#m4-root-port-stack-and-test-sequencer).
- **Numbering:** decision records are numbered once across the repository;
  [0001](../../project/decisions/0001-m0-grill.md) is the factory's M0
  record, [0002](0002-m1-grill.md) is M1's, [0003](0003-m2-grill.md) is M2's
  and [0004](0004-m3-grill.md) is M3's.

## Goal

**Add a synthesizable root-port stack: `link` reused in the downstream role, and a new integration IP `rc` holding the root-port transaction layer `rc_tl` and the command-ROM test sequencer `rc_seq`, with no CPU. Verify it back to back against the existing endpoint `core` in simulation, with the independent host model (#288) as a shadow checker. Give FPGA synthesis evidence for the host top on the DE2-115, in a board build where a GPIO data-link-boundary adapter replaces `phy`.**

This epic is the first milestone of the two-board host and endpoint destination first described here (P1). Physical boards, the board-to-board link, the DE10-Lite, a CPU, firmware and Linux come in later milestones (P2, P3, OP3).

M4 is done when all of the following hold on `main`:

1. **Flow:** every sub-issue is closed through the normal agent flow, with an independent `REVIEW-VERDICT: APPROVE` at the merged head.
2. **Specs:** each of these carries a captain (or delegate) sign-off on its PR: the `rc` HAS, the adapter HAS, and the `rc_tl` and `rc_seq` MAS pages (sub-IP specs). Every new IP and sub-IP has one MAS and one DV_PLAN. Citations pin PCIe Base Spec **rev 3.0**; no spec text, table or figure is reproduced.
3. **Function:** `./build regress --level 0 --level 1 --level 2` passes for `rc`, `rc_tl`, `rc_seq`, the adapter, `core`, `tl`, `cfg`, `link`, `dll`, `phy`, `mac`, `pcs` and `common`.
4. **Enumeration:** in the full-phy back-to-back e2e, `rc_seq`'s programs alone drive the host side. They set up the root port's Type 1 header. Over Type 0 configuration requests, they then:
   - read the endpoint's IDs;
   - walk its capability list;
   - size and assign every BAR;
   - enable memory space and bus mastering;
   - configure MSI.
5. **MMIO:** sequencer-issued memory reads and writes to the endpoint's BAR0 complete with the expected data. This includes reads returned as several completions.
6. **DMA and MSI:** the endpoint's bus-master reads and writes reach the host memory in `rc` correctly. Its MSI writes are captured in the MSI window and checked by `rc_seq`.
7. **Errors and recovery:**
   - `rc_tl` detects and records the following, as its MAS states:
     - Unsupported Request and Completer Abort completions;
     - completion timeout;
     - poisoned and malformed received TLPs;
     - the endpoint's error messages.
   - After a Hot Reset or a link down, `rc_seq` re-enumerates and traffic resumes.
   - Injected link errors recover through replay with no lost TLP.
8. **Oracle:** the host model (#288) runs as a passive shadow and checks both directions of the root-port link. It stays independent of `rc_tl`, `rc_seq` and the `rc` arch model: separate author and no shared code. The endpoint side keeps the existing `core` e2e checks and the `cfg` model shadow.
9. **Board build:** `rc` and `core`, both with the GPIO adapter in place of `phy`, run back to back in simulation and pass the enumeration and MMIO programs.
10. **FPGA:** `./build fpga --ip rc` (board build) passes Quartus synthesis on the DE2-115. Resources are reported; timing is reported for information only.
11. **Coverage:** line, toggle and functional floors for `rc`, `rc_tl`, `rc_seq` and the adapter are set and enforced by the nightly.

## Decisions

| # | Decision |
|---|---|
| P1 | The two-board destination is split into milestones that each ship on their own. M4 is the first. |
| P2 | No CPU at first. A hardware test sequencer drives the host side: enumeration and traffic. CPU, firmware and Linux come later. |
| P3 | No physical boards in the loop for CI. The success criteria are simulation of both the root port and the endpoint, plus FPGA synthesis. |
| P4 | The board-to-board transport crosses at the data-link boundary: DLL packets over a simple GPIO transport. |
| P5 | The root-port RTL runs against the endpoint RTL. The independent host model (#288) is the reference shadow and stays independent of both RTLs. |
| P6 | Planning starts now, in parallel with M3's last items. |
| P7 | This issue is rewritten into the M4 milestone after a short decision round. |
| Q1 | M4 delivers host RTL plus a simulation end-to-end test. |
| Q2 | The host scope is all four: Type 0 configuration enumeration plus BARs; MMIO reads and writes; endpoint DMA plus MSI capture; errors and recovery. |
| Q3 | Root-port stack: `link` is reused in the downstream role, plus a new `rc_tl`. |
| Q4 | Two builds: the full `phy` stack for the simulation back-to-back test, and a board top where a GPIO data-link-boundary adapter replaces `phy`. |
| OP1 | `rc` is the top-level IP, with two sub-IPs: `rc_tl` and the sequencer `rc_seq`. Each has its own MAS, testbench and DV_PLAN. This approves both sub-IPs. |
| OP2 | A new adapter IP, plus a `link` parameter that selects `phy` (the default) or the adapter, so `core` gets the GPIO build too. Pins per direction: 4 data, 1 control and 1 forwarded clock, 12 in total. Each 16-bit beat goes as 4 transfers. |
| OP3 | No endpoint board build in M4. The endpoint's GPIO build is exercised only in the simulated board-build pair. The DE10-Lite comes with the two-board milestone. |
| OP4 | `rc_seq` is a command-ROM interpreter. Its commands: config and memory read and write; expect with a mask; wait for MSI or link-up; Hot Reset; jump; done. New tests are new programs, not new RTL. |
| OP5 | A minimal Type 1 header for the root port now: IDs, Command and Status, bus numbers and the memory window. Host memory follows the default: one internal RAM set by a parameter, with an MSI window. The captain may override this. |
| F1 | M4 specs start now. M4 RTL starts once its HAS is signed off, without waiting for #296's leftover work or #363. |
| F2 | No planned `link` RTL change for the downstream role. The `rc` HAS records the downstream contract with test evidence, and any gap becomes its own issue. The OP2 adapter parameter is the only planned `link` change. |

Notes on the table:

- **P1 to P7, F1 and F2** are captain decisions despite the letters; F here
  is not a firstmate delegate decision as in [0004](0004-m3-grill.md#decisions).
- **F1 reading:** this record reads F1's "its HAS" as the `rc` HAS, so the
  `rc_tl` and `rc_seq` RTL waits for #367's sign-off; the adapter RTL
  ([#378](https://github.com/amichai-bd/pcie-factory/issues/378)) also waits
  for the adapter HAS ([#370](https://github.com/amichai-bd/pcie-factory/issues/370)).
- **OP1 and sub-IPs:** OP1 is the captain approval that
  [0004](0004-m3-grill.md#decisions) Q8 requires for each sub-IP. `rc_tl`
  and `rc_seq` are sub-IPs of `rc` under the
  [layout rule](../../project/layout.md#ips-and-sub-ips): their ids are
  `rc_<name>`, `rc`'s filelist reaches theirs, and each is a substantial,
  independently verified block with its own testbench.
- **OP5 host memory:** the captain did not decide host memory separately;
  it follows the drafted default (one internal RAM, its size a parameter,
  with an MSI window), which the captain may still override.
- **OP2 beat split:** 4 data pins carry a 16-bit beat in 4 transfers; the
  epic states this, the captain comment gives the pin counts.

Not decided here; each point belongs to the named sub-issue:

| Point | Owner |
| --- | --- |
| The downstream `link` contract and its test evidence (F2); host memory size and the MSI window address; Type 1 header fields and reset values | [#367](https://github.com/amichai-bd/pcie-factory/issues/367) `rc` HAS |
| `rc_tl` tags, outstanding requests, max read request size within M2 Q6, and the completion timeout value | [#368](https://github.com/amichai-bd/pcie-factory/issues/368) `rc_tl` MAS |
| `rc_seq` command encoding, ROM size and program format | [#369](https://github.com/amichai-bd/pcie-factory/issues/369) `rc_seq` MAS |
| The adapter's framing and control-pin use, and the `link` parameter's name and values | [#370](https://github.com/amichai-bd/pcie-factory/issues/370) adapter HAS |
| The coverage floor values | [#380](https://github.com/amichai-bd/pcie-factory/issues/380) |

## Carried over from M1 to M3

The epic states that the M1 to M3 rules carry over unchanged. Each rule is
recorded in full in [0002](0002-m1-grill.md#decisions),
[0003](0003-m2-grill.md#decisions) or [0004](0004-m3-grill.md#decisions) and
applies to `rc`, `rc_tl`, `rc_seq` and the adapter as it does to the earlier
IPs:

| Rule | In M4 |
| --- | --- |
| M1 Q5 | The PCIe Base Spec is paraphrased and cited by section, never copied (goal item 2). |
| M1 Q21 | The `rc` and adapter HAS and the `rc_tl` and `rc_seq` MAS are self-contained: every root-port and transport rule the design implements is written out in our own words; citations are for cross-checking only. |
| M1 Q22 | The shared RTL vocabulary and the hard coding-style rules apply to all M4 RTL. |
| M1 Q24, M3 Q8 | One testbench per IP and sub-IP. `rc_tl` and `rc_seq` are sub-IPs approved by OP1 under the [layout rule](../../project/layout.md#ips-and-sub-ips). |
| M1 Q25 | Structs at interfaces inside an IP, declared in a package and referenced as `pkg::t_name`. |
| M2 Q6 | Max payload 128 B, VC0 only, no ECRC, on the root-port side too: M4 reuses the same `link` with no RTL change (F2). Recorded in [0003](0003-m2-grill.md#decisions) Q6. |
| M2 Q3, M3 F9 | An independently written oracle judges the new layer; P5 applies it as the host model (#288) in passive shadow mode (goal item 8). |

M1 Q26's 50 MHz DE2-115 target is not an M4 gate: goal item 10 reports
`rc` timing for information only.

## Waves

From the epic's sub-issue list; the
[roadmap](../roadmap.md#m4-root-port-stack-and-test-sequencer) maps each
wave to its issues.

- **Wave 0, record:** decision record and roadmap.
- **Wave 1, specs and models:** `rc` HAS (root-port stack, Type 1 header, host memory and downstream `link` contract; captain sign-off); then the `rc_tl` and `rc_seq` MAS (captain sign-off); the GPIO adapter HAS and the `link` lower-layer parameter (captain sign-off); the `rc` arch model and perf scenarios; the host model passive shadow mode (independent author).
- **Wave 2, RTL (after the HAS sign-off, F1):** `rc_tl` requester path; then the root-port Type 1 header; then the completer path, MSI capture and receive errors; `rc_seq` interpreter and the M4 programs.
- **Wave 3, integration and evidence:** `rc` stack and full-phy back-to-back e2e with `core`; GPIO adapter RTL, `link` parameter and board-build e2e; `rc` board build synthesis on DE2-115; coverage floors for `rc`, `rc_tl`, `rc_seq` and the adapter.

**Later (not M4),** in this order: the two-board demonstration over physical
GPIO between the DE2-115 and the DE10-Lite (with the endpoint board build and
DE10-Lite flow support, OP3); a CPU or SoC host, firmware and Linux (P2); the
AXI bridge; Gen2, Gen3 and wider links. This order is the firstmate's, in
the epic's Later section, and the captain may override it. It changes
[0004](0004-m3-grill.md#decisions) Q7, which put the AXI bridge next after
M3. It is consistent with P1 (the two-board destination is split into
milestones, M4 first) and P2 (CPU, firmware and Linux come later), but
neither captain answer orders those milestones against the bridge. Gen2,
Gen3 and wider links stay after the bridge, as Q7 had them.

## Updates

None yet.
