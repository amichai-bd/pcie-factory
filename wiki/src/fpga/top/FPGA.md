# pcie_top FPGA

`pcie_top_de2_115` binds `pcie_top` to the Terasic DE2-115 for the
[FPGA flow](../../../tools/fpga/SPEC.md), following the
[FPGA BKM](../../../bkm/fpga.md). It is board policy, not part of
`top.rtl.f`: the flow adds it on top of the IP's filelist.

## Board wrapper

| Port | Board signal | Pin(s) | Use |
| --- | --- | --- | --- |
| `clk_50_i` | `CLOCK_50` | `Y2` | 50 MHz clock for everything (`clk_i`). |
| `key_ni[0]` | `KEY[0]` | `M23` | Held: reset (`rst_i`). |
| `key_ni[1]` | `KEY[1]` | `M21` | Press: one `valid_i` pulse with `data_i = SW[7:0]`. |
| `key_ni[2]` | `KEY[2]` | `N21` | Press: one `reseed_i` pulse. |
| `sw_i[7:0]` | `SW[7:0]` | `AB28 AC28 AC27 AD27 AB27 AC26 AD26 AB26` | Data byte. |
| `ledr_o[7:0]` | `LEDR[7:0]` | `G19 F19 E19 F21 F18 E18 J19 H19` | Last `data_o` with `valid_o`; 0 after reset. |
| `ledg0_o` | `LEDG[0]` | `E21` | A byte has been shown since reset. |

Reset is synchronous and active high. It is held for the first 15 cycles
after configuration by a 4-bit power-on counter (`por_cnt_q`, which starts
at 0 because the board file disables power-up don't-care), and while
`KEY[0]` is held. Reset clears both LED outputs.

Keys and switches pass through two-flop synchronizers to `clk_50_i`. A
press is a high-to-low step of a synchronized key, so each press gives one
pulse (the board debounces the keys).

## Constraints

The SDC constrains `CLOCK_50` to 20 ns (50 MHz) and false-paths the
asynchronous inputs and the static LED outputs. The board file sets the
Cyclone IV E device, pins and I/O standards.

## Running

Build with `./build fpga --ip top` on the CI host, or validate the inputs
anywhere with `./build fpga --ip top --dry-run`. Resource and timing results
are not recorded here but in `metrics.json` and, from the nightly run, the
tracker.

## Files

| File | Holds |
| --- | --- |
| [`pcie_top_de2_115.sv`](../../../../src/fpga/top/pcie_top_de2_115.sv) | DE2-115 board wrapper (FPGA flow only). |
| [`pcie_top.sdc`](../../../../src/fpga/top/pcie_top.sdc) | FPGA timing constraints. |
| [`de2_115.qsf.tcl`](../../../../src/fpga/top/de2_115.qsf.tcl) | DE2-115 device, pins and I/O settings. |
