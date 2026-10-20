# pcie_rc FPGA

`pcie_rc_de2_115` puts `pcie_rc` (`pcie_rc_tl` and `pcie_rc_seq` over
`pcie_link`) on the Terasic DE2-115 as the host board of the board build
(goal item 10, [`rc` HAS, Targets](../../arch/rc/HAS.md#targets)), for the
[FPGA flow](../../../tools/fpga/SPEC.md) under the
[FPGA BKM](../../../bkm/fpga.md). `link` runs at `LOWER` 1, so the
[GPIO adapter](../../arch/gpio/HAS.md) takes the place of the `phy` and its
12 pins go to the expansion header. **This is synthesis evidence only:** no
board is programmed, nothing is brought up, and the resource and timing
figures are for information, not a gate (decision record
[0005](../../decisions/0005-m4-grill.md#decisions), P3; the endpoint board
build is out of M4, OP3). The wrapper is board policy, not part of
`rc.rtl.f`: the flow adds it on top of the IP's filelist.

## Board wrapper

Unlike the [`core` harness](../core/FPGA.md#board-wrapper), which bounds
its data ports with stimulus registers, this wrapper is a real board top:
every `rc` input is a pin, a constant or the reset, and the stack is
observed through the GPIO pins and the sequencer result.

| Port | Board signal | Pin(s) | Use |
| --- | --- | --- | --- |
| `clk_i` | `CLOCK_50` | `Y2` | 50 MHz core clock (`clk_i`), from the board's oscillator. |
| `key_ni[0]` | `KEY[0]` | `M23` | Held: reset (`rst_i`). |
| `key_ni[1]` | `KEY[1]` | `M21` | Held: `start_i`; each press starts a run when none is in progress. |
| `ledr_o[15:0]` | `LEDR[15:0]` | `G19 F19 E19 F21 F18 E18 J19 H19 J17 G17 J15 H16 J16 H17 F15 G15` | `fail_step_o`, registered. |
| `ledg_o[0]` | `LEDG[0]` | `E21` | `done_o`, registered. |
| `ledg_o[1]` | `LEDG[1]` | `E22` | `pass_o`, registered. |
| `ledg_o[2]` | `LEDG[2]` | `E25` | `link_up_o`, registered. |
| `ledg_o[3]` | `LEDG[3]` | `E24` | `dl_up_o`, registered. |
| GPIO pins | `GPIO[11:0]` | see below | The adapter's 12 pins. |

The 12 pins follow the [adapter HAS map](../../arch/gpio/HAS.md#de2-115-pins)
for the downstream board, on the 40-pin expansion header
[cite: Terasic DE2-115 User Manual, rev unverified, §4.8]:

| Port | Header signal | Header pin | FPGA pin |
| --- | --- | --- | --- |
| `gpio_tx_data_o[0]` | `GPIO[0]` | 1 | `AB22` |
| `gpio_tx_data_o[1]` | `GPIO[1]` | 2 | `AC15` |
| `gpio_tx_data_o[2]` | `GPIO[2]` | 3 | `AB21` |
| `gpio_tx_data_o[3]` | `GPIO[3]` | 4 | `Y17` |
| `gpio_tx_ctrl_o` | `GPIO[4]` | 5 | `AC21` |
| `gpio_rx_data_i[0]` | `GPIO[5]` | 6 | `Y16` |
| `gpio_rx_data_i[1]` | `GPIO[6]` | 7 | `AD21` |
| `gpio_rx_data_i[2]` | `GPIO[7]` | 8 | `AE16` |
| `gpio_rx_data_i[3]` | `GPIO[8]` | 9 | `AD15` |
| `gpio_tx_clk_o` | `GPIO[9]` | 10 | `AE15` |
| `gpio_rx_ctrl_i` | `GPIO[10]` | 13 | `AC19` |
| `gpio_rx_clk_i` | `GPIO[11]` | 14 | `AF16` |

- **I/O standard.** The header's I/O voltage follows jumper JP6, 3.3 V by
  default [cite: Terasic DE2-115 User Manual, rev unverified, §4.8], so
  the 12 pins are 3.3-V LVTTL. Another JP6 setting needs the board file
  changed with it.
- **Forwarded clock input.** `gpio_rx_clk_i` enters on `GPIO[11]` (`AF16`),
  where the adapter HAS puts it; the fit routes it as the clock
  `clk_gpio_rx` and meets timing ([results](#results)). Whether `AF16` is
  one of the device's dedicated clock inputs is not checked against the
  manual ([gpio HAS, Open items](../../arch/gpio/HAS.md#open-items)).
- **Pin source.** The FPGA pin of each `GPIO[n]` is cross-checked against
  the DE2-115 GPIO pin table on components101.com
  (<https://components101.com/development-boards/altera-de2-115-development-board>,
  read 2026-10-19; the page gives no revision), a community copy of the
  board's pin list; the manual itself was not reachable from the authoring
  session (same open item).
- **Parameters.** Every `pcie_rc` parameter keeps its default but
  `LOWER`, which is 1: `TIMEOUT_DIV`, `ERR_TIMEOUT_DIV` and
  `CPL_TIMEOUT_DIV` 1, as the [HAS](../../arch/rc/HAS.md#parameters) asks
  for the FPGA build; `PROGRAM` 1 (`MMIO`, enumeration then BAR0 traffic,
  the board build's program); `GPIO_CLK_DIV` 2, so the forwarded clock is
  25 MHz.
- **Tie-offs.** The `phy` symbol interface is unused at `LOWER` 1
  ([`link` lower layer](../../arch/link/HAS.md#lower-layer)): the wrapper
  ties `ser_rx_clk_i`, `ser_rx_data_i` and `ser_tx_rxdet_i` to 0 and
  `ser_rx_elec_idle_i` to 1, and leaves the constant `ser_tx_*` outputs
  unobserved. `dl_state_o`, `dl_err_o`, `ltssm_state_o` and `rcv_err_o`
  have no LED.

Reset is the [`core` harness's](../core/FPGA.md#board-wrapper): the flop
`rst_q`, synchronous to `clk_i`, active high, with no logic between it and
`rst_i`, because `rst_i` also drives the adapter's receive-domain reset
synchronizer and [`common_cdc_rst`](../../rtl/common/cdc/common_cdc_rst.md)
takes that source straight from a flop. Every register powers up at 0 (the
board file disables power-up don't-care), so `rst_q` goes high at the first
`clk_i` edge; a 4-bit power-on counter keeps it high for at least 15
cycles, which meets the [HAS](../../arch/rc/HAS.md#clocks-and-reset)
minimum of 8, and `KEY[0]` keeps it high while pressed. `KEY[0]` and
`KEY[1]` pass through two-flop synchronizers (`COMMON_METAFLOP`, reset
tied low); `start_i` is the registered inverse of `KEY[1]`, so a press is
the rising edge that starts a run ([`rc` HAS, Sequencer run](../../arch/rc/HAS.md#sequencer-run)).
`rst_q` carries the same `SYNCASYNCNET` lint waiver as the `rc` top's
`rst_i` ([`src/lint_waivers.yaml`](../../../../src/lint_waivers.yaml)).

## Constraints

| Constraint | Reason |
| --- | --- |
| `clk_core`: 20 ns on `clk_i` | `clk_i` at 50 MHz, the board oscillator; it runs `rc_tl`, `rc_seq`, the `dll` and the adapter except its receive front end. |
| `clk_gpio_rx`: 40 ns on `gpio_rx_clk_i` | The partner's forwarded clock: a DE2-115 at 50 MHz with `GPIO_CLK_DIV` 2 sends 25 MHz ([gpio HAS, Pins and transfers](../../arch/gpio/HAS.md#pins-and-transfers)). It comes from the partner's oscillator, with no phase relationship to `clk_core`. |
| `derive_clock_uncertainty` | Quartus clock uncertainty for both clocks. |
| `set_max_delay` 20 ns from `clk_core` to `clk_gpio_rx` and from `clk_gpio_rx` to `clk_core` | `rc_tl`, `rc_seq` and the `dll` add no clock and no crossing ([HAS, Clocks and reset](../../arch/rc/HAS.md#clocks-and-reset)), so every path between the domains is a crossing cell in the adapter's receive front end ([gpio MAS, Timing](../../rtl/gpio/MAS.md#timing)): the [`common_cdc_fifo`](../../rtl/common/cdc/common_cdc_fifo.md#timing) Gray pointers and memory read, the [`common_cdc_rst`](../../rtl/common/cdc/common_cdc_rst.md#timing) asynchronous set from `rst_i`, and the [`common_cdc_sync2`](../../rtl/common/cdc/common_cdc_sync2.md#timing) alignment flag. Their MAS pages ask for a max delay of at most one capturing period; 20 ns, the shorter period, meets that both ways. No `set_clock_groups -asynchronous` and no false path: either would let a Gray vector skew past one update. |
| `set_false_path -hold` both ways between `clk_core` and `clk_gpio_rx` | The clocks have no phase relationship, so a hold or removal check across them means nothing; each cell resolves metastability in its own flops. |
| `set_input_delay` 0 ns (max and min) on `gpio_rx_ctrl_i`, `gpio_rx_data_i[*]`, relative to the falling edge of `clk_gpio_rx` | The partner changes them as its forwarded clock falls and the front end captures them as it rises, half a transfer later. 0 ns is that ideal at the header: the cable and board skew are not measured ([gpio HAS, Open items](../../arch/gpio/HAS.md#open-items)), so the check covers the FPGA's own pin-to-register skew only. |
| `set_output_delay` 0 ns (max and min) on `gpio_tx_clk_o`, `gpio_tx_ctrl_o`, `gpio_tx_data_o[*]`, relative to `clk_core` | All six are flop outputs with no logic after the flop, the forwarded clock among them; their relationship at the partner is set by the RTL, so the constraint only bounds each register-to-pin path by one `clk_core` period. |
| `set_false_path -from` `key_ni[*]` | Asynchronous board inputs, each through a two-flop synchronizer. |
| `set_false_path -to` `ledr_o[*]`, `ledg_o[*]` | Static, human-visible outputs. |

The board file holds the Cyclone IV E device, the pins and the I/O
standards: 3.3-V LVTTL for `CLOCK_50` and the header, 2.5 V for the keys
and LEDs [cite: Terasic DE2-115 User Manual, rev unverified, §4].

## Running

Build with `./build fpga --ip rc` on the CI host, or validate the inputs
anywhere with `./build fpga --ip rc --dry-run`. Required CI synthesizes
`rc` whenever its inputs change (`ci-fpga`, `--synth-only`); nightly runs
`./build fpga --all`, which selects every IP with a `src/fpga/<ip>/`
folder. Resource and timing results go to `metrics.json` and, from the
nightly run, to the tracker's `rc` page; the wrapper's own logic (the
power-on counter, `rst_q`, two 2-flop synchronizers, the start flop and
20 LED flops) is included in them.

## Results

For information only (synthesis evidence, no board): the nightly tracker
owns the live figures. This is the fit with the host memory in M9K,
`fpga` workflow run 39373371520 at `09b41c4`
([#417](https://github.com/amichai-bd/pcie-factory/issues/417)), Quartus
Prime Lite 25.1std.0, slow and fast models.

| Item | Result |
| --- | --- |
| `clk_core` (50 MHz) | Met: setup slack +3.813 ns, hold +0.141 ns, Fmax 61.78 MHz. |
| `clk_gpio_rx` (25 MHz) | Met: setup slack +17.366 ns, hold +0.201 ns, recovery +15.008 ns, removal +0.800 ns, Fmax 167.59 MHz. |
| Unconstrained paths | 0. |
| Logic elements | 6440 / 114480. |
| Registers | 2899. |
| Memory | 15 / 432 M9K, 78472 memory bits, 9 RAMs: 7 simple dual port, single clock, old data on a mixed-port read during write; the `rc_tl` host memory, single port, 1024 x 32, 4 M9K; and the `rc_seq` ROM, 256 words narrowed by synthesis to 57 bits, 2 M9K. |
| Pins | 35. |
| Worst setup path | `clk_core`: the `dll` flow-control state (`u_dll` `u_fc` `alloc_q`) to the `dll` TX path (`u_tlp_tx` `rt_q`); slack +3.813 ns. |

**Host memory in M9K.** The `rc_tl` host memory, a 4 KiB
[`common_sync_ram`](../../rtl/common/ram/common_sync_ram.md) with four
byte lanes, maps to 4 M9K in single-port mode through the block's
[Intel backend](../../rtl/common/ram/common_sync_ram.md#intel-backend),
which the FPGA flow selects. In the first fit (`fpga` workflow run
39328627425 at `9114f49`, the portable inferred RAM) Quartus built it from
logic: 35699 registers and 48243 logic elements in all, and its read data
(`u_mem` `u_ram` `rd_data_o`) ended the worst `clk_core` path at slack
+2.488 ns. With the backend the build has 32800 fewer registers and 41803
fewer logic elements.

**Quartus elaboration.** The first runs showed that Quartus Lite rejects a
member select of a struct localparam in a constant expression (error
10742, "constant expression cannot contain a hierarchical identifier"),
which Verilator accepts: the `rc_seq` package's second-pass program builds
and the ROM's and top's parameter checks used it. They now build both
passes inside one function and take `t_prog`'s fields by part-select
([`rc_seq` MAS, Programs in the package](../../rtl/rc_seq/MAS.md#microarchitecture)).
The limit is a row of the [FPGA BKM's known limits](../../../bkm/fpga.md#known-quartus-limits);
its style-check rule is owned by [#418](https://github.com/amichai-bd/pcie-factory/issues/418).
The 32768-bit program images themselves elaborate (map about 4 minutes).

**Quartus warnings.** Fit: 169177 and 292013, both
[known](../../../bkm/fpga.md#known-quartus-warnings). The published log
gives the count of a known ID, not its pins; the 3.3-V LVTTL inputs here
are `CLOCK_50` and the header's `gpio_rx_*` pins, both covered by the
169177 row. Map: 10036 ×1 and
276020 ×1, known (276020 is the `dll` `end_q` array, as in the
[`core` fit](../core/FPGA.md#results)), and three not on the known list:

| ID | What | Why |
| --- | --- | --- |
| 14320 ×4, under 14284 and 14285 | RAM bits synthesized away in `rc_tl`'s non-posted queue (`pcie_rc_tl_cpl` `u_npq`). | Each 8-entry `t_np_ent` carries the request's address bits 15:2 (`adw`, 14 bits), but at `HOST_MEM_ADDR_W` 12 host memory reads only bits 11:2, so the 4 columns of bits 15:12 have no reader. Expected; a larger `HOST_MEM_ADDR_W` keeps them. The same class as the `core` fit's 14320 rows. |

## Files

| File | Holds |
| --- | --- |
| [`pcie_rc_de2_115.sv`](../../../../src/fpga/rc/pcie_rc_de2_115.sv) | DE2-115 board build wrapper (FPGA flow only). |
| [`pcie_rc.sdc`](../../../../src/fpga/rc/pcie_rc.sdc) | FPGA timing constraints. |
| [`de2_115.qsf.tcl`](../../../../src/fpga/rc/de2_115.qsf.tcl) | DE2-115 device, pins and I/O settings. |
