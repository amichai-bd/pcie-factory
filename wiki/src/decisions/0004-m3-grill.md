# 0004: M3 grill decisions

- **Status:** accepted
- **Source:** captain grill session for M3 (2026-10-17), recorded in the M3
  epic ([#282](https://github.com/amichai-bd/pcie-factory/issues/282)). The
  goal, the decision tables and the waves are copied verbatim from the epic
  body: "this epic" refers to the epic. Later decisions are listed under
  Updates. Waves and their issues are on the
  [design roadmap](../roadmap.md#m3-endpoint-transaction-layer).
- **Numbering:** decision records are numbered once across the repository;
  [0001](../../project/decisions/0001-m0-grill.md) is the factory's M0
  record, [0002](0002-m1-grill.md) is M1's and [0003](0003-m2-grill.md) is
  M2's.

## Goal

**Turn the Gen1 x1 link into a minimal PCI Express endpoint a host can enumerate and use: a new transaction layer IP `tl`, a configuration space IP `cfg`, and an integration IP `core` (`tl` and `cfg` over `link`), exposing a simple TLP stream to user logic. Verify it against an independently written host model, with FPGA and ASIC timing evidence.**

Specs and arch models start now, alongside M2's `link` wave; M3 RTL starts only after M2 (#203) closes (captain, M3 grill). M3 is done when all of the following hold on `main`:

1. **Flow:** every sub-issue of this epic is closed through the normal agent flow, with an independent `REVIEW-VERDICT: APPROVE` at the merged head.
2. **Specs:** the `core`, `tl` and `cfg` HAS pages carry a captain (or delegate) sign-off on their PR. Each of the three IPs has one MAS and one DV_PLAN. Citations pin PCIe Base Spec **rev 3.0**; no spec text, table or figure is reproduced.
3. **Function:** `./build regress --level 0 --level 1 --level 2` passes for `core`, `tl`, `cfg`, `link`, `dll`, `phy`, `mac`, `pcs` and `common`.
4. **Enumeration:** in the `core` back-to-back e2e, the host model reads the IDs, walks the capability list, sizes and assigns every BAR, enables memory space and bus mastering, configures MSI and MSI-X, and receives an interrupt through each.
5. **Traffic:** host memory reads and writes to the application BAR complete correctly, including reads split into several completions; the application's own memory reads and writes (bus master) complete correctly; Unsupported Request and completion-timeout paths behave as the HAS states.
6. **Errors:** Unsupported Request, malformed TLP, poisoned TLP, unexpected completion and completion timeout are detected, recorded in the status registers, and signalled with error messages when enabled.
7. **Independent oracle:** the `core` DV uses a host (root complex) model written independently of the `tl` and `cfg` arch models (separate author, no shared code).
8. **Physical:** nightly reports met timing for `core` at **50 MHz on the DE2-115** (Quartus, M1 Q26). On sky130hd (ORFS), `core` meets the **100 MHz checkpoint** in study mode (M1 Q27) and reports 125 MHz metrics nightly; 125 MHz closure stays future work (#235).

## Decisions

| # | Decision |
|---|---|
| Q1 | **Role:** the controller is a PCI Express **Endpoint** only (Type 0 configuration header). Root-port support is not in M3. |
| Q2 | **Layout:** new top-level IPs `tl` (transaction layer) and `cfg` (configuration space and capabilities), and a new integration IP `core` stacking `tl` and `cfg` over `link`, mirroring how `phy` and `link` integrate other IPs. Each has one HAS, one MAS, one DV_PLAN and one testbench. |
| Q3 | **Application interface:** a simple valid/ready TLP stream with decoded headers (requests in, completions out, and the reverse for the application as bus master), in the style of the `dll` upper interface. A bus bridge is a later IP. |
| Q4 | **Capabilities:** PCI Express capability, Power Management, MSI and **MSI-X**. No AER in M3. |
| Q5 | **Hardware:** the DE2-115 stays a synthesis and timing target only (it has no transceivers). A transceiver board is revisited once the endpoint works end to end. |
| Q6 | **Sequencing:** M3 specs and arch models start now, alongside M2's `link` wave; M3 RTL starts after M2 closes. |
| Q7 | **After M3:** an **AXI bridge** IP on top of the TLP stream comes next, before Gen2, Gen3 or wider links. |
| Q8 | **Sub-IPs:** allowed case by case with the captain's approval, per the layout rule (a substantial, independently verified block). This lifts M1 Q24's "no sub-IPs" for M3 onwards. |

**Firstmate decisions (captain's delegate, 2026-10-17; the captain may override any):**

| # | Decision |
|---|---|
| F1 | **Completer scope:** memory read and write (32- and 64-bit addresses) and Type 0 configuration read and write are served. I/O, locked and Type 1 configuration requests get an Unsupported Request completion. Received messages: the power-management and Vendor_Defined Type 1 messages the spec requires an endpoint to accept are handled or silently dropped as the `tl` HAS states; any other message is an Unsupported Request. |
| F2 | **Requester scope:** the application issues memory reads and writes; `tl` issues MSI and MSI-X writes. Up to **8 outstanding non-posted requests** (tags 0 to 7, extended tags off). Max payload and max read request are **128 B** (M2 Q6); read completions split on a 64 B read completion boundary. |
| F3 | **BARs:** BAR0 is the application's 32-bit non-prefetchable memory window (size a parameter); **BAR2 holds the MSI-X table and pending-bit array**, so the application window stays clean. |
| F4 | **Interrupts:** MSI with 1 vector and 64-bit addresses; MSI-X with a table size parameter (default 8), its table in a `common` RAM. No legacy INTx (Interrupt Pin 0). |
| F5 | **Power:** the Power Management capability supports D0 and D3hot only, with no PME. Link power states stay as the `mac` HAS defines them. |
| F6 | **Errors:** baseline (non-AER) reporting: Device Status bits and ERR_COR, ERR_NONFATAL and ERR_FATAL messages gated by Device Control and the Command register's SERR# enable. |
| F7 | **IDs:** Vendor, Device, Subsystem, Class Code and Revision IDs are parameters. Their defaults are placeholders that claim no real assigned vendor, and the `cfg` HAS says so. |
| F8 | **Partition:** the `core` HAS is written first and owns the `tl`↔`cfg` interface page; the `tl` and `cfg` HAS then follow it in parallel. |
| F9 | **Oracle:** an independently written host model (mirroring M2 Q3) judges `core` and `tl` behaviour in DV. |
| F10 | **Shells:** like M2's wave 1, each spec PR adds the IP's port shell (ports per the HAS, outputs tied to reset values, no logic), the MAS and DV_PLAN stubs and a smoke test, so the page set and checks hold. |

## Carried over from M1 and M2

The epic states that M1 and M2 rules carry over unchanged, except that Q8
replaces M1 Q24's "no sub-IPs". Each rule is recorded in full in
[0002](0002-m1-grill.md#decisions) or [0003](0003-m2-grill.md#decisions) and
applies to `core`, `tl` and `cfg` as it does to the earlier IPs:

| Rule | In M3 |
| --- | --- |
| M1 Q5 | The PCIe Base Spec is paraphrased and cited by section, never copied (goal item 2). |
| M1 Q21 | The `core`, `tl` and `cfg` HAS and MAS are self-contained: every transaction-layer and configuration rule the design implements is written out in our own words; citations are for cross-checking only. |
| M1 Q22 | The shared RTL vocabulary and the hard coding-style rules apply to all `core`, `tl` and `cfg` RTL. |
| M1 Q24 | One testbench per IP still holds. Its "no sub-IPs" part is replaced by Q8: a sub-IP is allowed when the [layout rule](../../project/layout.md#ips-and-sub-ips) holds (a substantial, independently verified block) and the captain approves it. |
| M1 Q25 | Structs at interfaces inside an IP, declared in a package and referenced as `pkg::t_name`. |
| M1 Q26 | The FPGA (DE2-115) timing target for `core` is 50 MHz (goal item 8). |
| M1 Q27, Q28 | The 100 MHz study checkpoint is M3's ASIC gate for `core`; 125 MHz stays the default nightly target, and its closure stays future work ([#235](https://github.com/amichai-bd/pcie-factory/issues/235)). |
| M2 Q3 | An independently written oracle judges the new layer; F9 applies it as the host model. |
| M2 Q6 | Max payload 128 B and VC0 only; F2 adds max read request 128 B. |

## Waves

Copied verbatim from the epic; the
[roadmap](../roadmap.md#m3-endpoint-transaction-layer) maps each wave to its
issues.

- **Wave 0, record:** decision record and roadmap.
- **Wave 1, specs and models:** `core` HAS with the `tl`↔`cfg` interface (captain sign-off); then the `tl` and `cfg` HAS with arch models (captain sign-off); then the `core` arch model; the independent host model.
- **Wave 2, RTL (after M2 closes):** `cfg` with its testbench; `tl` receive path; `tl` transmit path with completions and MSI/MSI-X; `tl` completion tracking and timeout.
- **Wave 3, `core`:** stack and back-to-back e2e enumeration and traffic; coverage floors; FPGA and ASIC.

## Updates

**2026-10-18, captain decisions** on the `core` HAS open questions (PR
[#301](https://github.com/amichai-bd/pcie-factory/pull/301) for
[#284](https://github.com/amichai-bd/pcie-factory/issues/284)), copied
verbatim from the epic
([#282](https://github.com/amichai-bd/pcie-factory/issues/282)):

| # | Decision |
|---|---|
| Q9 | **Credit release:** `dll` and `link` gain a credit-release input; `tl` queues posted and non-posted requests sized by the advertised credits and releases them when a request leaves `tl`. Amends M2 Q5 ("unchanged"). Tracked by #303. |
| Q10 | **Reset:** while the data link is down (including Hot Reset), `tl` and `cfg`, config space included, are held in reset. No function-level reset in M3. |
| Q11 | **D3hot and MSI-X:** memory requests in D3hot get Unsupported Request and the function issues no requests or interrupts outside D0. An MSI-X request while bus mastering is off sets its pending bit and is sent once allowed; no interrupt is lost. |
| Q12 | **Vendor ID default:** 0x0000 placeholder, which claims no vendor; users override the ID parameters. |

The other open questions of that draft (throughput target,
application-interface details, citations unchecked) stay as the author
proposed, as firstmate decisions the captain may override.

**2026-10-19, captain direction on ASIC work** (on
[#282](https://github.com/amichai-bd/pcie-factory/issues/282#issuecomment-6266206427)
and [#296](https://github.com/amichai-bd/pcie-factory/issues/296#issuecomment-6266206778)),
after the WSL host dropped three times under ASIC load. It overrides goal
item 8 and the M1 Q27, Q28 row above:

- ASIC place-and-route and timing are a later target, taken up once the RTL
  functionality is done or nearly done. The ASIC 100 MHz checkpoint for
  `core` leaves M3 for that later milestone, tracked by
  [#363](https://github.com/amichai-bd/pcie-factory/issues/363).
- [#296](https://github.com/amichai-bd/pcie-factory/issues/296) delivers the
  `core` ASIC inputs (SDC, power grid, flow configuration, the shared RAM
  macro selection) with synthesis-only evidence; its global-route figures
  are interim notes.
- [#319](https://github.com/amichai-bd/pcie-factory/issues/319),
  [#320](https://github.com/amichai-bd/pcie-factory/issues/320) and
  [#235](https://github.com/amichai-bd/pcie-factory/issues/235) (ASIC timing
  and power closure) wait for the same later target.
- CI ([#362](https://github.com/amichai-bd/pcie-factory/issues/362#issuecomment-6266351123)):
  PR CI runs Quartus synthesis only and no ASIC job; the nightly runs the
  full Quartus flow and the ASIC synthesis-only check, with ASIC
  place-and-route paused.

**2026-10-19:** the order after M3 in Q7 is changed by the M4 epic's Later
section; see [0005](0005-m4-grill.md#waves).
