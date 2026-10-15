# pcie_phy MAS

How `pcie_phy` implements its [HAS](../../arch/phy/HAS.md): the `pcie_mac`
and `pcie_pcs` instances joined by the
[PIPE nets](../../arch/phy/PIPE.md#signals), with no logic of its own.

## Microarchitecture

```mermaid
flowchart LR
  up["upper packet stream<br/>link control"] <--> mac["u_mac<br/>pcie_mac"]
  mac <-- "pipe_* nets" --> pcs["u_pcs<br/>pcie_pcs"]
  pcs <--> low["lower symbol interface<br/>clk_i, ser_rx_clk_i"]
```

- **`u_mac` (`pcie_mac`)** takes `clk_i`, `rst_i`, `port_role_i`, the
  upper packet stream, link control and status, and its MAC-to-PCS side of
  PIPE. `TIMEOUT_DIV` and `ERR_TIMEOUT_DIV` pass through. `N_FTS` and
  `L0S_IDLE_CYCLES` keep their `pcie_mac` defaults, 32 and 256.
- **`u_pcs` (`pcie_pcs`)** takes `clk_i`, `rst_i`, its PCS-to-MAC side
  of PIPE, and the whole lower symbol interface, including `ser_rx_clk_i`.
- **PIPE nets.** There is one `logic` net per PIPE signal, named
  `pipe_<x>` after the stack net on the PIPE page. Each is driven by
  `pipe_<x>_o` of one IP and read as `pipe_<x>_i` by the other. They stay
  flat because they mirror an external interface, the PIPE specification
  ([structs](../../../bkm/rtl.md#structs-at-interfaces)).
- **Reset.** `rst_i` goes to both IPs. The MAC also holds the PCS in
  reset through `pipe_reset_n`. The PCS derives the RX-domain reset itself
  (`pcs` HAS, Clocks and reset).

## Timing

`pcie_phy` adds no registers and no combinational logic, so the stack
latency is the sum of the two IPs' latencies
([HAS targets](../../arch/phy/HAS.md#targets)). The e2e measures them
(`e2e_targets`):

| Path | Latency | Where it comes from |
| --- | --- | --- |
| Transmit | 2 cycles, from an accepted `sop` beat to its STP or SDP on `ser_tx_data_o` (the packet's first byte is in `[19:10]` of that word) | The MAC's transmit word is combinational (`mac` MAS, zero inside its budget of 3), and the PCS takes 2 (`pcs` HAS). The target is 6. |
| Receive | 11.8 to 14.3 cycles at +600 ppm with the elastic buffer at its nominal fill | `pcs` receive (at most 12 at a fill of 12 or less), then the MAC's 3, plus 1 when the packet starts in `[15:8]`. The target is 16. |
| Throughput | 1.9788 bytes per cycle for 274-byte TLPs, which is the HAS floor exactly; 1.4956 for DLLPs, against a floor of 1.4949 | One framing cycle per packet (STP or SDP, and END); one SKP ordered set every 1180 symbols. |

The only clock-domain crossings are inside `pcie_pcs`. Every phy input carries a `cdc-domain`
annotation: `ser_rx_data_i` and `ser_rx_elec_idle_i` are on
`ser_rx_clk_i`, every other input on `clk_i`. `ser_rx_elec_idle_i` passes
straight to `pcie_pcs` with the HAS contract: 1 for any word that holds an
idle bit, whose bits before the idle are still line data. The `pcs`
`rst_i` net has a `SYNCASYNCNET` lint waiver seen from the phy top,
mirroring the `pcs` one (`src/lint_waivers.yaml`): it is the synchronous
`clk_i` reset and the RX reset synchronizer's asynchronous input. The RX
reset itself has one consumer style and no waiver (#170).

## Files

| File | Holds |
| --- | --- |
| [`pcie_phy.sv`](../../../../src/rtl/phy/pcie_phy.sv) | The stack top: `u_mac`, `u_pcs` and the PIPE nets. |
| [`phy.rtl.f`](../../../../src/rtl/phy/phy.rtl.f) | `-F ../mac/mac.rtl.f`, then `-F ../pcs/pcs.rtl.f`, then `pcie_phy.sv`. The shared vocabulary and `common_pkg` come in through those two filelists, each flattened once. |
