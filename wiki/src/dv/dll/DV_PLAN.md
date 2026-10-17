# pcie_dll DV plan

> **Draft for wave 2:** only the shell smoke test exists; the `dll`
> testbench arrives with the `dll` top
> ([#211](https://github.com/amichai-bd/pcie-factory/issues/211)).

## Strategy

- **Shell test:** [`tests/test_dll.py`](../../../../src/dv/dll/tests/test_dll.py)
  drives `pcie_dll` directly (no SV testbench; `dll.dv.f` is empty). With
  `clk_i` running and every input toggled at random, it checks each output
  with a defined [reset value](../../arch/dll/HAS.md#reset-values) at every
  falling `clk_i` edge, through two reset pulses.
- **Functional coverage:** the `dll_shell` covergroup bins `rst` (low,
  high) and `link_up` (down, up), crossed.
- **Independent link partner:** the [partner model](PARTNER.md)
  (`models/pcie_dll_partner.py`, M2 Q3) and its pytest unit tests, run by
  `./build ci unit`, not by a regression entry. It shares no code with the
  architecture model; its possible disagreements with the HAS are listed
  on its page.
- **Planned (wave 2):** one `dll` testbench driving the upper TLP stream
  and a stand-in `phy` packet stream, with the independent DLL link
  partner ([#210](https://github.com/amichai-bd/pcie-factory/issues/210),
  M2 Q3) as the protocol judge and the
  [architecture model](../../arch/dll/HAS.md#architecture-model) as a
  second reference; error injection on the packet stream (corrupted,
  dropped and flagged TLPs and DLLPs) and finite and infinite credit
  configurations.

## Plan

| Feature | Entry `name` | Level | Seeds / note |
| --- | --- | --- | --- |
| [Reset values of the shell](../../arch/dll/HAS.md#reset-values) | `smoke` | 0 | 1; 128 cycles with random inputs |

Levels 1 and 2 have no entries yet (`tests: []`).

## Gaps

| Feature | Owning issue |
| --- | --- |
| [Data link control states](../../arch/dll/HAS.md#data-link-control-states), [DLLPs and CRC-16](../../arch/dll/DLLP.md) | [#211](https://github.com/amichai-bd/pcie-factory/issues/211) |
| [Sequence numbers, LCRC, replay buffer, replay timer, REPLAY_NUM](../../arch/dll/RETRY.md#replay) | [#212](https://github.com/amichai-bd/pcie-factory/issues/212) |
| [Receive checks, duplicates, Ack and Nak scheduling](../../arch/dll/RETRY.md#receiving-tlps) | [#213](https://github.com/amichai-bd/pcie-factory/issues/213) |
| [Flow-control initialisation, accounting, UpdateFC](../../arch/dll/FLOW_CONTROL.md) | [#214](https://github.com/amichai-bd/pcie-factory/issues/214) |
| [Targets](../../arch/dll/HAS.md#targets) end to end | [#215](https://github.com/amichai-bd/pcie-factory/issues/215), [#216](https://github.com/amichai-bd/pcie-factory/issues/216) |
