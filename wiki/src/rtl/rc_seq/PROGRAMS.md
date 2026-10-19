# pcie_rc_seq programs

The programs of [`pcie_rc_seq`](MAS.md#programs), section by section: the
steps, in order, and what each checks. The ops and their failure rules are
the [MAS commands](MAS.md#commands); a request step is written as its
tl-seq op (`CFG_RD` is `REQ` with op 0) and checks `OK` (`chk` 1) unless
the row says otherwise. Values are those of `rc` and `core` at their
default parameters; the package (#376) holds them as named constants, and
the wait timeouts, Hot Reset hold and loop limits with them.

## Address plan

| Item | Value | Why |
| --- | --- | --- |
| Buses | Primary 0, Secondary 1, Subordinate 1 | One link below the root port. |
| Endpoint | Bus 1, device 0, function 0 | The only device below a downstream port ([outbound routing](../../arch/rc/HAS.md#outbound-routing)). |
| Memory window | 0x1000_0000 to 0x100F_FFFF (Memory Base and Limit 0x100) | 1 MiB, apart from host memory (0x0 to 0xFFF) and the MSI window (0xFEE0_0000). |
| BAR0, BAR2 | 0x1000_0000, 0x1000_1000 | Inside the window; 4 KiB each at `core` defaults ([BAR sizing](../../arch/cfg/REGISTERS.md#bar-sizing)). |
| MSI | Address 0xFEE0_0000 (window offset 0), data 0x0001 | `MSI_ADDR` default; the offset and data tell this vector apart. |
| Host memory | DMA source 0x100 to 0x13F, destination 0x200 to 0x23F, run flag 0xFFC | Inside the 4 KiB default host memory. |
| Not decoded | 0x1000_8000 (in the window, no BAR); 0x2000_0000 (outside the window) | The errors section's Unsupported Request targets. |

## Boundary

Programs drive the host side alone (goal item 4); what the endpoint does
beyond its configuration space is the application's. In simulation that
is the `core` application model of the e2e
([#377](https://github.com/amichai-bd/pcie-factory/issues/377)), which these
programs need to behave as follows; #377 owns that model.

| Need | Model behavior | Used by |
| --- | --- | --- |
| BAR0 as memory | Offsets 0x000 to 0xEFF store writes with their byte enables and return them on reads. | MMIO, recovery checks |
| DMA doorbell | A write to BAR0 0xF0C starts a copy of the bytes from the system address at 0xF00 to the one at 0xF04, length in bytes at 0xF08 (up to 128), by bus-master reads then writes, then raises MSI vector 0. | DMA and MSI |
| No-answer DW | A read of BAR0 0xF10 is never answered. | Errors (completion timeout) |
| Link down | The e2e takes the link down while the errors section polls BAR0 after its first re-enumeration. | Errors |

The board build (goal item 9) runs `MMIO` only, with the board's
application answering BAR0 as memory.

## Enumeration

The section `ENUM`, first in every M4 program (after the run flag in
`ERRORS`); program `ENUM` is this section then `DONE`.

| # | Steps | Checks |
| --- | --- | --- |
| E1 | `HDR_WR` DW 6 = 0x0001_0100 (bytes 0 to 2), DW 8 = 0x1000_1000, DW 1 = 0x0000_0006 (byte 0). | Bus numbers, memory window, Memory Space and Bus Master Enable of the root port. |
| E2 | `HDR_RD` DW 0, 1, 2, 6, 8, each with an `EXPECT`. | IDs (`rc` defaults), Command 0x0006, Class Code 0x060400 and Revision ID, the buses, the window. |
| E3 | `WAIT_LINK`. | The link reaches DL_Active. |
| E4 | `CFG_RD` DW 0, 3 and 1, each with an `EXPECT`. | `core`'s Vendor and Device IDs, Header Type 0x00 (bits 22:16), Capabilities List (Status bit 4, DW bit 20). |
| E5 | `CFG_RD` DW 13 (0x34); `EXPECT` bits 7:0 = 0x40. | The capability pointer. |
| E6 | `CFG_RD` 0x40, 0x50, 0x60, 0x70; `EXPECT` bits 15:0 = 0x5001, 0x6005, 0x7011, 0x0010. | The capability walk: PM, MSI, MSI-X, PCI Express, ending with next 0 ([`cfg` capabilities](../../arch/cfg/CAPS.md)). |
| E7 | For BAR0 to BAR5 (DW 4 to 9): `CFG_WR` 0xFFFF_FFFF, `CFG_RD`, `EXPECT` the whole DW. | BAR0 and BAR2 read 0xFFFF_F000, the others 0. |
| E8 | `CFG_WR` BAR0, BAR2 with their addresses; `CFG_RD` and `EXPECT` each. | BAR assignment. |
| E9 | `CFG_WR` 0x54 = 0xFEE0_0000, 0x58 = 0, 0x5C = 0x0001, 0x50 byte 2 = 0x01; `CFG_RD` 0x50 and `EXPECT` bit 16. | MSI address, data and MSI Enable. |
| E10 | `CFG_WR` DW 1 byte 0 = 0x06; `CFG_RD` and `EXPECT` bits 2:1 = 0b11. | Memory Space and Bus Master Enable of the endpoint. |

## MMIO

Program `MMIO`: `ENUM`, then this section, then `DONE`.

| # | Steps | Checks |
| --- | --- | --- |
| M1 | 32 `MEM_WR` to BAR0 + 4k, k = 0 to 31, a distinct pattern per DW. | 1-DW writes. |
| M2 | `MEM_RD` BAR0, `len` 32; 32 `EXPECT`s on `R[0]` to `R[31]`. | A 128-byte read returned as several completions (`core` splits on 64-byte boundaries), in address order. |
| M3 | `MEM_RD` BAR0 + 0x38, `len` 4; 4 `EXPECT`s. | A short read crossing a 64-byte boundary. |
| M4 | `MEM_WR` BAR0 + 0x80 = 0x1234_5678; `MEM_WR` BAR0 + 0x80 = 0xFFFF_FFFF with `first_be` 0x3; `MEM_RD`; `EXPECT` 0x1234_FFFF. | Byte enables. |
| M5 | `ERR_RD`; `EXPECT` 0 on bits 12:0. | No error recorded. |

## DMA and MSI

Program `DMA_MSI`: `ENUM`, then this section, then `DONE`. It checks both
DMA directions through host memory: the endpoint reads the source and
writes the destination.

| # | Steps | Checks |
| --- | --- | --- |
| D1 | 16 `HOST_WR` of a pattern to the source, 16 `HOST_WR` of 0 to the destination; `ERR_CLR` 0x1FFF. | Host memory set up before the endpoint reads it. |
| D2 | `MEM_WR` BAR0 + 0xF00 = 0x100, + 0xF04 = 0x200, + 0xF08 = 64, + 0xF0C = 1. | The doorbell ([boundary](#boundary)). |
| D3 | `WAIT_MSI`; `EXPECT` `R[0]` = 0x0001, `R[1]` = 0. | The MSI is captured with its data and window offset. |
| D4 | 16 × (`HOST_RD` destination DW; `EXPECT` the pattern). | The DMA read and write landed: when an MSI is captured, the writes sent before it are in host memory ([host memory access](../../arch/rc/TL_SEQ.md#host-memory-access)). |
| D5 | `ERR_RD`; `EXPECT` 0 on bits 12:0. | No Unsupported Request or lost MSI. |

## Errors and recovery

Program `ERRORS`. A run flag in host memory tells the three passes over
`ENUM` apart, since host memory is the only state a run can branch on.

| # | Steps | Checks |
| --- | --- | --- |
| R1 | `HOST_WR` flag = 0; `ENUM`. | First enumeration. |
| R2 | `HOST_RD` flag; `JUMP DATA_EQ` 1 to R9; `JUMP DATA_EQ` 2 to R12 (limit 1 each). | Pass selection. |
| R3 | `CFG_WR` 0x78 byte 0 = 0x1F; `ERR_CLR` 0x1FFF. | Every error reporting enable on in `core` ([`cfg` errors](../../arch/cfg/HAS.md#errors)), record clear. |
| R4 | `CFG_RD` bus 1, device 0, function 1, checking `UR`; `MEM_WR` 0x1000_8000; `MEM_RD` 0x1000_8000 checking `UR`. | An Unsupported Request completion (non-posted) and a posted Unsupported Request at the endpoint. |
| R5 | `ERR_RD`; `JUMP DATA_NE` back to that `ERR_RD` with value and mask 0x181 (limit set by #376); `EXPECT` 0x181 on 0x181. | Record bits 0 (UR completion), 7 (ERR_COR, the advisory non-posted UR) and 8 (ERR_NONFATAL, the posted UR) are set once the messages arrive. |
| R6 | `MEM_RD` 0x2000_0000, `CFG_RD` device 1, `CFG_RD` bus 2, each checking `LOCAL_UR`. | `rc_tl`'s outbound refusals send nothing. |
| R7 | `MEM_RD` BAR0 + 0xF10 checking `TIMEOUT`; `ERR_RD`; `EXPECT` bit 3; `ERR_CLR` 0x1FFF; `ERR_RD`; `EXPECT` 0 on bits 12:0. | Completion timeout recorded; the record clears. |
| R8 | `HOST_WR` flag = 1; `HOT_RESET`; `WAIT_LINK`; `JUMP ALWAYS` to `ENUM`. | Hot Reset, link-up, second enumeration. |
| R9 | `MEM_WR` then `MEM_RD` BAR0 + 0x40 with an `EXPECT`; `HOST_WR` flag = 2. | Traffic resumes after the Hot Reset. |
| R10 | `MEM_RD` BAR0 (`chk` 0); `JUMP STATUS_EQ OK` back to it (limit set by #376); `JUMP STATUS_EQ LINK_DOWN` over the next step; `FAIL`. | The link goes down under traffic and the request finishes with `LINK_DOWN`. |
| R11 | `WAIT_LINK`; `JUMP ALWAYS` to `ENUM`. | Third enumeration after the link down. |
| R12 | `MEM_WR` then `MEM_RD` BAR0 + 0x44 with an `EXPECT`; `DONE`. | Traffic resumes after the link down. |

Completer Abort, poisoned, malformed and unexpected completions are not
drawn by a program: `core` sends none of them on request, so they are
injected on the line by the e2e (#377) and by `rc_tl`'s own DV
([#375](https://github.com/amichai-bd/pcie-factory/issues/375)), and
`dl_err_o` with the host model shadow checks replay of injected link
errors.

## Self-test

Program `SELFTEST` runs only on the `rc_seq` testbench, whose stand-in
for `rc_tl` answers each command as the test chooses (#376). It holds at
least one step of every op, every `JUMP` condition taken and not taken, a
loop that reaches its limit, a `REQ` of every tl-seq op, a `MEM_RD` of 32
DWs and a `HOT_RESET`; each test makes exactly one chosen step fail, or
none, and checks `pass_o` and `fail_step_o`. Further unit programs take
ids 5 and up ([selection](MAS.md#programs)).
