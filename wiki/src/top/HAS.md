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
| `reseed_i` | in | 1 | `clk_i` | Re-seed the key after this cycle (COM-style). |
| `valid_o` | out | 1 | `clk_i` | `data_o` holds a scrambled byte. |
| `data_o` | out | 8 (`t_byte`) | `clk_i` | Scrambled byte; meaningful only while `valid_o` is 1. |

`t_byte` is `pcie_top_lfsr_pkg::t_byte` (`logic [7:0]`).

## Parameters

None.

## Behavior

Let `K` be the key byte of the [LFSR](lfsr/HAS.md#behavior) in the current
cycle. At each rising edge:

| Condition | `valid_o` next | `data_o` next | LFSR |
| --- | --- | --- | --- |
| `rst_i` | 0 | `data_i ^ K` if `valid_i`, else unchanged | seed |
| `valid_i` (no reset) | 1 | `data_i ^ K` | re-seed if `reseed_i`, else advance |
| not `valid_i` (no reset) | 0 | unchanged | re-seed if `reseed_i`, else hold |

So a byte in the same cycle as `reseed_i` is scrambled with the current
key, and the next valid byte uses the seed key `8'hFF`. After reset or a
re-seed, a stream of `00h` bytes comes out as `FF 17 C0 14 ...`.
Scrambling is its own inverse: feeding the output back after a re-seed
restores the data.

Reset values: `valid_o` is 0 and the LFSR holds the seed. `data_o` is not
reset; it is qualified by `valid_o`.

## Microarchitecture

`pcie_top_lfsr` (instance `u_lfsr`) with `advance_i = valid_i` and
`reseed_i = reseed_i`. One XOR stage, a `valid_q` register with reset, and a
`data_q` register enabled by `valid_i`.

## Timing

- Latency: one cycle from `valid_i`/`data_i` to `valid_o`/`data_o`.
- Throughput: one byte per cycle, back to back.
- No clock-domain crossing.

## Files

| File | Holds |
| --- | --- |
| [`pcie_top.sv`](../../../src/top/rtl/pcie_top.sv) | The module. |
| [`top.rtl.f`](../../../src/top/rtl/top.rtl.f) | `-F ../lfsr/rtl/lfsr.rtl.f`, then `pcie_top.sv`. |
