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

## Later

The transaction layer (above the `dll` upper interface,
[0003](decisions/0003-m2-grill.md) Q1 and Q5), then Gen2 and Gen3 rates
([0002](decisions/0002-m1-grill.md) Q9). They are planned here once M2
lands.
