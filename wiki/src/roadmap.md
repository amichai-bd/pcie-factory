# Design roadmap

The product milestones for the PCI Express controller. The factory's own
milestones are on the [project roadmap](../project/roadmap.md); the target
and prefix are declared in [`src/README.md`](../../src/README.md).

## M1: Gen1 x1 physical layer

**Done:** the M1 epic ([#65](https://github.com/amichai-bd/pcie-factory/issues/65))
closed on 2026-10-17, with ASIC timing closure at 125 MHz deferred
([0002](decisions/0002-m1-grill.md) Q28).

Deliver a Gen1 x1 physical layer as two top-level IPs, `mac` and `pcs`,
joined by PIPE and stacked in a thin `phy` IP, verified to every Gen1 LTSSM
state with FPGA and ASIC evidence. Tracked by the M1 epic
([#65](https://github.com/amichai-bd/pcie-factory/issues/65)); the goal, the
done criteria and the decisions are in
[decision record 0002](decisions/0002-m1-grill.md).

| Wave | Outcome | Issues |
| --- | --- | --- |
| 0, factory | Kind-first layout first; then the design record home, citation check, coverage, CDC cells and check, the shared RTL vocabulary and the open M0 follow-ups | #92, then #66, #67, #68, #69 → #70, #120; #124, #126, #138, #139 |
| 1, HAS | `phy` HAS and PIPE, then the `mac` and `pcs` HAS; each signed off by the captain or the captain's delegate | #71, then #72, #73 |
| 2, leaf RTL | `mac`: scrambler, ordered sets, frame, LTSSM core. `pcs`: 8b/10b encode and decode, alignment, elastic buffer, then the `pcs` top with PIPE control. Finished as planned (Q24) | #74, #75, #76, #77; #81, #82, #83, #84, #85 |
| 2b, restructure | `mac` and `pcs` each become one IP with one testbench through its ports, the shared `common_pkg` and structs at interfaces (Q24, Q25) | #146 (`mac`), #145 (`pcs`) |
| 2c, `mac` states | Power states, then control states, inside the `mac` IP and its testbench | #79 → #80 |
| 3 | `phy` e2e scaffold now (Q23), then the `phy` RTL integration and all-RTL e2e; coverage floors; retiring the seed `top` IP; FPGA and ASIC at the end, on the full `phy` only | #131 → #86; #90; #91; #87, #88 |

M1 is done when the goal checklist in the decision record holds on `main`.
The board loopback demo on physical hardware (#89) needs the captain's
board and is tracked outside M1 ([0002](decisions/0002-m1-grill.md) Q20).
ASIC timing closure at 125 MHz (#235) is also outside M1: the 100 MHz
checkpoint is M1's ASIC timing evidence ([0002](decisions/0002-m1-grill.md) Q27, Q28).

## M2: Data link layer

Add a reliable data link layer as a new top-level IP, `dll`, stacked over
`phy` in a new integration IP, `link`, verified against an independently
written link partner for error recovery, credits and throughput, with FPGA
and ASIC timing evidence. Tracked by the M2 epic
([#203](https://github.com/amichai-bd/pcie-factory/issues/203)), which
started after M1 closed (Q9); the goal, the done criteria and the decisions
are in [decision record 0003](decisions/0003-m2-grill.md).

| Wave | Outcome | Issues |
| --- | --- | --- |
| 0, factory and record | This roadmap and the decision record; CI cancels superseded runs; machine pre-check of PR evidence; nightly per-IP regression trends with drop alerts | #204; #205, #206, #207 |
| 1, specs | `dll` HAS and arch model, then the `link` HAS (each signed off by the captain or the captain's delegate) and the independent DLL link partner | #208, then #209, #210 |
| 2, `dll` RTL | `dll` top, DLLP path and data-link control with the `dll` testbench; then the TX replay path, the RX check and ACK/NAK path, and flow control | #211, then #212, #213, #214 |
| 3, `link` | `link` stack and back-to-back e2e with error injection and credit tests; then throughput, coverage floors, FPGA and ASIC | #215, then #216, #217, #218, #219 |

M2 is done when the goal checklist in the decision record holds on `main`.
The `link` ASIC gate is the 100 MHz checkpoint, with 125 MHz reported
nightly; 125 MHz closure (#235) does not block M2
([0003](decisions/0003-m2-grill.md) Q10).

## M3: Endpoint transaction layer

Turn the Gen1 x1 link into a minimal endpoint a host can enumerate and use:
a transaction layer IP, `tl`, a configuration space IP, `cfg`, and an
integration IP, `core` (`tl` and `cfg` over `link`), exposing a simple TLP
stream to user logic and verified against an independently written host
model, with FPGA and ASIC timing evidence. Tracked by the M3 epic
([#282](https://github.com/amichai-bd/pcie-factory/issues/282)); the goal,
the done criteria and the decisions are in
[decision record 0004](decisions/0004-m3-grill.md). Specs and arch models
run alongside M2's `link` wave; RTL starts after M2 closes (Q6).

| Wave | Outcome | Issues |
| --- | --- | --- |
| 0, record | This roadmap and the decision record | #283 |
| 1, specs and models | `core` HAS with the `tl`↔`cfg` interface; then the `tl` and `cfg` HAS with arch models (each signed off by the captain or the captain's delegate); then the `core` arch model; the independent host model | #284, then #285, #286; then #287; #288 |
| 2, RTL (after M2) | `cfg` with its testbench; `dll` and `link` credit release (Q9); then the `tl` receive path, then transmit path with completions and MSI/MSI-X, then completion tracking and timeout | #289; #303, then #290, then #291, then #292 |
| 3, `core` | `core` stack and back-to-back e2e enumeration and traffic; then coverage floors, FPGA and ASIC | #293, then #294, #295, #296 |

M3 is done when the goal checklist in the decision record holds on `main`.

## M4: Root-port stack and test sequencer

Add a synthesizable root-port stack with no CPU: `link` reused in the
downstream role under a new integration IP, `rc`, which holds the root-port
transaction layer `rc_tl` and the command-ROM test sequencer `rc_seq`
(sub-IPs, OP1). It is verified back to back against the endpoint `core` in
simulation, with the independent host model (#288) as a shadow checker, and
synthesized for the DE2-115 in a board build where a GPIO data-link-boundary
adapter replaces `phy`. M4 is the first milestone towards a two-board host
and endpoint (P1). Tracked by the M4 epic
([#357](https://github.com/amichai-bd/pcie-factory/issues/357)); the goal,
the done criteria and the decisions are in
[decision record 0005](decisions/0005-m4-grill.md). Specs start now; RTL
starts once the `rc` HAS is signed off (F1, reading F1's "its HAS" as the
`rc` HAS; the adapter RTL also waits for #370).

| Wave | Outcome | Issues |
| --- | --- | --- |
| 0, record | This roadmap and the decision record | #366 |
| 1, specs and models | `rc` HAS with the Type 1 header, host memory and downstream `link` contract; then the `rc_tl` and `rc_seq` MAS; the GPIO adapter HAS and the `link` lower-layer parameter (each signed off by the captain or the captain's delegate); then the `rc` arch model and perf scenarios; the host model passive shadow mode | #367, then #368, #369; #370; then #371; #372 |
| 2, RTL (after the `rc` HAS sign-off) | `rc_tl` root-port Type 1 header, then the requester path, then the completer path with MSI capture and receive errors; `rc_seq` interpreter and the M4 programs | #374, then #373, then #375; #376 |
| 3, integration and evidence | `rc` stack and full-phy back-to-back e2e with `core`; the `dll` replay-timeout parameter for slow lower layers; then the GPIO adapter RTL, `link` parameter and board-build e2e; then the `rc` board build synthesis on the DE2-115; coverage floors | #377; #384; then #378, then #379; #380 |

M4 is done when the goal checklist in the decision record holds on `main`.
FPGA timing is reported for information only, and M4 has no ASIC target.

## Later

In order: the firstmate's ordering in the M4 epic, consistent with P1 and
P2, which changes [0004](decisions/0004-m3-grill.md) Q7; the captain may
override it ([0005](decisions/0005-m4-grill.md#waves)):

1. The two-board demonstration over physical GPIO between the DE2-115 and
   the DE10-Lite, with the endpoint board build and DE10-Lite flow support
   (OP3).
2. A CPU or SoC host, firmware and Linux (P2).
3. An AXI bridge IP on top of the `core` TLP stream
   ([0004](decisions/0004-m3-grill.md) Q7).
4. Gen2 and Gen3 rates ([0002](decisions/0002-m1-grill.md) Q9) and wider
   links.

Each is planned here once the milestone before it lands.
