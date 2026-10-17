# pcie_link FPGA

`pcie_link_de2_115` puts `pcie_link` (`pcie_dll` over `pcie_phy`) on the
Terasic DE2-115 for the [FPGA flow](../../../tools/fpga/SPEC.md), following
the [FPGA BKM](../../../bkm/fpga.md) and the
[`phy` harness](../phy/FPGA.md). It is a synthesis and timing harness, not
a link: it gives nightly the stack's resources and timing on both clocks at
50 MHz ([`link` HAS, Targets](../../arch/link/HAS.md#targets)). 50 MHz is
the FPGA timing and resource target only (captain decision Q26 in the
[M1 decision record](../../decisions/0002-m1-grill.md), carried into M2 by
the [M2 record](../../decisions/0003-m2-grill.md)): there is no board
bring-up (M2 Q2), the ASIC clock target is separate (same HAS table) and
the design's rates are unchanged. The wrapper is board policy, not part
of `link.rtl.f`: the flow adds it on top of the IP's filelist.

## Board wrapper

The pins are the `phy` harness's, with the same meaning:

| Port | Board signal | Pin(s) | Use |
| --- | --- | --- | --- |
| `clk_i` | `CLOCK_50` | `Y2` | 50 MHz core clock (`clk_i`), from the board's oscillator. |
| `rx_clk_i` | `HSMC_CLKIN0` | `AH15` | 50 MHz recovered RX clock (`ser_rx_clk_i`), from an independent source off the board. |
| `key0_ni` | `KEY[0]` | `M23` | Held: reset (`rst_i`). |
| `sw_i[0]` | `SW[0]` | `AB28` | `port_role_i`: 0 downstream, 1 upstream. |
| `sw_i[1]` | `SW[1]` | `AC28` | Serial input of the `clk_i` stimulus register. |
| `sw_i[2]` | `SW[2]` | `AC27` | Serial input of the `ser_rx_clk_i` stimulus register. |
| `ledr_o[5:0]` | `LEDR[5:0]` | `G19 F19 E19 F21 F18 E18` | `ltssm_state_o`, registered. |
| `ledg_o[0]` | `LEDG[0]` | `E21` | `link_up_o`, registered. |
| `ledg_o[1]` | `LEDG[1]` | `E22` | Parity of every other `pcie_link` output, registered. |

The clock sources and why the RX clock cannot be one of the board's
50 MHz pins are as on the [`phy` page](../phy/FPGA.md#board-wrapper): the
board's three 50 MHz pins are one oscillator, so the RX clock stays on
`HSMC_CLKIN0`, driven from off the board, and the two clocks stay
asynchronous [cite: Terasic DE2-115 User Manual, rev unverified, §4.5]
[cite: Terasic DE2-115 User Manual, rev unverified, §4.7].

The board has pins for few of the stack's 201 port bits, so the wrapper
bounds every `pcie_link` port with flops on that port's clock:

- **Inputs.** Each domain has a serial-in shift register fed by one
  synchronized switch: 41 bits on `clk_i` drive the upper TLP transmit
  stream (valid, 32 data bits, `sop`, `eop`), `tlp_rx_ready_i`, the four
  link-control requests and `ser_tx_rxdet_i`; 21 bits on `ser_rx_clk_i`
  drive `ser_rx_data_i` and `ser_rx_elec_idle_i`. Every input bit is a
  distinct flop that synthesis cannot treat as a constant, so no part of
  the stack is optimized away. The registers have no reset and start at 0.
- **Outputs.** Every output is registered on `clk_i`. The LTSSM state and
  link-up go to LEDs; the 128 other output bits (the upper TLP stream, the
  six credit counts, the data link status and errors, `rcv_err_o` and the
  transmit symbols) are folded into one parity flop on `LEDG[1]`, so each
  one is observed. The parity tree starts at registers, so it never
  lengthens a `pcie_link` path.
- **Parameters.** The credit parameters keep their defaults;
  `TIMEOUT_DIV` and `ERR_TIMEOUT_DIV` are 1, as the
  [HAS](../../arch/link/HAS.md#parameters) asks for FPGA builds.
- **Not a functional test.** The stimulus does not honour the HAS input
  contracts: `port_role_i` follows a live switch, and the TLP stream and
  link requests are arbitrary bit patterns. The LEDs are therefore not a
  functional result: the harness exists for resources and timing only.

Reset is the `phy` harness's: the flop `rst_q`, synchronous to `clk_i`,
active high, with no logic between it and `rst_i`, because `rst_i` also
drives the pcs RX reset synchronizer's asynchronous set and
[`common_cdc_rst`](../../rtl/common/cdc/common_cdc_rst.md) takes that
source straight from a flop. Every register powers up at 0 (the board file
disables power-up don't-care), so `rst_q` goes high at the first `clk_i`
edge; a 4-bit power-on counter keeps it high for at least 15 cycles, which
meets the [HAS](../../arch/link/HAS.md#clocks-and-resets) minimum of 8, and
`KEY[0]` keeps it high while pressed. `KEY[0]` and the switches pass
through two-flop synchronizers (`COMMON_METAFLOP`, reset tied low):
`SW[2]` to `ser_rx_clk_i`, the others to `clk_i`. `rst_q` carries the same
`SYNCASYNCNET` lint waiver as the `link` top's `rst_i`
([`src/lint_waivers.yaml`](../../../../src/lint_waivers.yaml)).

## Constraints

| Constraint | Reason |
| --- | --- |
| `clk_core`: 20 ns on `clk_i` | `clk_i` at the 50 MHz FPGA target; it runs all of the `dll` and the `phy` except the PCS receive front end. |
| `clk_rx`: 20 ns on `rx_clk_i` | `ser_rx_clk_i` at 50 MHz nominal; an independent source, within 600 ppm of `clk_i`, with no phase relationship. |
| `derive_clock_uncertainty` | Quartus clock uncertainty for both clocks. |
| `set_max_delay` 20 ns (one destination period) from `clk_core` to `clk_rx` and from `clk_rx` to `clk_core` | The `dll` adds no clock and no crossing ([HAS, Clocks and resets](../../arch/link/HAS.md#clocks-and-resets)), so every path between the domains is a crossing cell inside `pcie_pcs`, the same set as in the [`phy` constraints](../phy/FPGA.md#constraints): the [`common_cdc_fifo`](../../rtl/common/cdc/common_cdc_fifo.md#timing) Gray pointers and memory read, the [`common_cdc_rst`](../../rtl/common/cdc/common_cdc_rst.md#timing) asynchronous set from `rst_i`, and the [`common_cdc_sync2`](../../rtl/common/cdc/common_cdc_sync2.md#timing) inputs, alone or inside `common_cdc_pulse`. Their MAS pages ask for a max delay of at most one destination period; a false path or an asynchronous clock group would let a Gray vector skew past one update. |
| `set_false_path -hold` both ways between `clk_core` and `clk_rx` | The clocks have no phase relationship, so a hold or removal check across them means nothing; each cell resolves metastability in its own flops. |
| `set_false_path -from` `key0_ni`, `sw_i[*]` | Asynchronous board inputs, each through a two-flop synchronizer. |
| `set_false_path -to` `ledr_o[*]`, `ledg_o[*]` | Static, human-visible outputs. |

The board file is the `phy` one: the Cyclone IV E device, pins and I/O
standards, 3.3-V LVTTL for both clock inputs and 2.5 V for the key,
switches and LEDs [cite: Terasic DE2-115 User Manual, rev unverified, §4].

**The `dll` combinational path.** `ltssm_state_o` → `pkt_tx_valid` → `phy`
([`link` MAS, Timing](../../rtl/link/MAS.md#timing)) runs from the `mac`
LTSSM state register's decode through the `dll` replay-rollover logic and
back into the `mac`'s packet-stream registers in one `clk_core` cycle. It
is an ordinary `clk_core` path under the 20 ns constraint, with no
exception.

## Running

Build with `./build fpga --ip link` on the CI host, or validate the inputs
anywhere with `./build fpga --ip link --dry-run`. Nightly runs
`./build fpga --all`, which selects every IP with a `src/fpga/<ip>/` folder,
so it compiles `link` with no further setting. Resource and timing results
are not recorded here but in `metrics.json` and, from the nightly run, on
the tracker's `link` page; the wrapper's own logic (211 flops and the
parity tree) is included in the resource counts.

## Results

The nightly tracker owns the live figures. This is the acceptance fit,
with the [Intel `common_sdp_ram` backend](../../rtl/common/ram/common_sdp_ram.md#intel-backend):
`ci-gate` run 39074066731, job `ci-fpga`, at `080031d`, Quartus Prime Lite
25.1std.0, slow and fast models. Its `link` FPGA inputs are byte-identical
to the PR's final head, whose own `ci-fpga` run reproduces it (PR #276).

| Item | Result |
| --- | --- |
| `clk_core` (50 MHz target) | Met: setup slack +1.150 ns, hold +0.140 ns, Fmax 53.05 MHz. |
| `clk_rx` (50 MHz target) | Met: setup slack +2.814 ns, hold +0.182 ns, recovery +15.986 ns, removal +0.498 ns, Fmax 58.19 MHz. |
| Unconstrained paths | 0. |
| Logic elements | 7376 / 114480. |
| Registers | 2836. |
| Memory | 4 / 432 M9K, 26112 memory bits. |
| Worst setup path | `clk_core`, inside the receive descrambler (`data_q[1]` to `data_q[15]`), slack +1.150 ns. |
| Quartus warnings | Map 6, fit 2. |

**Block RAM** (`resources.rams`, one row per RAM in the fitter report):

| Instance | Mode | Geometry | Bits | M9K | Mixed-port read during write |
| --- | --- | --- | --- | --- | --- |
| `dll` receive buffer, `u_rx_buf` `common_sdp_ram` | Simple dual port, single clock | 512 × 34 | 17408 | 2 | Old data |
| `dll` replay buffer, `u_tlp_tx` `common_sdp_ram` | Simple dual port, single clock | 256 × 32 | 8192 | 1 | Old data |
| `dll` TX `end_q` array, inferred by Quartus | Simple dual port, single clock | 64 × 8 | 512 | 1 | Old data |

Both `dll` buffers are in M9K through the Intel backend's one-lane form, so
neither is in registers. The fourth M9K is not a `common_sdp_ram`: Quartus
infers the TX path's 64-entry `end_q` array as a RAM.

**`dll` rollover path.** `ltssm_state_o` → `pkt_tx_valid` → `phy` is a
`clk_core` path, so its slack is at least the clock's worst, +1.150 ns. The
flow reports only the single worst path, which is a different one (above),
so this path's exact slack is a bound, not a measurement. The firstmate
accepted the bound in place of a per-path report
([#218 decision](https://github.com/amichai-bd/pcie-factory/issues/218#issuecomment-6243168833)).

## Files

| File | Holds |
| --- | --- |
| [`pcie_link_de2_115.sv`](../../../../src/fpga/link/pcie_link_de2_115.sv) | DE2-115 synthesis and timing harness (FPGA flow only). |
| [`pcie_link.sdc`](../../../../src/fpga/link/pcie_link.sdc) | FPGA timing constraints. |
| [`de2_115.qsf.tcl`](../../../../src/fpga/link/de2_115.qsf.tcl) | DE2-115 device, pins and I/O settings. |
