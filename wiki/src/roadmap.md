# Design roadmap

The product milestones for the PCI Express controller. The factory's own
milestones are on the [project roadmap](../project/roadmap.md); the target
and prefix are declared in [`src/README.md`](../../src/README.md).

## M1: Gen1 x1 physical layer

Deliver a Gen1 x1 physical layer as two top-level IPs, `mac` and `pcs`,
joined by PIPE and stacked in a thin `phy` IP, verified to every Gen1 LTSSM
state with FPGA and ASIC evidence. Tracked by the M1 epic
([#65](https://github.com/amichai-bd/pcie-factory/issues/65)); the goal, the
done criteria and the decisions are in
[decision record 0002](decisions/0002-m1-grill.md).

| Wave | Outcome | Issues |
| --- | --- | --- |
| 0, factory | Kind-first layout first; then the design record home, citation check, coverage, CDC cells and check, and the open M0 follow-ups | #92, then #66, #67, #68, #69 → #70; #27, #39, #44, #47, #50, #62, #63 |
| 1, HAS | `phy` HAS and PIPE (after #66 and #67), then the `mac` and `pcs` HAS; each signed off by the captain | #71, then #72, #73 |
| 2, `mac` | Scrambler and ordered sets, then framing and the LTSSM core, then the `mac` top with the link partner, then power and control states | #74, #75 → #76, #77 → #78 → #79, #80 |
| 2, `pcs` | 8b/10b encode and decode, then alignment; elastic buffer; then the `pcs` top with PIPE control and the channel model | #81, #82 → #83; #84 → #85 |
| 3 | `phy` e2e, then FPGA and ASIC, then the board demo (programmed by the captain), coverage floors and retiring the seed `top` IP | #86 → #87, #88 → #89, #90, #91 |

M1 is done when the goal checklist in the decision record holds on `main`.

## Later

Data-link and transaction layers, then Gen2 and Gen3 rates
([0002](decisions/0002-m1-grill.md) Q1 and Q9). They are planned here once
M1 lands.
