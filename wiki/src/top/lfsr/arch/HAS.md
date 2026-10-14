# pcie_top_lfsr HAS

## Interfaces

One clock, `clk_i`; every input is sampled and every register updates on
its rising edge.

| Port | Dir | Width | Clock | Description |
| --- | --- | --- | --- | --- |
| `clk_i` | in | 1 | - | Clock. |
| `rst_i` | in | 1 | `clk_i` | Synchronous, active-high reset. |
| `reseed_i` | in | 1 | `clk_i` | Load the seed at the next edge. Wins over `advance_i`. |
| `advance_i` | in | 1 | `clk_i` | Advance the state by eight shifts at the next edge. |
| `key_o` | out | 8 (`t_byte`) | `clk_i` | Key byte for the current state; combinational from the state register. |

## Parameters

None. Width, polynomial and seed are fixed constants in the package ([Files](#files)).

## Behavior

The state `S` is 16 bits. One **shift** is:

- the output bit is `S[15]`;
- `S' = {S[14:0], 1'b0} ^ (S[15] ? 16'h0039 : 16'h0000)`, that is, `S[15]`
  feeds back into bits 0, 3, 4 and 5 (G(X) = X^16 + X^5 + X^4 + X^3 + 1).

`key_o[i]`, for `i` = 0 to 7, is the output bit of shift `i` when eight
shifts are applied to the current state: bit 0 is the first bit and
scrambles data bit 0. An **advance** replaces the state with the state after
those eight shifts.

At each rising edge, in priority order:

| Condition | Next state |
| --- | --- |
| `rst_i` | `16'hFFFF` (seed) |
| `reseed_i` | `16'hFFFF` (seed) |
| `advance_i` | the state after eight shifts |
| otherwise | unchanged |

Reset value: the state is `16'hFFFF`, so `key_o` is `8'hFF` in the first
cycle after reset or re-seed. From the seed, consecutive advances give the
key bytes `FF 17 C0 14 B2 E7 02 82 ...` (the published Gen1/Gen2 sequence
for scrambled `00h` data). The sequence is maximal-length: it repeats after
65535 advances, and the all-zero state is unreachable.

## Microarchitecture

One 16-bit state register `state_q`. A combinational loop unrolls the eight
shifts to compute both `key_o` and the advanced state. A second
`always_comb` selects `state_d` (re-seed, advance or hold); the
`always_ff` applies reset.

## Timing

- `key_o` depends only on `state_q`: it is a register-to-output path through
  eight levels of XOR at most.
- An advance or re-seed takes effect in `key_o` one cycle after the inputs
  are sampled.
- No clock-domain crossing.

## Files

| File | Holds |
| --- | --- |
| [`pcie_top_lfsr_pkg.sv`](../../../../src/top/lfsr/rtl/pcie_top_lfsr_pkg.sv) | `LFSR_W`, `BYTE_W`, `t_lfsr`, `t_byte`, `LFSR_TAPS` (`16'h0039`), `LFSR_SEED` (`16'hFFFF`). |
| [`pcie_top_lfsr.sv`](../../../../src/top/lfsr/rtl/pcie_top_lfsr.sv) | The module. |
| [`lfsr.rtl.f`](../../../../src/top/lfsr/rtl/lfsr.rtl.f) | Package, then module. |
