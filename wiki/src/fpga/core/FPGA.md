# pcie_core FPGA

`pcie_core_de2_115` puts `pcie_core` (`pcie_tl` and `pcie_cfg` over
`pcie_link`) on the Terasic DE2-115 for the
[FPGA flow](../../../tools/fpga/SPEC.md), following the
[FPGA BKM](../../../bkm/fpga.md) and the [`link` harness](../link/FPGA.md).
It is a synthesis and timing harness, not an endpoint: it gives nightly the
endpoint's resources and timing on both clocks at 50 MHz
([`core` HAS, Targets](../../arch/core/HAS.md#targets)). 50 MHz is the FPGA
timing and resource target only (captain decision Q26 in the
[M1 decision record](../../decisions/0002-m1-grill.md)): there is no board
bring-up (Q5), the ASIC clock target is separate (same HAS table) and the
design's rates are unchanged. The wrapper is board policy, not part of
`core.rtl.f`: the flow adds it on top of the IP's filelist.

## Board wrapper

The pins are the `link` harness's, less `SW[2]`: `core` ties the port role
to upstream, so no switch drives it.

| Port | Board signal | Pin(s) | Use |
| --- | --- | --- | --- |
| `clk_i` | `CLOCK_50` | `Y2` | 50 MHz core clock (`clk_i`), from the board's oscillator. |
| `rx_clk_i` | `HSMC_CLKIN0` | `AH15` | 50 MHz recovered RX clock (`ser_rx_clk_i`), from an independent source off the board. |
| `key0_ni` | `KEY[0]` | `M23` | Held: reset (`rst_i`). |
| `sw_i[0]` | `SW[0]` | `AB28` | Serial input of the `clk_i` stimulus register. |
| `sw_i[1]` | `SW[1]` | `AC28` | Serial input of the `ser_rx_clk_i` stimulus register. |
| `ledr_o[5:0]` | `LEDR[5:0]` | `G19 F19 E19 F21 F18 E18` | `ltssm_state_o`, registered. |
| `ledg_o[0]` | `LEDG[0]` | `E21` | `link_up_o`, registered. |
| `ledg_o[1]` | `LEDG[1]` | `E22` | Parity of every other `pcie_core` output, registered. |

The clock sources and why the RX clock cannot be one of the board's
50 MHz pins are as on the [`phy` page](../phy/FPGA.md#board-wrapper): the
board's three 50 MHz pins are one oscillator, so the RX clock stays on
`HSMC_CLKIN0`, driven from off the board, and the two clocks stay
asynchronous [cite: Terasic DE2-115 User Manual, rev unverified, §4.5]
[cite: Terasic DE2-115 User Manual, rev unverified, §4.7].

The board has pins for few of the endpoint's 355 port bits, so the wrapper
bounds every `pcie_core` port with flops on that port's clock:

- **Inputs.** Each domain has a serial-in shift register fed by one
  synchronized switch: 165 bits on `clk_i` drive `cq_ready_i`, the `cc`
  stream (38 bits), the `rq` stream (117 bits), `rc_ready_i`, the interrupt
  request (7 bits) and `ser_tx_rxdet_i`; 21 bits on `ser_rx_clk_i` drive
  `ser_rx_data_i` and `ser_rx_elec_idle_i`. Every input bit is a distinct
  flop that synthesis cannot treat as a constant, so no part of the stack
  is optimized away. The registers have no reset and start at 0.
- **Outputs.** Every output is registered on `clk_i`. The LTSSM state and
  link-up go to LEDs; the 159 other output bits (the `cq` and `rc`
  streams, the `cc` and `rq` ready, the interrupt status, the function
  status, the data link status and errors, `rcv_err_o` and the transmit
  symbols) are folded into one parity flop on `LEDG[1]`, so each one is
  observed. The parity tree starts at registers, so it never lengthens a
  `pcie_core` path.
- **Parameters.** Every `pcie_core` parameter keeps its default:
  `TIMEOUT_DIV`, `ERR_TIMEOUT_DIV` and `CPL_TIMEOUT_DIV` are 1, as the
  [HAS](../../arch/core/HAS.md#parameters) asks for FPGA builds;
  `BAR0_ADDR_W` is 12 and `MSIX_TABLE_SIZE` 8.
- **Not a functional test.** The stimulus does not honour the
  [application contracts](../../arch/core/APP.md): the streams and the
  interrupt request are arbitrary bit patterns. The LEDs are therefore not
  a functional result: the harness exists for resources and timing only.

Reset is the `link` harness's: the flop `rst_q`, synchronous to `clk_i`,
active high, with no logic between it and `rst_i`, because `rst_i` also
drives the pcs RX reset synchronizer's asynchronous set and
[`common_cdc_rst`](../../rtl/common/cdc/common_cdc_rst.md) takes that
source straight from a flop. Every register powers up at 0 (the board file
disables power-up don't-care), so `rst_q` goes high at the first `clk_i`
edge; a 4-bit power-on counter keeps it high for at least 15 cycles, which
meets the [HAS](../../arch/core/HAS.md#reset) minimum of 8, and `KEY[0]`
keeps it high while pressed. `KEY[0]` and the switches pass through
two-flop synchronizers (`COMMON_METAFLOP`, reset tied low): `SW[1]` to
`ser_rx_clk_i`, the others to `clk_i`. `rst_q` carries the same
`SYNCASYNCNET` lint waiver as the `core` top's `rst_i`
([`src/lint_waivers.yaml`](../../../../src/lint_waivers.yaml)).

## Constraints

| Constraint | Reason |
| --- | --- |
| `clk_core`: 20 ns on `clk_i` | `clk_i` at the 50 MHz FPGA target; it runs `tl`, `cfg`, the `dll` and the `phy` except the PCS receive front end. |
| `clk_rx`: 20 ns on `rx_clk_i` | `ser_rx_clk_i` at 50 MHz nominal; an independent source, within 600 ppm of `clk_i`, with no phase relationship. |
| `derive_clock_uncertainty` | Quartus clock uncertainty for both clocks. |
| `set_max_delay` 20 ns (one destination period) from `clk_core` to `clk_rx` and from `clk_rx` to `clk_core` | `tl`, `cfg` and the `dll` add no clock and no crossing ([HAS, Clocks](../../arch/core/HAS.md#clocks)), so every path between the domains is a crossing cell inside `pcie_pcs`, the same set as in the [`link` constraints](../link/FPGA.md#constraints): the [`common_cdc_fifo`](../../rtl/common/cdc/common_cdc_fifo.md#timing) Gray pointers and memory read, the [`common_cdc_rst`](../../rtl/common/cdc/common_cdc_rst.md#timing) asynchronous set from `rst_i`, and the [`common_cdc_sync2`](../../rtl/common/cdc/common_cdc_sync2.md#timing) inputs, alone or inside `common_cdc_pulse`. Their MAS pages ask for a max delay of at most one destination period; a false path or an asynchronous clock group would let a Gray vector skew past one update. |
| `set_false_path -hold` both ways between `clk_core` and `clk_rx` | The clocks have no phase relationship, so a hold or removal check across them means nothing; each cell resolves metastability in its own flops. |
| `set_false_path -from` `key0_ni`, `sw_i[*]` | Asynchronous board inputs, each through a two-flop synchronizer. |
| `set_false_path -to` `ledr_o[*]`, `ledg_o[*]` | Static, human-visible outputs. |

The board file is the `link` one less `SW[2]`: the Cyclone IV E device,
pins and I/O standards, 3.3-V LVTTL for both clock inputs and 2.5 V for
the key, switches and LEDs [cite: Terasic DE2-115 User Manual, rev unverified, §4].

## Running

Build with `./build fpga --ip core` on the CI host, or validate the inputs
anywhere with `./build fpga --ip core --dry-run`. Nightly runs
`./build fpga --all`, which selects every IP with a `src/fpga/<ip>/` folder,
so it compiles `core` with no further setting. Resource and timing results
are not recorded here but in `metrics.json` and, from the nightly run, on
the tracker's `core` page; the wrapper's own logic (364 flops: 186
stimulus, 166 registered outputs, the parity flop, `rst_q`, the 4-bit
power-on counter and three 2-flop synchronizers; and the parity tree) is
included in the resource counts.

## Results

The nightly tracker owns the live figures. This is the acceptance fit:
`fpga` workflow run 39142366219 at `7fb17ff`, Quartus Prime Lite
25.1std.0, slow and fast models. The PR's head has the same FPGA inputs;
`ci-gate` run 39144100875 (job `ci-fpga`, at `97bfae4`) reproduces every figure
below.

| Item | Result |
| --- | --- |
| `clk_core` (50 MHz target) | Met: setup slack +1.065 ns, hold +0.102 ns, Fmax 52.81 MHz. |
| `clk_rx` (50 MHz target) | Met: setup slack +4.005 ns, hold +0.181 ns, Fmax 62.52 MHz. |
| Unconstrained paths | 0. |
| Logic elements | 13300 / 114480. |
| Registers | 5467. |
| Memory | 28 / 432 M9K, 48848 memory bits, 16 RAMs, each simple dual port, single clock, old data on a mixed-port read during write. |
| Worst setup path | `clk_core`, inside `tl`: the `cfg` completion former's descriptor queue head (`u_cf` `u_desc`, its RAM read register) to the `cc` former's descriptor read pointer (`u_cc` `u_desc` `rd_q`), through the transmit arbiter's eligibility and round robin; slack +1.065 ns. |

**Closing 50 MHz.** The first fit (run 39141831879, at `9d9edbc`)
reached 36.12 MHz on `clk_core` (setup slack -7.684 ns; `clk_rx`
59.17 MHz): its worst path ran from the same descriptor queue head
through the transmit arbiter and `tlp_tx_data` into the `dll` transmit
CRC (`u_dll` `u_tlp_tx` `st_q.crc`) in one cycle, 27.7 ns of data delay.
`tl`'s registered transmit output stage
([`tl` MAS, transmit arbiter](../../rtl/tl/MAS.md#transmit-arbiter)) cut
that path at `tl`'s edge, which closed both clocks in one round. Before
that fit, Quartus rejected a package-constant index inside a port
connection in `pcie_tl` (error 10162); the select is now a named net
([#347](https://github.com/amichai-bd/pcie-factory/issues/347) adds the
matching check).

**Quartus warnings.** Fit: 169177 and 292013, both
[known](../../../bkm/fpga.md#known-quartus-warnings). Map: 10036 ×7 and
276020 ×1, known (276020 is the `dll` `end_q` array, as in the
[`link` fit](../link/FPGA.md#results)), and three not on the known list:

| ID | What | Why |
| --- | --- | --- |
| 14320 ×36, under 14284 and 14285 | RAM bits synthesized away, the first in the `cfg` MSI-X table (`pcie_cfg_bar2` `u_table`). | The memory bits used (48848) are 288 below the RAMs' declared sizes, so the 36 nodes are 36 one-bit columns of the 8-entry RAMs, columns no logic reads with these parameters: the table's Message Address bits 1:0 (2), which the read forces to 0 ([`cfg` MAS, BAR2](../../rtl/cfg/MAS.md#bar2)); in `tl`'s posted queue (`u_pq`, `t_pq_ent`) and cq line (`u_npq_cq`, `t_cq_ent`), address bits 27:12 of each entry (16 each), above `BAR0_ADDR_W` 12, sized for its maximum of 28 (the consumers read bits 11:2); and in the cfg line (`u_npq_cfg`, `t_cfg_ent`), `addr_lo` bits 1:0 (2), which reach only `cpl_rec.addr[1:0]`, a field `pcie_tl_cpl` never reads (the Lower Address comes from bits 6:2 and the first byte enables). Each was found by comparing the fields written into each 8-entry RAM with those read at the default parameters; 2 + 16 + 16 + 2 is the 36. Expected; another `BAR0_ADDR_W` keeps more columns. |
| 10037 ×1 | `pcie_tl_trk.sv` line 250: a conditional expression evaluates to a constant. | The tag array is a `COMMON_DFF_ARRAY_RST_EN` with its enable tied to 1 (the vocabulary has no reset array without an enable), so the macro's enable test is constant. No logic is lost. |

## Files

| File | Holds |
| --- | --- |
| [`pcie_core_de2_115.sv`](../../../../src/fpga/core/pcie_core_de2_115.sv) | DE2-115 synthesis and timing harness (FPGA flow only). |
| [`pcie_core.sdc`](../../../../src/fpga/core/pcie_core.sdc) | FPGA timing constraints. |
| [`de2_115.qsf.tcl`](../../../../src/fpga/core/de2_115.qsf.tcl) | DE2-115 device, pins and I/O settings. |
