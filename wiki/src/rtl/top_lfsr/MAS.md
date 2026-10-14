# pcie_top_lfsr MAS

`pcie_top_lfsr` is the data-scrambler LFSR of the PCI Express Gen1/Gen2
(8b/10b) physical layer: it produces the key byte that its parent,
[`pcie_top`](../../arch/top/HAS.md), XORs with each data byte. It is a seed IP,
small on purpose, that gives the factory a real sub-IP to build, regress and
review. As a sub-IP it has no HAS of its own: its role in the system is in
the parent's HAS, and this page owns its interface, behavior and structure.

It is not a full scrambler: it sees no data, does not know which symbols are
K codes, and does not apply the SKP (do not advance) or COM (re-seed) rules;
the parent decides when to advance and re-seed. It is not the Gen3+
(128b/130b) scrambler, which uses a different polynomial.

Its IP id is `top_lfsr` since the kind-first layout
([#92](https://github.com/amichai-bd/pcie-factory/issues/92)); before, it was
`top/lfsr`. Tracker records up to that change keep the old id (records are
never rewritten), so its history before the move is on the
[`top/lfsr` tracker page](../../../tracker/ip/top/lfsr/index.md), and the
`ip:top/lfsr` label is superseded by `ip:top_lfsr`.

## Position

```mermaid
flowchart LR
  ctl["pcie_top: valid_i, reseed_i"] --> lfsr["pcie_top_lfsr"]
  lfsr -- "key_o[7:0]" --> xor["pcie_top: data XOR key"]
```

No sub-IPs.

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

## Decisions

| Choice | Rationale | Rejected |
| --- | --- | --- |
| Galois form, eight shifts unrolled per advance | One key byte per clock for a byte-wide datapath; 16 flops and a few dozen XORs need no pipelining. | Bit-serial LFSR on an 8x clock (second clock); table of precomputed states (area for nothing). |
| `key_o` from the registered state only | The parent's XOR has no combinational path through the control inputs. | Key from the next state (longer path, control-to-data timing arc). |
| Synchronous re-seed with priority over advance | Matches the COM rule: the byte after a COM uses the seed's key. | Advance priority (a COM byte would consume key). |

## Timing

- `key_o` depends only on `state_q`: it is a register-to-output path through
  eight levels of XOR at most.
- An advance or re-seed takes effect in `key_o` one cycle after the inputs
  are sampled.
- No clock-domain crossing.

## Targets

| Item | Target |
| --- | --- |
| Throughput | One key byte per clock. |
| Latency | `key_o` is the seed key in the cycle after reset or re-seed. |
| Area | 16 flops. |
| Polynomial, seed | X^16 + X^5 + X^4 + X^3 + 1, `16'hFFFF`. |

## Files

| File | Holds |
| --- | --- |
| [`pcie_top_lfsr_pkg.sv`](../../../../src/rtl/top_lfsr/pcie_top_lfsr_pkg.sv) | `LFSR_W`, `BYTE_W`, `t_lfsr`, `t_byte`, `LFSR_TAPS` (`16'h0039`), `LFSR_SEED` (`16'hFFFF`). |
| [`pcie_top_lfsr.sv`](../../../../src/rtl/top_lfsr/pcie_top_lfsr.sv) | The module. |
| [`top_lfsr.rtl.f`](../../../../src/rtl/top_lfsr/top_lfsr.rtl.f) | Package, then module. |
