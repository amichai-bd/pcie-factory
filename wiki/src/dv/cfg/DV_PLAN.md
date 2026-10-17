# pcie_cfg DV plan

> **Draft for wave 2:** only the shell smoke test exists; the register-file
> tests arrive with the RTL
> ([#289](https://github.com/amichai-bd/pcie-factory/issues/289)).

## Strategy

- **Shell test:** [`tests/test_cfg.py`](../../../../src/dv/cfg/tests/test_cfg.py)
  drives `pcie_cfg` directly (no SV testbench; `cfg.dv.f` is empty). With
  `clk_i` running, `rst_i` pulsed twice and every other input toggled at
  random, it checks each qualified and state output against its
  [reset value](../../arch/cfg/HAS.md#reset) at every falling `clk_i` edge.
- **Functional coverage:** the `cfg_shell` covergroup bins `rst` (low,
  high) and `tl` offering a register access, the application an
  interrupt, both or neither, crossed.
- **Planned (wave 2):** `pcie_cfg` alone, driven on the
  [tl-cfg interface](../../arch/core/TL_CFG.md) and the
  [interrupt port](../../arch/core/APP.md#interrupts), checked against a
  reference model written from the HAS: every field of
  [REGISTERS.md](../../arch/cfg/REGISTERS.md) and
  [CAPS.md](../../arch/cfg/CAPS.md) with its access type and byte enables,
  BAR sizing, bus and device capture, the [error](../../arch/cfg/HAS.md#errors)
  table and message decision, the [message](../../arch/cfg/HAS.md#messages)
  priority and withdrawal, the [interrupt](../../arch/cfg/HAS.md#interrupts)
  resolution with masks and pending bits, the
  [power states](../../arch/cfg/HAS.md#power-states), and the latency
  [targets](../../arch/cfg/HAS.md#targets). The arch model may serve as a
  second reference ([arch BKM](../../../bkm/arch.md#dv-and-arch)).

## Plan

| Feature | Entry `name` | Level | Seeds / note |
| --- | --- | --- | --- |
| [Reset values of the shell](../../arch/cfg/HAS.md#reset) | `smoke` | 0 | 1; 128 cycles with random inputs |

Levels 1 and 2 have no entries yet (`tests: []`).

## Gaps

| Feature | Owning issue |
| --- | --- |
| [Register access](../../arch/cfg/HAS.md#register-access), [registers](../../arch/cfg/REGISTERS.md), [capabilities](../../arch/cfg/CAPS.md) | [#289](https://github.com/amichai-bd/pcie-factory/issues/289) |
| [Errors](../../arch/cfg/HAS.md#errors), [messages](../../arch/cfg/HAS.md#messages), [interrupts](../../arch/cfg/HAS.md#interrupts), [power states](../../arch/cfg/HAS.md#power-states) | [#289](https://github.com/amichai-bd/pcie-factory/issues/289) |
| Enumeration end to end against the independent host model | [#293](https://github.com/amichai-bd/pcie-factory/issues/293) |
