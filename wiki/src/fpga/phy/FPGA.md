# pcie_phy FPGA

`pcie_phy_de2_115` puts `pcie_phy` on the Terasic DE2-115 for the
[FPGA flow](../../../tools/fpga/SPEC.md), following the
[FPGA BKM](../../../bkm/fpga.md). It is a synthesis and timing harness, not
a link: it gives nightly the stack's resources and timing on both clocks at
125 MHz ([`phy` HAS, Targets](../../arch/phy/HAS.md#targets)). The board
loopback demo is [#89](https://github.com/amichai-bd/pcie-factory/issues/89).
The wrapper is board policy, not part of `phy.rtl.f`: the flow adds it on
top of the IP's filelist.

## Board wrapper

| Port | Board signal | Pin(s) | Use |
| --- | --- | --- | --- |
| `clk_125_i` | `SMA_CLKIN` | `AH14` | 125 MHz core clock and PCLK (`clk_i`), from an external source. |
| `rx_clk_i` | `HSMC_CLKIN0` | `AH15` | 125 MHz recovered RX clock (`ser_rx_clk_i`), as a serializer card on the HSMC connector would deliver it. |
| `key0_ni` | `KEY[0]` | `M23` | Held: reset (`rst_i`). |
| `sw_i[0]` | `SW[0]` | `AB28` | `port_role_i`: 0 downstream, 1 upstream. |
| `sw_i[1]` | `SW[1]` | `AC28` | Serial input of the `clk_i` stimulus register. |
| `sw_i[2]` | `SW[2]` | `AC27` | Serial input of the `ser_rx_clk_i` stimulus register. |
| `ledr_o[5:0]` | `LEDR[5:0]` | `G19 F19 E19 F21 F18 E18` | `ltssm_state_o`, registered. |
| `ledg_o[0]` | `LEDG[0]` | `E21` | `link_up_o`, registered. |
| `ledg_o[1]` | `LEDG[1]` | `E22` | Parity of every other `pcie_phy` output, registered. |

The board has pins for few of the stack's 105 port bits, so the wrapper
bounds every `pcie_phy` port with flops on that port's clock, as a real
system would:

- **Inputs.** Each domain has a serial-in shift register fed by one
  synchronized switch: 28 bits on `clk_i` drive the packet transmit stream,
  the link-control requests and `ser_tx_rxdet_i`; 21 bits on `ser_rx_clk_i`
  drive `ser_rx_data_i` and `ser_rx_elec_idle_i`. Every input bit is a
  distinct flop that synthesis cannot treat as a constant, so no part of
  the stack is optimized away. The registers have no reset and start at 0.
- **Outputs.** Every output is registered on `clk_i`. The LTSSM state and
  link-up go to LEDs; the 45 other output bits are folded into one parity
  flop on `LEDG[1]`, so each one is observed. The parity tree starts at
  registers, so it never lengthens a `pcie_phy` path.
- **Parameters.** `TIMEOUT_DIV` and `ERR_TIMEOUT_DIV` are 1, as the
  [HAS](../../arch/phy/HAS.md#parameters) asks for FPGA builds.
- **Not a functional test.** The stimulus does not honour the HAS input
  contracts. `port_role_i` follows a live switch, not only while in reset,
  and the packet stream and link requests are arbitrary bit patterns. The
  LEDs are therefore not a functional result: the harness exists for
  resources and timing only.

Reset is synchronous to `clk_i` and active high. It is the flop `rst_q`,
with no logic between it and `rst_i`, because `rst_i` also drives the pcs RX
reset synchronizer's asynchronous set and
[`common_cdc_rst`](../../rtl/common/cdc/common_cdc_rst.md) takes that source
straight from a flop. Every register powers up at 0, because the board file
disables power-up don't-care. So `rst_q` is low for the first `clk_i` cycle
after configuration, while the stack's registers hold their power-up zeros,
and goes high at the first `clk_125_i` edge. A 4-bit power-on counter
(`por_cnt_q`) then keeps it high for at least 15 cycles, which meets the
[HAS](../../arch/phy/HAS.md#clocks-and-resets) minimum of 8, and `KEY[0]`
keeps it high while pressed. `KEY[0]` and the
switches pass through two-flop synchronizers (`COMMON_METAFLOP` instances
of `common_cdc_sync2`, reset tied low): `SW[2]` to `ser_rx_clk_i`, the
others to `clk_i`.

The wrapper's reset flop `rst_q`, which drives the phy `rst_i`, carries
the same `SYNCASYNCNET` lint waiver as the `phy` top's `rst_i`
([`src/lint_waivers.yaml`](../../../../src/lint_waivers.yaml)). The pcs
RX reset needs none: every consumer takes it asynchronously (#170).

## Constraints

| Constraint | Reason |
| --- | --- |
| `clk_core`: 8 ns on `clk_125_i` | `clk_i` at 125 MHz. |
| `clk_rx`: 8 ns on `rx_clk_i` | `ser_rx_clk_i` at 125 MHz nominal; another oscillator, within 600 ppm of `clk_i`, with no phase relationship. |
| `derive_clock_uncertainty` | Quartus clock uncertainty for both clocks. |
| `set_max_delay` 8 ns from `clk_core` to `clk_rx` and from `clk_rx` to `clk_core` | Every path between the domains is a crossing cell inside `pcie_pcs` ([`pcs` HAS crossings](../../arch/pcs/HAS.md#clock-domain-crossings); [`phy` MAS](../../rtl/phy/MAS.md#timing)): the [`common_cdc_fifo`](../../rtl/common/cdc/common_cdc_fifo.md#timing) Gray pointers and memory read, the [`common_cdc_rst`](../../rtl/common/cdc/common_cdc_rst.md#timing) asynchronous set from `rst_i`, and the [`common_cdc_sync2`](../../rtl/common/cdc/common_cdc_sync2.md#timing) inputs, alone or inside `common_cdc_pulse`. Their MAS pages ask for a max delay of at most one destination period. A false path or an asynchronous clock group is not used: either would let a Gray vector skew past one update. |
| `set_false_path -hold` both ways between `clk_core` and `clk_rx` | The clocks have no phase relationship, so a hold or removal check across them means nothing; each cell resolves metastability in its own flops. |
| `set_false_path -from` `key0_ni`, `sw_i[*]` | Asynchronous board inputs, each through a two-flop synchronizer. |
| `set_false_path -to` `ledr_o[*]`, `ledg_o[*]` | Static, human-visible outputs. |

The board file sets the Cyclone IV E device, pins and I/O standards:
3.3-V LVTTL for both clock inputs (`SMA_CLKIN` is a 3.3 V input,
`HSMC_CLKIN0` follows jumper JP6, 3.3 V by default) and 2.5 V for the key,
switches and LEDs (jumper JP7 in its default position)
[cite: Terasic DE2-115 User Manual, rev unverified, §4].

## Running

Build with `./build fpga --ip phy` on the CI host, or validate the inputs
anywhere with `./build fpga --ip phy --dry-run`. Nightly runs
`./build fpga --all`, which selects every IP with a `src/fpga/<ip>/` folder,
so it compiles `phy` with no further setting. Resource and timing results
are not recorded here but in `metrics.json` and, from the nightly run, the
tracker; the wrapper's own logic (115 flops and the parity tree) is
included in the resource counts.

## Files

| File | Holds |
| --- | --- |
| [`pcie_phy_de2_115.sv`](../../../../src/fpga/phy/pcie_phy_de2_115.sv) | DE2-115 synthesis and timing harness (FPGA flow only). |
| [`pcie_phy.sdc`](../../../../src/fpga/phy/pcie_phy.sdc) | FPGA timing constraints. |
| [`de2_115.qsf.tcl`](../../../../src/fpga/phy/de2_115.qsf.tcl) | DE2-115 device, pins and I/O settings. |
