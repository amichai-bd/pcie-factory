# common_sdp_ram_fifo MAS

`common_sdp_ram_fifo` is a first-word-fall-through queue on one clock: up
to 2^`ADDR_W` entries in a [`common_sdp_ram`](common_sdp_ram.md), plus a
head register that is the RAM's read register. The oldest entry waits at
the head until it is popped, and the head can be popped every cycle. Like
every shared block it has no HAS, and this page owns its interface,
behavior and structure. It moved here from `tl`
([#385](https://github.com/amichai-bd/pcie-factory/issues/385)) so that
`rc_tl` reuses it; `tl` builds its receive, completion, transmit and rc
queues from it ([tl MAS, queue](../../tl/MAS.md#queue)).

## Interfaces

| Port | Dir | Width | Clock | Description |
| --- | --- | --- | --- | --- |
| `clk_i` | in | 1 | - | Clock; every change is at its rising edge. |
| `rst_i` | in | 1 | `clk_i` | Active-high synchronous reset: the queue is empty after an edge with `rst_i` high. |
| `push_i` | in | 1 | `clk_i` | Append `push_data_i` at this edge. Never high while `full_o` is high. |
| `push_data_i` | in | `WIDTH` | `clk_i` | The entry pushed. |
| `pop_i` | in | 1 | `clk_i` | Take the head at this edge. Never high while `head_valid_o` is low. |
| `head_valid_o` | out | 1 | `clk_i` | The head holds an entry; a flop. |
| `head_o` | out | `WIDTH` | `clk_i` | The oldest entry, valid while `head_valid_o` is high; the RAM's read register. |
| `empty_o` | out | 1 | `clk_i` | No entry at all, neither at the head nor in the RAM. |
| `full_o` | out | 1 | `clk_i` | The RAM holds 2^`ADDR_W` entries: no room for a push. |
| `ram_used_o` | out | `ADDR_W`+1 | `clk_i` | Entries in the RAM, 0 to 2^`ADDR_W`; the head is not counted. |

`empty_o`, `full_o` and `ram_used_o` are combinational from the pointers
and the head flag, so they depend on no input.

## Parameters

| Name | Type | Default | Legal range | Effect |
| --- | --- | --- | --- | --- |
| `WIDTH` | `int unsigned` | 8 | 1 or more | Entry width in bits. |
| `ADDR_W` | `int unsigned` | 1 | 1 or more | The RAM holds 2^`ADDR_W` entries; the queue holds one more at the head. |

The RAM's own parameter checks (`common_sdp_ram: <rule>`) stop
elaboration on an illegal value.

## Behavior

- **Fall-through:** an entry pushed at edge E into an empty queue is at
  the head after edge E+1: the RAM read that loads it is never at the edge
  that writes it.
- **Load:** at each edge, the oldest entry in the RAM loads into the head
  when the RAM holds an entry written at an earlier edge and the head is
  empty or popped at this edge. A pop with no entry to load empties the
  head.
- **Throughput:** a pop and the load of the next entry share an edge, so
  a queue with entries in the RAM shows a new head after every pop; with
  a push and a pop at every edge it streams one entry per cycle.
- **Capacity:** 2^`ADDR_W` + 1 entries: the RAM full and the head held.
- **Order:** entries leave in the order pushed; none is lost or
  repeated.
- **Reset:** `rst_i` clears the pointers and the head flag; `head_o`
  keeps the RAM's last read word, which no consumer may use while
  `head_valid_o` is low. RAM contents are not reset.
- **Contract errors:** a push while `full_o` is high or a pop while
  `head_valid_o` is low is outside the contract; assertions
  `a_push_when_full` and `a_pop_when_empty` stop simulation.

## Microarchitecture

- **Pointers:** `wr_q` and `rd_q`, `ADDR_W`+1 bits each, count pushes and
  loads; `in_ram = wr_q - rd_q` is the RAM fill, and its top bit is
  `full_o`. The low `ADDR_W` bits address the RAM.
- **Head:** `hv_q` marks the head valid. `load = in_ram != 0 && (!hv_q ||
  pop_i)` reads the RAM at `rd_q` into its read register (the head) and
  advances `rd_q`; `hv_q` takes `load || (hv_q && !pop_i)`.
- **RAM:** one [`common_sdp_ram`](common_sdp_ram.md) `u_ram`, one lane,
  written at `wr_q` on `push_i`, read with `rd_en_i` = `load`. Because
  `load` needs an entry already counted in `in_ram`, its read address
  never equals this edge's write address, so the RAM's collision rule is
  never used. On FPGA and ASIC the RAM's backends apply unchanged.

## Decisions

| Choice | Rationale | Rejected |
| --- | --- | --- |
| The head is the RAM's read register | No extra `WIDTH` flops and no mux after the RAM; a FPGA block RAM's output register is the head. | A separate head register loaded from the RAM (one more `WIDTH` register per queue). |
| No bypass from push to head | The read never meets a write, so the RAM's collision rule and backends never matter; the cost is two edges of latency on an empty queue. | A write-to-head bypass (a mux on `head_o` and a collision case). |
| A shared block in the RAM group, named after the RAM it uses | `tl` and `rc_tl` both need it; the layout rule puts shared logic in `common`, never a copy in each IP. | Keeping it in `tl` and copying it into `rc_tl`. |

## Timing

One clock, `clk_i`; reset synchronous. Push to head: two edges on an
empty queue. Pop to next head: the same edge. No input reaches an output
combinationally.

## Files

| File | Holds |
| --- | --- |
| [`common_sdp_ram_fifo.sv`](../../../../../src/rtl/common/ram/common_sdp_ram_fifo.sv) | The module. |
| [`common_sdp_ram_fifo.rtl.f`](../../../../../src/rtl/common/ram/common_sdp_ram_fifo.rtl.f) | `-F ../common_macros.rtl.f` (the vocabulary), `-F common_sdp_ram.rtl.f`, then the module. |
