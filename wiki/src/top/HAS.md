# pcie_top HAS

## Interfaces

One clock, `clk_i`; every input is sampled and every register updates on
its rising edge.

| Port | Dir | Width | Clock | Description |
| --- | --- | --- | --- | --- |
| `clk_i` | in | 1 | - | Clock. |
| `rst_i` | in | 1 | `clk_i` | Synchronous, active-high reset. |
| `valid_i` | in | 1 | `clk_i` | `data_i` holds a byte to scramble this cycle. |
| `data_i` | in | 8 (`t_byte`) | `clk_i` | Input byte. |
| `reseed_i` | in | 1 | `clk_i` | Re-seed the key from the next byte; a byte in this cycle uses the current key. |
| `valid_o` | out | 1 | `clk_i` | `data_o` holds a scrambled byte. |
| `data_o` | out | 8 (`t_byte`) | `clk_i` | Scrambled byte; don't-care while `valid_o` is 0. |

`t_byte` is `pcie_top_lfsr_pkg::t_byte` (`logic [7:0]`).

## Parameters

None.

## Behavior

Let `K` be the key byte of the [LFSR](lfsr/HAS.md#behavior) in the current
cycle. At each rising edge:

| Condition | `valid_o` next | `data_o` next | LFSR |
| --- | --- | --- | --- |
| `rst_i` | 0 | don't care | seed |
| `valid_i` (no reset) | 1 | `data_i ^ K` | re-seed if `reseed_i`, else advance |
| not `valid_i` (no reset) | 0 | don't care | re-seed if `reseed_i`, else hold |

So a byte in the same cycle as `reseed_i` is scrambled with the current
key, and the next valid byte uses the seed key `8'hFF`. After reset or a
re-seed, a stream of `00h` bytes comes out as `FF 17 C0 14 ...`.
Scrambling is its own inverse: feeding the output back after a re-seed
restores the data.

Reset values: `valid_o` is 0 and the LFSR holds the seed. `data_o` is not
reset and is don't-care while `valid_o` is 0.

## Microarchitecture

`pcie_top_lfsr` (instance `u_lfsr`) with `advance_i = valid_i` and
`reseed_i = reseed_i`. One XOR stage, a `valid_q` register with reset, and a
`data_q` register enabled by `valid_i`.

## Timing

- Latency: one cycle from `valid_i`/`data_i` to `valid_o`/`data_o`.
- Throughput: one byte per cycle, back to back.
- No clock-domain crossing.

## FPGA board wrapper

`pcie_top_de2_115` binds `pcie_top` to the Terasic DE2-115 for the
[FPGA flow](../../tools/fpga/SPEC.md); it is board policy, not part of
`top.rtl.f`.

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
pulse (the board debounces the keys). The SDC constrains `CLOCK_50` to
20 ns and false-paths the asynchronous inputs and the static LED outputs.

Build with `./build fpga --ip top`; resource and timing results are not
recorded here but in `metrics.json` and, once nightly runs, the tracker
(#16).

## Files

| File | Holds |
| --- | --- |
| [`pcie_top.sv`](../../../src/top/rtl/pcie_top.sv) | The module. |
| [`top.rtl.f`](../../../src/top/rtl/top.rtl.f) | `-F ../lfsr/rtl/lfsr.rtl.f`, then `pcie_top.sv`. |
| [`pcie_top_de2_115.sv`](../../../src/top/fpga/pcie_top_de2_115.sv) | DE2-115 board wrapper (FPGA flow only). |
| [`pcie_top.sdc`](../../../src/top/fpga/pcie_top.sdc) | FPGA timing constraints. |
| [`de2_115.qsf.tcl`](../../../src/top/fpga/de2_115.qsf.tcl) | DE2-115 device, pins and I/O settings. |
