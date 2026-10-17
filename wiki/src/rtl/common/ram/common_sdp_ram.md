# common_sdp_ram MAS

`common_sdp_ram` is the simple dual-port synchronous RAM: one write port
and one read port with its own address and a read enable, on one clock.
Like every shared block it has no HAS, and this page owns its interface,
behavior and structure. The [family decisions](common_sync_ram.md#decisions)
apply; a consumer that reads every edge ties `rd_en_i` high.

## Interfaces

| Port | Dir | Width | Clock | Description |
| --- | --- | --- | --- | --- |
| `clk_i` | in | 1 | - | Clock; every access is at its rising edge. |
| `wr_en_i` | in | 1 | `clk_i` | Write the selected lanes of word `wr_addr_i` at this edge. |
| `wr_lanes_i` | in | `LANES` | `clk_i` | Lane enables, as in [`common_sync_ram`](common_sync_ram.md#interfaces). |
| `wr_addr_i` | in | `ADDR_W` | `clk_i` | Write address. |
| `wr_data_i` | in | `WIDTH` | `clk_i` | Write data; only the enabled lanes are stored. |
| `rd_en_i` | in | 1 | `clk_i` | Read enable. |
| `rd_addr_i` | in | `ADDR_W` | `clk_i` | Read address. |
| `rd_data_o` | out | `WIDTH` | `clk_i` | The word read at the last edge with `rd_en_i` high. |

## Parameters

| Name | Type | Default | Legal range | Effect |
| --- | --- | --- | --- | --- |
| `WIDTH` | `int unsigned` | 8 | 1 or more | Word width in bits. |
| `ADDR_W` | `int unsigned` | 4 | 1 or more | Depth is `2**ADDR_W` words. |
| `LANES` | `int unsigned` | 1 | 1 or more, dividing `WIDTH` | Number of equal write lanes. |

Illegal values stop elaboration through `COMMON_ASSERT_PARAM` blocks
`g_width_check`, `g_addr_w_check` and `g_lanes_check` with the message
`common_sdp_ram: <rule>`.

## Behavior

- **Read:** at a rising edge with `rd_en_i` high, `rd_data_o` takes the
  word at `rd_addr_i` as it was before that edge (one-edge latency); with
  `rd_en_i` low it keeps its value.
- **Write:** at a rising edge with `wr_en_i` high, the lanes enabled by
  `wr_lanes_i` of word `wr_addr_i` take `wr_data_i`; independent of the
  read port.
- **Read during write:** an enabled read of the word written at the same
  edge returns the old word; the new word is readable from the next edge.
- **Contents:** words are undefined until written; `rd_data_o` is
  undefined until the first enabled read. No reset.

## Microarchitecture

- **Storage and ports:** `mem_q` with one `COMMON_MEM_WRITE_LANES` process
  at `wr_addr_i` and one `COMMON_MEM_READ_EN` process into `rd_data_o` at
  `rd_addr_i`, both on `clk_i`. Separate processes make a collision
  read-first.
- **Inference shape:** the conventional simple dual-port template.
  Expected, not yet measured: Quartus maps it to block RAM in simple
  dual-port mode with old-data mixed-port read-during-write, and the read
  enable to the read port's read or clock enable; lanes map to byte
  enables as in the [base MAS](common_sync_ram.md#microarchitecture).
  Yosys infers a memory with one write port and one non-transparent
  synchronous read port with an enable; the ASIC flow maps it to flops
  unless the target selects the [sky130 backend](#sky130-backend).

## Decisions

| Choice | Rationale | Rejected |
| --- | --- | --- |
| One module with a read enable | The source's two modules differ only in a tied-high enable; one block keeps one page and one test set. | The source's separate always-read wrapper. |
| A sky130 backend file, selected by the ASIC source list ([#279](https://github.com/amichai-bd/pcie-factory/issues/279)) | Inferred arrays map to flops on sky130hd, too large to route (about 25 kbit in `link`); one module name per backend file keeps the RTL free of conditionals and lint still sees the backend. | A flow define or top parameter (a conditional or plumbing through every consumer); Yosys memory mapping in an ORFS hook (no reviewed RTL, no lint). |
| Pending write with a bypass in the sky130 backend | The macro defines no same-word write and read in one edge; delaying the write one edge and bypassing it keeps the read-first contract for every input. | Requiring consumers never to collide (a contract change). |

Provenance: adapted from nand2anything `src/common/rtl/n2a_sdp_ram_portable.sv`
(modules `n2a_sdp_ram_enabled` and `n2a_sdp_ram`, commit `085ef4b`),
adapted with the owner's permission.

## sky130 backend

[`common_sdp_ram_sky130.sv`](../../../../../src/rtl/common/ram/common_sdp_ram_sky130.sv),
listed by its backend list `common_sdp_ram.sky130.f`, is this module on
sky130 SRAM macros ([backends](../../../../project/layout.md#backends)).
Only an ASIC source list selects it (`asic/<ip>/sky130hd.backends.f`);
simulation, lint of the IPs and the FPGA flow use the portable file. Its
parameters, ports and the behavior above are identical, with one limit:
`LANES` must be 1 (`g_lanes_check`).

**Macro.** `sky130_sram_1rw1r_64x256_8`, an OpenRAM macro the pinned ORFS
image carries (`flow/platforms/sky130ram/sky130_sram_1rw1r_64x256_8/`:
`.lef`, `_TT_1p8V_25C.lib`, `.gds`, `.v`; ORFS commit `d4b96380b10e`), 256
words of 64 bits with a byte write mask. Port 0 reads or writes (`csb0`,
`web0` active low), port 1 reads (`csb1` active low); both register every
input at the rising edge of their clock. Its Liberty view (typical corner,
1.8 V, 25 °C) gives a 0.165 ns setup and a −0.052 ns hold on every input,
and the read data on a falling-edge arc, about 0.38 to 0.53 ns after the
falling edge that follows the read edge; the area is about 0.42 mm²
(1041 × 403 µm). The image also holds 128x256, 80x64 and 44x64 variants;
64x256 tiles both `dll` buffers with the fewest macros of the same storage.
The macro's model drives undefined data in a cycle without a read, and a
port 1 read of the word port 0 writes at the same edge is undefined.

**Structure.**

- **Tiling.** `ceil(WIDTH / 64)` macros across and `2**(ADDR_W - 8)` deep
  (one row, low words only, when `ADDR_W <= 8`). Port 0 of every macro is
  the write port, port 1 the read port, both on `clk_i`; a row's chip
  selects decode the top address bits; unused data bits are written as
  zero. The `dll` replay buffer (256 × 32) takes one macro, the receive
  buffer (512 × 34) two.
- **Pending write.** A write is registered (`pend_q`, `pend_addr_q`,
  `pend_data_q`) and committed to the macros at the next edge. A read of
  the pending word (`collide`) disables the macro read and registers the
  pending data (`byp_q`, `byp_data_q`) instead. So no physical collision
  happens, and an enabled read always gets the word before this edge's
  write (read-first); a word written at edge N is readable at edge N+1,
  from the bypass, and from the macro after.
- **Read data and hold.** `fresh_q` (the last edge read the macros) and
  `row_q` select the macro row's read data; `hold_q` takes it at the next
  edge, and `rd_data_o` is the bypass word, the fresh macro word or
  `hold_q`. So `rd_data_o` keeps the last word read through cycles without
  a read, as the portable read register does. The extra state is
  `2 × WIDTH + ADDR_W + ROW_W + 3` flops.
- **Latency and timing.** Still one edge, but the macro word arrives about
  half a period plus 0.5 ns after the read edge, not at the clock-to-output
  of a register, so the consumer's logic after `rd_data_o` gets the
  remaining half period. STA covers it through the macro's Liberty arcs.

**Verification.** The [lint check](../../../../tools/checks/SPEC.md#lint-check)
lints the backend list with the DV-only cell model
[`sky130_sram_1rw1r_64x256_8.sv`](../../../../../src/dv/common/tb/sky130_sram_1rw1r_64x256_8.sv),
written for this repository from the macro's documented behavior: inputs
at the rising edge, read data for one cycle only, junk otherwise, and a
fatal error on a same-word write and read. The backend's equivalence with
the portable RAM, against that model and against the OpenRAM model, is PR
evidence (#279), not a regression yet. Silicon behavior and timing come
only from the ASIC flow.

## Timing

One clock, no reset. `rd_data_o` is the RAM's read register.

## Files

| File | Holds |
| --- | --- |
| [`common_sdp_ram.sv`](../../../../../src/rtl/common/ram/common_sdp_ram.sv) | The module. |
| [`common_sdp_ram.rtl.f`](../../../../../src/rtl/common/ram/common_sdp_ram.rtl.f) | `-F ../common_macros.rtl.f`, then the module. |
| [`common_sdp_ram_sky130.sv`](../../../../../src/rtl/common/ram/common_sdp_ram_sky130.sv) | The [sky130 backend](#sky130-backend): the same module on SRAM macros. |
| [`common_sdp_ram.sky130.f`](../../../../../src/rtl/common/ram/common_sdp_ram.sky130.f) | Its backend list: `-F ../common_macros.rtl.f`, then the backend. |
