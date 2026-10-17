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
- **Inference shape:** the conventional simple dual-port template. The
  FPGA flow does not rely on inference: it selects the
  [Intel backend](#intel-backend). Yosys infers a memory with one write
  port and one non-transparent synchronous read port with an enable; the
  ASIC flow maps it to flops.

## Intel backend

`common_sdp_ram_intel.sv` is the same module for Quartus: one `altsyncram`
named `u_ram` (in generate block `g_one_lane` for one lane, else
`g_lanes`), a [source backend](../../../../project/layout.md#source-backends)
that only the FPGA flow's source list selects (`cfg/backends.yaml`, suffix
`intel`), in place of the portable file. Simulation, `./build check` lint
and the ASIC flow read the portable file; Verilator reads the backend only
against the DV-only stub `src/dv/common/tb/altsyncram.sv`.

| `altsyncram` parameter | Value | Why |
| --- | --- | --- |
| `operation_mode` | `DUAL_PORT` | Port A writes, port B reads (simple dual port). |
| `intended_device_family`, `ram_block_type` | `INTEL_DEVICE_FAMILY` (default `Cyclone IV E`), `INTEL_RAM_BLOCK_TYPE` (default `M9K`) | Vendor facts, not contract: the backend's two extra parameters; another Intel board overrides them from its board settings file. |
| `width_a`, `widthad_a`, `numwords_a`; `width_b`, `widthad_b`, `numwords_b` | `WIDTH`, `ADDR_W`, `2**ADDR_W` on both ports | No mixed widths. |
| `width_byteena_a`, `byte_size` | One lane: not given (vendor defaults, no byte enables). Several lanes: `LANES` and the lane width | One lane writes whole words through `wren_a` alone, as Intel-generated wrappers do; Quartus rejects `width_byteena_a = 1` with a `byte_size` that is not the width (Error 272006, seen on the first `link` fit with 34-bit words). Several lanes are byte enables; an M9K byte is 8 or 9 bits, so they need 8- or 9-bit lanes (`g_lane_w_check`, Intel backend only). |
| `outdata_reg_a`, `outdata_reg_b` | `UNREGISTERED` | One-edge read latency: `rd_data_o` is the block's read register. |
| `address_reg_b`, `rdcontrol_reg_b` | `CLOCK0` | One clock. |
| `read_during_write_mode_mixed_ports` | `OLD_DATA` | Read-first collision, as the portable file. |
| `clock_enable_*` | `BYPASS` | No clock enables. |
| `init_file`, `power_up_uninitialized` | `UNUSED`, `FALSE` | No image; contents stay undefined to the contract. |

Pins: `clock0` is `clk_i`; `wren_a` is `wr_en_i` with at least one lane
enabled, `byteena_a` is `wr_lanes_i` with several lanes (unconnected with
one), `address_a` and `data_a` the write
port; `rden_b` is `rd_en_i` (a disabled read holds `q_b`), `address_b` and
`q_b` the read port. `wren_b`, `aclr*` and `addressstall_*` tie low;
`rden_a`, `clock1`, `clocken*` and `byteena_b` tie high. `data_b`, `q_a` and
`eccstatus`, absent in this mode, are explicit empty connections, as
Intel-generated wrappers leave them, and so is `byteena_a` with one lane;
the backend's lint test expects exactly `DECLFILENAME` and those
`PINCONNECTEMPTY` messages, counted over both generate branches (Verilator
reports each branch's empty connections whatever `LANES` is). The
parameter checks are the portable file's plus `g_lane_w_check`.

**Fitter evidence.** Simulation never proves the mapping; each consuming
target's fit does (`resources.rams` in its `metrics.json`). Expected per
instance: `Simple Dual Port`, `Single Clock`, mixed-port RDW `Old data`,
and the M9K count below (an M9K holds 256 words of up to 36 bits, or 512
of up to 18, in this mode). Measured on the first `link` fit
([#276](https://github.com/amichai-bd/pcie-factory/pull/276) `ci-fpga`, run
39074066731 at `080031d`, whose `link` FPGA inputs equal the PR's final
head, which reproduces it; Quartus Prime Lite 25.1std, EP4CE115F29C7, with
this backend's one-lane form; map, fit and timing passed;
[`link` FPGA, Results](../../../fpga/link/FPGA.md#results)). The fit's fourth M9K is not this block: Quartus
infers the 64 x 8 `end_q` array of the dll TX path as a RAM.

| Consumer (target) | Geometry | Expected M9K | Measured |
| --- | --- | --- | --- |
| dll receive buffer (`link`, DE2-115) | 512 x 34 | 2 (two x18 at 512 words) | 2 M9K: Simple Dual Port, Single Clock, 512 x 34, 17408 bits, mixed RDW Old data (`altsyncram:g_one_lane.u_ram`). |
| dll replay buffer (`link`, DE2-115) | 256 x 32 | 1 (x32 at 256 words) | 1 M9K: Simple Dual Port, Single Clock, 256 x 32, 8192 bits, mixed RDW Old data (`altsyncram:g_one_lane.u_ram`). |

## Decisions

| Choice | Rationale | Rejected |
| --- | --- | --- |
| One module with a read enable | The source's two modules differ only in a tied-high enable; one block keeps one page and one test set. | The source's separate always-read wrapper. |
| An explicit Intel backend selected by source list | The fit must be known (M9K, old-data collision), not inferred; one module name in two files keeps every consumer and test unchanged. | Inference alone (shape unmeasured); `ifdef` or `ramstyle` in the block; vendor primitives in IP RTL. |
| Lanes on byte enables, 8- or 9-bit lanes only | Matches M9K byte sizes; the consumers use one lane. | Emulating narrow lanes with read-modify-write. |

Provenance: adapted from nand2anything `src/common/rtl/n2a_sdp_ram_portable.sv`
(modules `n2a_sdp_ram_enabled` and `n2a_sdp_ram`, commit `085ef4b`),
adapted with the owner's permission. The Intel backend is adapted from
nand2anything `src/common/rtl/n2a_sdp_ram_intel.sv`, and its stub and
rejection set from `src/common/dv/memory/n2a_altsyncram_port_stub.sv` and
its reject fixtures (commit `8bb70d248d4755d7510fb2c0ec6487a5e05dec41`),
adapted with the owner's permission; that project fits the same backend on
the DE2-115.

## Timing

One clock, no reset. `rd_data_o` is the RAM's read register.

## Files

| File | Holds |
| --- | --- |
| [`common_sdp_ram.sv`](../../../../../src/rtl/common/ram/common_sdp_ram.sv) | The module. |
| [`common_sdp_ram.rtl.f`](../../../../../src/rtl/common/ram/common_sdp_ram.rtl.f) | `-F ../common_macros.rtl.f`, then the module. |
| [`common_sdp_ram_intel.sv`](../../../../../src/rtl/common/ram/common_sdp_ram_intel.sv) | The [Intel backend](#intel-backend); in no filelist, selected by the FPGA flow. |
