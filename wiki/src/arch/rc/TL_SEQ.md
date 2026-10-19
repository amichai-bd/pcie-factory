# pcie_rc tl-seq interface

The interface between the root-port transaction layer `pcie_rc_tl` and the
sequencer `pcie_rc_seq` inside [`pcie_rc`](HAS.md) (decision record
[0005](../../decisions/0005-m4-grill.md#decisions), OP1, OP4, OP5). It is
part of the `rc` HAS and changes with it, and the `rc_tl` MAS
([#368](https://github.com/amichai-bd/pcie-factory/issues/368)) and the
`rc_seq` MAS ([#369](https://github.com/amichai-bd/pcie-factory/issues/369))
implement it exactly. A change here changes both.

## Ownership

| Concern | Owner | Why |
| --- | --- | --- |
| Forming and sending every TLP; tags, outstanding requests, completion matching and timeout; the Type 1 header; routing; host memory; MSI capture; the error record | `rc_tl` | The root-port rules and state live in one sub-IP. |
| The program: the ROM, its commands and their encoding, expects with a mask, waits and their timeouts, the run result | `rc_seq` | The sequencer only issues commands and checks results; it knows no TLP format. |
| The `link` Hot Reset request | `rc_seq` | A program step ([downstream role](HAS.md#downstream-role)). |

## Signals

Each net `<x>` is the port `<x>_o` of the sub-IP that drives it and `<x>_i`
of the other; none is an `rc` port. All are synchronous to `clk_i`. Both
sub-IPs are reset by `rst_i` only, not by link down
([link down](HAS.md#link-down)); while `rst_i` is 1, `cmd_ready`,
`rsp_valid` and `msi_valid` are 0. The bundled fields cross as packed
structs in `common_pkg`, the package for types two IPs exchange
([RTL BKM](../../../bkm/rtl.md#structs-at-interfaces)); the field names
below are the struct fields of `t_rc_cmd`, `t_rc_rsp` and `t_rc_msi`,
added with the `rc_tl` shell ([`rc_tl` interfaces](../../rtl/rc_tl/MAS.md#interfaces)).

`rc` also wires `link`'s `dl_up_o` and `link_up_o` to `rc_seq` (for its
waits) and `rc_seq`'s Hot Reset request to `link`'s `hot_reset_req_i`.

### Commands

| Net | Dir | Width | Meaning |
| --- | --- | --- | --- |
| `cmd_valid` | seq→tl | 1 | A command is offered. Its fields are held while it is 1. |
| `cmd_ready` | tl→seq | 1 | `rc_tl` takes the command at this edge. |
| `cmd.op` | seq→tl | 4 | The operation, below. |
| `cmd.addr` | seq→tl | 32 | The address, by operation, below. |
| `cmd.len` | seq→tl | 6 | DWs: 1 to 32 for `MEM_RD`, 1 for every other operation. |
| `cmd.first_be` | seq→tl | 4 | Byte enables of the first DW; bit i enables byte i (`data[8i+7:8i]`). |
| `cmd.last_be` | seq→tl | 4 | Byte enables of the last DW when `len` is above 1; 0 otherwise. |
| `cmd.data` | seq→tl | 32 | Write data, byte 0 in `[7:0]`; for `ERR_CLR`, the bits to clear; don't care for a read. |

| `op` | Name | `addr` | Effect |
| --- | --- | --- | --- |
| 0 | `CFG_RD` | Bus `[27:20]`, device `[19:15]`, function `[14:12]`, DW index `[11:2]` | Configuration read to the endpoint ([outbound](HAS.md#outbound-routing)). |
| 1 | `CFG_WR` | As `CFG_RD` | Configuration write to the endpoint. |
| 2 | `MEM_RD` | Byte address, `[1:0]` 0 | Memory read of `len` DWs to the endpoint. |
| 3 | `MEM_WR` | Byte address, `[1:0]` 0 | Memory write of 1 DW to the endpoint. |
| 4 | `HDR_RD` | DW index `[11:2]` | Read of the root port's [Type 1 header](HAS.md#type-1-header) ([header access](#header-access)). |
| 5 | `HDR_WR` | DW index `[11:2]` | Write of the header. |
| 6 | `HOST_RD` | Byte offset in host memory, `[1:0]` 0 | Read of 1 DW of [host memory](HAS.md#host-memory) ([host memory access](#host-memory-access)). |
| 7 | `HOST_WR` | As `HOST_RD` | Write of 1 DW of host memory. |
| 8 | `ERR_RD` | Ignored | Read of the [error record](#error-record). |
| 9 | `ERR_CLR` | Ignored | Clears the error record bits set in `data`. |

Any other `op`, a `len` outside its range (a `MEM_WR` with `len` above 1
included), `addr` bits outside the fields above not 0, or a `MEM_RD` whose
`addr` and `len` cross a 4 KiB boundary, finishes with `LOCAL_UR`: nothing
is sent and nothing changes. For an endpoint command (`CFG_*`, `MEM_*`)
offered while `dl_up_o` is 0, `LINK_DOWN` takes precedence over these
checks, as in the [outbound routing](HAS.md#outbound-routing) order. Which byte-enable combinations are legal is
the `rc_tl` MAS's ([#368](https://github.com/amichai-bd/pcie-factory/issues/368)). `rc_tl`
takes commands in order; a command's effect on rc state (a header,
host-memory or error-record write) is visible to every later command.

### Results

| Net | Dir | Width | Meaning |
| --- | --- | --- | --- |
| `rsp_valid` | tl→seq | 1 | One-cycle pulse: one result beat. `rc_seq` takes every beat; there is no ready. |
| `rsp.status` | tl→seq | 3 | The status, below. |
| `rsp.data` | tl→seq | 32 | A read's DW, byte 0 in `[7:0]`; 0 for a write or a failed command. |
| `rsp.last` | tl→seq | 1 | The command's last beat. |

| `status` | Name | Meaning |
| --- | --- | --- |
| 0 | `OK` | Done: a write sent (`MEM_WR`, once `link` has taken its last beat) or completed, a read's data. |
| 1 | `UR` | The endpoint completed with Unsupported Request. |
| 2 | `CA` | The endpoint completed with Completer Abort. |
| 3 | `CRS` | The endpoint completed a configuration request with Configuration Request Retry Status [cite: PCIe Base Spec, rev 3.0, §2.3.2]; `rc_tl` does not retry, the program may. |
| 4 | `TIMEOUT` | No completion within the completion timeout. |
| 5 | `LOCAL_UR` | Not sent: refused by the [outbound routing](HAS.md#outbound-routing) or by the field checks above. |
| 6 | `LINK_DOWN` | Not sent, or abandoned, because `dl_up_o` is or fell to 0. |
| 7 | `BAD` | A poisoned or malformed completion, or one with a reserved status, for this request. |

- **Order.** Results come in command order. Every command gets at least
  one beat; only a `MEM_RD` gets more, at most `len`: `len` beats when it
  succeeds, the DWs in address order, however many completions carried
  them, and fewer when it fails (below).
- **Failure.** Every beat but the last has status `OK`; the last beat's
  status is the command's. A read that fails after some of its data has
  arrived ends with its failing status on its last beat.
- **Pipelining.** `rc_seq` may offer a command before earlier results
  arrive; how many endpoint requests `rc_tl` keeps outstanding is its
  MAS's ([#368](https://github.com/amichai-bd/pcie-factory/issues/368)).
- **Local commands** (`HDR_*`, `HOST_*`, `ERR_*`) always finish `OK`
  unless the field checks refuse them, whatever the link does.

### Header access

`HDR_RD` returns the DW at the index, every field included; indices 16
to 1023 read 0. `HDR_WR` writes the bytes `first_be` enables, field by
field as the [header table](HAS.md#type-1-header) gives access: RW fields
take the data, RO fields and indices 16 to 1023 ignore it. A write takes
effect for every later command, including the routing of the next
endpoint request.

### Host memory access

`HOST_RD` returns the DW at the offset; `HOST_WR` writes the bytes
`first_be` enables. Offset bits at or above `HOST_MEM_ADDR_W` must be 0.
A write is in host memory before its result, so an endpoint read that a
later command triggers sees it. The endpoint's own writes take effect in
arrival order, the endpoint's posted requests never passing one another
[cite: PCIe Base Spec, rev 3.0, §2.4.1]: when an MSI is captured, every
DMA write the endpoint sent before it is in host memory.

### Error record

One DW of sticky event bits in `rc_tl`. A bit sets on its event, stays set
through link down, and clears on `ERR_CLR` with that bit 1 in `data` or on
`rst_i`. How each event is detected is the `rc_tl` MAS's
([#368](https://github.com/amichai-bd/pcie-factory/issues/368)).

| Bit | Event |
| --- | --- |
| 0 | A completion with Unsupported Request status for an endpoint request. |
| 1 | A completion with Completer Abort status. |
| 2 | A configuration completion with Configuration Request Retry Status. |
| 3 | A completion timeout. |
| 4 | A completion matching no outstanding request (dropped). |
| 5 | A poisoned TLP received [cite: PCIe Base Spec, rev 3.0, §2.7.2.2]. |
| 6 | A malformed TLP received. |
| 7 | An ERR_COR message received. |
| 8 | An ERR_NONFATAL message received. |
| 9 | An ERR_FATAL message received. |
| 10 | Any other message received (dropped). |
| 11 | An inbound request handled as Unsupported Request ([inbound routing](HAS.md#inbound-routing)). |
| 12 | An MSI dropped because the capture was full. |
| 31:13 | Reserved, read 0. |

### MSI capture

| Net | Dir | Width | Meaning |
| --- | --- | --- | --- |
| `msi_valid` | tl→seq | 1 | The oldest captured MSI is offered. |
| `msi_ready` | seq→tl | 1 | `rc_seq` takes (removes) it at this edge. |
| `msi.offset` | tl→seq | 10 | Its DW offset in the [MSI window](HAS.md#msi-window). |
| `msi.data` | tl→seq | 32 | Its data DW, byte 0 in `[7:0]`. |

Captures are kept in arrival order, at least 4 of them (the depth is the
`rc_tl` MAS's); a capture arriving when full is dropped and sets error
record bit 12. The capture survives link down and empties on `rst_i`. A
"wait for MSI" step waits for `msi_valid`, with the timeout the `rc_seq`
MAS sets, and takes the capture to check it.
