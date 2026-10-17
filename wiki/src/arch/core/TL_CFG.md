# pcie_core tl-cfg interface

The interface between the transaction layer `pcie_tl` and the
configuration space `pcie_cfg` inside [`pcie_core`](HAS.md) (decision
record [0004](../../decisions/0004-m3-grill.md#decisions), F8). It is part
of the `core` HAS: the same sign-off covers it, and the `tl` and `cfg` HAS
pages implement it exactly. A change here changes both.

## Ownership

| Concern | Owner | Why |
| --- | --- | --- |
| TLP parsing, routing, Unsupported Request and malformed checks, completions, tags and completion timeout | `tl` | Header rules live in one IP. |
| Every configuration register, BAR, capability, the MSI-X table and pending bits | `cfg` | Register state lives in one IP. |
| Matching a memory request to BAR0 or BAR2 | `tl`, from the bases `cfg` drives | The decode sits on the receive path; the sizes are parameters both IPs see, so no mask crosses. |
| Status bits and the decision to send an error message | `cfg` | It holds the severity rules, the Device Control enables and SERR# enable; `tl` reports events and holds none of them. |
| Resolving an interrupt to an MSI or MSI-X write, masking and pending bits | `cfg` | It holds the MSI and MSI-X state and the table; `tl` only sends the write `cfg` asks for. |
| Forming and sending every TLP, interrupt writes and error messages included | `tl` | One transmit path and one ordering point. |

## Signals

Each net `<x>` is the port `<x>_o` of the IP that drives it and `<x>_i` of
the other; none is a `core` port. All are synchronous to `clk_i`. Both IPs
are held in reset while `rst_i` is 1 or `dl_up_o` is 0
([reset](HAS.md#reset)); every output below is 0 in reset.

### Register access

`tl` hands `cfg` every Type 0 configuration request addressed to function
0, and every memory request that hits BAR2 (the MSI-X table and pending-bit
array, decision F3), one DW at a time.

| Net | Dir | Width | Clock | Meaning |
| --- | --- | --- | --- | --- |
| `reg_req_valid` | tl→cfg | 1 | `clk_i` | An access is offered. |
| `reg_req_ready` | cfg→tl | 1 | `clk_i` | `cfg` takes the access at this edge. |
| `reg_req_bar2` | tl→cfg | 1 | `clk_i` | 0 configuration space, 1 BAR2 memory. |
| `reg_req_write` | tl→cfg | 1 | `clk_i` | 1 write, 0 read. |
| `reg_req_addr` | tl→cfg | 10 | `clk_i` | DW index. Configuration: the request's Extended Register Number in `[9:6]` and Register Number in `[5:0]` [cite: PCIe Base Spec, rev 3.0, §2.2.7]. BAR2: the byte offset in BAR2 divided by 4. |
| `reg_req_be` | tl→cfg | 4 | `clk_i` | Byte enables of this DW; bit i enables `reg_req_data[8i+7:8i]`. |
| `reg_req_data` | tl→cfg | 32 | `clk_i` | Write data, byte 0 in `[7:0]`; don't care for a read. |
| `reg_req_bus` | tl→cfg | 8 | `clk_i` | Configuration write only: the Bus Number the request is addressed to. |
| `reg_req_dev` | tl→cfg | 5 | `clk_i` | Configuration write only: the Device Number the request is addressed to. |
| `reg_cpl_valid` | cfg→tl | 1 | `clk_i` | One-cycle pulse: the access taken last has finished. |
| `reg_cpl_data` | cfg→tl | 32 | `clk_i` | With `reg_cpl_valid`, for a read: the DW, disabled bytes included; don't care for a write. |

- **One access at a time.** After an access is taken, `tl` offers no
  other until `reg_cpl_valid`, which comes 1 or more cycles after the
  take, for writes too. Fields are held while valid is 1.
- **Configuration.** One request is one access. `cfg` completes every
  access successfully: a register it does not implement reads 0 and
  ignores writes, so there is no completion status on this interface.
  `tl` builds the completion (with data for a read) itself. A request to
  a function other than 0 never reaches `cfg`; `tl` completes it with
  Unsupported Request [cite: PCIe Base Spec, rev 3.0, §2.3.1].
- **Bus and device number.** On every configuration write it takes, `cfg`
  captures `reg_req_bus` and `reg_req_dev` as this function's bus and
  device number, whatever the register
  [cite: PCIe Base Spec, rev 3.0, §2.2.6.2].
- **BAR2.** `tl` splits a BAR2 request into DW accesses in address order,
  with the byte enables of each DW. For a read it gathers the `reg_cpl_data`
  DWs into its completions; a write needs no completion on the link, but
  its accesses still finish before the next one starts.

### Register state

Levels `cfg` drives every cycle. Each changes only at the edge a
configuration write takes effect, or at reset.

| Net | Dir | Width | Clock | Meaning |
| --- | --- | --- | --- | --- |
| `bus_num` | cfg→tl | 8 | `clk_i` | Captured Bus Number; 0 from reset to the first configuration write. |
| `dev_num` | cfg→tl | 5 | `clk_i` | Captured Device Number, as above. |
| `mem_space_en` | cfg→tl | 1 | `clk_i` | Command register Memory Space Enable. |
| `bus_master_en` | cfg→tl | 1 | `clk_i` | Command register Bus Master Enable. |
| `d_state` | cfg→tl | 2 | `clk_i` | Power State: 0 D0, 3 D3hot (decision F5). |
| `bar0_base` | cfg→tl | 32 | `clk_i` | BAR0 address: bits `[31:BAR0_ADDR_W]` as software wrote them, lower bits 0. |
| `bar2_base` | cfg→tl | 32 | `clk_i` | BAR2 address: bits `[31:12]` as software wrote them, lower bits 0. |
| `cpl_timeout_dis` | cfg→tl | 1 | `clk_i` | Device Control 2 Completion Timeout Disable: 1 disables `tl`'s requester completion timeout. When it falls, each read still outstanding gets a fresh full timeout from that cycle. |

- The completer ID and requester ID of every TLP `tl` sends are
  `bus_num`, `dev_num` and function 0.
- **BAR hit.** A memory request hits BARn when `mem_space_en` is 1,
  `d_state` is D0, its address bits `[63:32]` are 0 and its bits
  `[31:k]` equal those of `barn_base`, with k = `BAR0_ADDR_W` for BAR0 and
  12 for BAR2 (BAR2 is 4 KiB). Any other memory request, those outside
  D0 included, is an Unsupported Request
  [cite: PCIe Base Spec, rev 3.0, §2.3.1]
  [cite: PCIe Base Spec, rev 3.0, §5.3.1.4].
- `bus_master_en` 0 or `d_state` not D0 stops `tl` taking requests on
  `rq`; `cfg` already gates the interrupts it asks for.
- Not on this interface: the MSI and MSI-X state (`cfg` resolves
  interrupts), the error-reporting enables (`cfg` decides messages), and
  the maximum payload and read request sizes (`tl` uses the fixed 128
  bytes, decision F2; `cfg` drives the [status outputs](APP.md#status)).
  The completion timeout value is fixed in `tl`; `cfg` advertises no
  programmable range (Completion Timeout Ranges Supported 0000b), which
  obliges that fixed value to lie between 50 µs and 50 ms
  [cite: PCIe Base Spec, rev 3.0, §7.8.15]. `cfg` does advertise
  Completion Timeout Disable, which `tl` honors through `cpl_timeout_dis`:
  while it is 1, an outstanding read waits for its completions with no
  timeout, and when it falls each read still outstanding gets a fresh
  full timeout from that cycle (firstmate decision as the captain's delegate, which the captain
  may override).
- The read completion boundary is fixed at 64 bytes: `cfg` hardwires the
  Link Control RCB bit to 0 (64 bytes), so software cannot set a boundary
  that disagrees with how `tl` splits completions
  [cite: PCIe Base Spec, rev 3.0, §7.8.7].

### Requester state

A level from `tl`, for the Device Status register (firstmate decision as
the captain's delegate, which the captain may override).

| Net | Dir | Width | Clock | Meaning |
| --- | --- | --- | --- | --- |
| `tl_np_pending` | tl→cfg | 1 | `clk_i` | 1 while any application non-posted request taken on `rq` still awaits its final completion or its timeout. `cfg` shows it as Device Status Transactions Pending [cite: PCIe Base Spec, rev 3.0, §7.8.5]. |

### Error events

One-cycle pulses from `tl`, one bit per kind; events of one kind in one
cycle give one pulse. `cfg` turns them into status bits and messages
([#285](https://github.com/amichai-bd/pcie-factory/issues/285)).

| Bit of `tl_err` (tl→cfg, 9, `clk_i`) | Event |
| --- | --- |
| 0 | Unsupported Request, posted: a posted request or message `tl` does not serve; dropped, no completion. |
| 1 | Unsupported Request, non-posted: `tl` answered it with an Unsupported Request completion. |
| 2 | Malformed TLP: a received TLP failed a `tl` format check; dropped. |
| 3 | Poisoned request: a received request with its poisoned bit set. A posted one is dropped; a non-posted one (a configuration write, say) is discarded without effect and answered with an Unsupported Request completion [cite: PCIe Base Spec, rev 3.0, §2.7.2.2]. Only bit 3 pulses, never bit 1 as well, so `cfg` counts the event once. A poisoned request that is otherwise an Unsupported Request is reported as the Unsupported Request (bit 0 or 1) only. |
| 4 | Poisoned completion: a received completion for one of this function's reads with its poisoned bit set. |
| 5 | Unexpected completion: a completion that matches no outstanding read. |
| 6 | Completion timeout: a read's completions did not all arrive in time. |
| 7 | Received Unsupported Request completion (the Status register's Received Master Abort). |
| 8 | Received Completer Abort completion (the Status register's Received Target Abort). |

Which checks raise each bit, and the order of the request and completion
checks, are the `tl` HAS's ([#286](https://github.com/amichai-bd/pcie-factory/issues/286))
[cite: PCIe Base Spec, rev 3.0, §2.3.1]
[cite: PCIe Base Spec, rev 3.0, §2.3.2]
[cite: PCIe Base Spec, rev 3.0, §2.8]. `core` never sends Completer Abort,
so there is no Signaled Target Abort event.

### Messages

`cfg` asks `tl` to send an interrupt write or an error message.

| Net | Dir | Width | Clock | Meaning |
| --- | --- | --- | --- | --- |
| `msg_valid` | cfg→tl | 1 | `clk_i` | A message is offered. |
| `msg_ready` | tl→cfg | 1 | `clk_i` | `tl` takes it at this edge. |
| `msg_kind` | cfg→tl | 2 | `clk_i` | 0 interrupt write, 1 ERR_COR, 2 ERR_NONFATAL, 3 ERR_FATAL. |
| `msg_addr` | cfg→tl | 64 | `clk_i` | Interrupt write: the message address, bits `[1:0]` 0; `tl` uses a 3-DW header when bits `[63:32]` are 0. Don't care otherwise. |
| `msg_data` | cfg→tl | 32 | `clk_i` | Interrupt write: the DW written with all four bytes enabled; under MSI, the Message Data in `[15:0]` and 0 in `[31:16]`. Don't care otherwise. |

- An interrupt write is a 1-DW posted memory write
  [cite: PCIe Base Spec, rev 3.0, §6.1.4]. `tl` sends it after every
  posted request it took on `rq` before, so the interrupt never overtakes
  the data it signals ([ordering](APP.md#ordering)).
- An error message is a Message routed to the Root Complex with no data,
  with the code of its kind [cite: PCIe Base Spec, rev 3.0, §2.2.8.3].
- A message taken is sent unless the link goes down first.
- <a id="withdrawal"></a>**Withdrawal.** `msg_valid` falls without
  `msg_ready` only when the offered message may no longer go out: the
  function leaves D0, Bus Master Enable is cleared (interrupt writes), the
  enable of the offer's interrupt mode is cleared (MSI Enable for an MSI
  write, MSI-X Enable for an MSI-X write), or reset. `tl` takes a message
  only in a cycle where it sees `msg_valid` and raises `msg_ready`, samples
  the fields in that cycle, and never assumes an offer stays up. A
  withdrawn interrupt write becomes pending, or is dropped when neither
  MSI nor MSI-X is enabled ([resolution](#interrupt-resolution)); a
  withdrawn error message is
  discarded, its status bits already set (firstmate decision as the
  captain's delegate, which the captain may override).
- <a id="outside-d0"></a>**Outside D0.** A function outside D0 originates
  no requests other than PME, which `core` does not implement
  [cite: PCIe Base Spec, rev 3.0, §5.3.1.4]. So while `d_state` is not D0,
  `cfg` offers no message of any kind: interrupts wait as pending
  ([resolution](#interrupt-resolution)), and an error event still sets
  its status bits but sends no ERR_COR, ERR_NONFATAL or ERR_FATAL, then
  or later on the return to D0 (firstmate decision as the captain's
  delegate, which the captain may override). `tl` still sends every
  completion: configuration completions, the one for the Power State
  write that returns the function to D0, and the Unsupported Request
  completions for memory requests outside D0.
- **No wait between the two handshakes.** `cfg` never holds
  `reg_req_ready` or delays `reg_cpl_valid` waiting for `msg_ready`, and
  `tl` never holds `msg_ready` waiting for `reg_cpl_valid` or a
  `reg_req_ready`. A BAR2 write that unmasks a pending vector makes `cfg`
  offer a message while `tl` waits for that write's `reg_cpl_valid`; the
  rule keeps the two IPs, specified apart, free of deadlock.

### Interrupt resolution

The application's [interrupt port](APP.md#interrupts) belongs to `cfg`,
which resolves each request and answers it:

An interrupt is **allowed** while `bus_master_en` is 1 and `d_state` is
D0 (decision record [0004](../../decisions/0004-m3-grill.md#updates),
Q11). Outside D0 the function originates no requests: no `rq` requests,
no interrupt writes and no error messages; it still sends completions
([outside D0](#outside-d0)).

1. Neither MSI nor MSI-X enabled: dropped (there is no INTx).
2. MSI-X enabled (it wins if software also enabled MSI): `cfg` reads the
   vector's table entry. If the vector or the function is masked, or the
   interrupt is not allowed, it sets the vector's pending bit (status
   pending). Otherwise it offers an interrupt write with the entry's
   address and data. An MSI-X request is never dropped.
3. MSI enabled: if allowed, an interrupt write with the MSI address and
   data. If not, `cfg` sets its one internal MSI pending flag (status
   pending) and offers the write once allowed; the flag clears when the
   write is taken, when software clears MSI Enable, or at reset. MSI has
   one vector and no per-vector masking (decision F4), so several
   requests while pending give one write.

`irq_status_o` reports sent when `tl` takes the write. A pending vector
is sent once it is unmasked (vector and function mask both clear) and
the interrupt is allowed, in whichever order the two happen: `cfg` then
clears the pending bit and offers the write itself, and the application
sees no report for it [cite: PCIe Base Spec, rev 3.0, §6.1.4]. Unmasking
while the interrupt is not allowed leaves the bit set. Pending bits are
read through BAR2 like the table.

MSI-X pending bits are kept while MSI-X Enable is 0, as the
software-visible state of the pending-bit array: clearing MSI-X Enable,
or switching to MSI, neither clears nor sends them. While MSI-X is
disabled nothing is sent from them; once it is enabled again, each
pending vector goes out by the rule above. Only reset clears them
(firstmate decision as the captain's delegate, which the captain may
override). A vector at or above `MSIX_TABLE_SIZE`
is an application error that the RTL checks with an assertion.

## Link to cfg

Inside `core`, `cfg` also takes the `link` error pulses, for its
correctable and uncorrectable status bits and messages:

| Net | Dir | Width | Clock | Meaning |
| --- | --- | --- | --- | --- |
| `dl_err` | link→cfg | 7 | `clk_i` | The `link` `dl_err_o` ([`dll` error bits](../dll/HAS.md#errors)). |
| `rcv_err` | link→cfg | 1 | `clk_i` | The `link` `rcv_err_o`: a receiver error. |

Both stay `core` output ports too. One event can pulse both (a TLP with a
symbol error pulses `rcv_err`, then Bad TLP); `cfg` counts each as its own
error, as the [`link` errors](../link/HAS.md#errors) state.
