# pcie_tl MAS

How `pcie_tl` implements its [HAS](../../arch/tl/HAS.md). Wave 2 builds it
in three issues: the receive path
([#290](https://github.com/amichai-bd/pcie-factory/issues/290), built),
the transmit path with completions and MSI/MSI-X writes
([#291](https://github.com/amichai-bd/pcie-factory/issues/291), built), and
completion tracking and timeout
([#292](https://github.com/amichai-bd/pcie-factory/issues/292)). Each
block is a file of the one `tl` IP; the tracker attaches at the
[slots](#slots) without reworking the built blocks. The HAS and its pages
([TLP formats](../../arch/tl/TLP.md), [routing](../../arch/tl/ROUTING.md),
[completions](../../arch/tl/COMPLETIONS.md),
[ordering](../../arch/tl/ORDERING.md)) own the rules; this page owns how
the blocks apply them.

## Top

```mermaid
flowchart LR
  rx_in["tlp_rx_*"] --> rx["u_rx<br/>parse, checks, route"]
  st["register state"] --> rx
  rx -- "t_pq_ent, data DWs" --> q["u_rx_q<br/>posted queue, cq line,<br/>cfg line, epochs, release"]
  rx -- "t_cq_ent, t_cfg_ent" --> q
  rx -- "malformed non-posted" --> q
  rx -- "err 3:0" --> err["tl_err_o"]
  rx -- "t_slot_pwr" --> spl["slot_pwr_*_o"]
  rx -. "cpl_beat (#292)" .-> trk["tracker slot"]
  q -- "posted head, data, cq line" --> cq["u_cq<br/>cq stream, handles"]
  q -- "posted head, data, cfg line" --> reg["u_reg<br/>register access"]
  cq --> cq_out["cq_*"]
  cq -- "rd_take, rd_ctx" --> cpl["u_cpl<br/>handles, cc, records,<br/>two completion formers"]
  cpl -- "hd_free" --> cq
  cc_in["cc_*"] --> cpl
  reg --> reg_out["reg_req_*"]
  reg -- "cpl_rec" --> cpl
  q -- "pme_ack" --> txq["u_txq<br/>rq, messages, PME_TO_Ack;<br/>posted and non-posted lines"]
  rq_in["rq_*, msg_*"] --> txq
  txq -. "rq_rd_take (#292)" .-> trk
  cpl -- "completion queues" --> tx["u_tx<br/>arbiter, headers"]
  txq -- "posted, non-posted lines" --> tx
  tx --> tx_out["tlp_tx_*"]
  tx -. "rq_sent (#292)" .-> trk
  q --> rel["fc_rel_*_o"]
```

**Reset.** `tl` is in reset while `rst_i` is 1 or `dl_up_i` is 0
([HAS, reset](../../arch/tl/HAS.md#reset)): the top forms `rst` from both
and every block takes it as its synchronous reset. Every handshake
output (`tlp_rx_ready_o`, `tlp_tx_valid_o`, `cq_valid_o`, `cc_ready_o`,
`rq_ready_o`, `msg_ready_o`, `reg_req_valid_o`, `fc_rel_p_o`,
`fc_rel_np_o`, `tl_err_o`, `slot_pwr_valid_o`) is also gated by `rst` at the top, so it is 0
in the first cycle `dl_up_i` falls, before the registers clear. The ready
and valid inputs reach the blocks gated the same way.

**Ports.** The top ports are the HAS ports, flat (they mirror the `link`,
tl-cfg and application interfaces the HAS names). Inside, the blocks
exchange the packed structs of the [package](#package). The parameters
keep the HAS ranges (`COMMON_ASSERT_PARAM`, one per parameter).

### Slots

What the tracker (#292), not built yet, connects to, and the tie each
slot carries until then. Every slot net is MAS-named; the testbench's
bind tap reads the outgoing ones, and also the block nets `cpl_rec`,
`rd_take`, `rd_ctx` and `pme_ack` ([DV plan](../../dv/tl/DV_PLAN.md)).
The completion former, `cc`, `rq`, the messages and the transmit path
(#291) are built: `cpl_room`, `hd_free` and `pme_room` are driven by
[`u_cpl`](#completions) and [`u_txq`](#transmit-lines).

| Slot net (top) | Type | Direction | Owner | Tie until then | Contract |
| --- | --- | --- | --- | --- | --- |
| `cpl_beat` | `t_rx_beat` | `u_rx` to tracker | #292 | unread | Every beat of a received completion with a defined Fmt, `sop` to `eop`, in the cycle it arrives, a digest DW included (the tracker reads Length data DWs). `tl` never stalls them. |
| `rc_arrive`, `rc_ep`, `p_none`, `p_known`, the posted head's `ep[LINE_RC]` | | tracker and `u_rx_q` | #292 | `rc_arrive` 0 | The rc line's ordering: a completion that arrives takes `rc_ep` and arms the line; it may be offered on `rc` once `p_none`, or `p_known` and the posted head's rc epoch is after it ([receive queues](#receive-queues)). `rc_arrive` in the same cycle as a posted push counts as after that posted request. While a later posted request loads into an empty posted queue, `p_none` and `p_known` are both 0 for a cycle, so the tracker latches a completion's eligibility once earned, as the cq and cfg lines do (`ok_q`), and an `rc` offer once up stays up until taken (APP). |
| `rq_rd_take`, `rq_rd_ctx` | `logic`, `t_rq_ctx` | `u_txq` to tracker | #292 | unread | A read taken on `rq` (its `sop` beat): its tag, address bits 11:2, length and byte enables. The tag is in use from then ([requester tags](../../arch/tl/COMPLETIONS.md#requester-tags)). |
| `rq_sent`, `rq_sent_tag` | `logic`, 3 bits | `u_tx` to tracker | #292 | unread | The link took the `sop` beat of that read's TLP: its timer starts ([completion timeout](../../arch/tl/COMPLETIONS.md#completion-timeout)) and completions for it may match. |
| `trk_err` | 9 bits | tracker to the top | #292 | 0 | ORed into `tl_err_o` (bits 2 and 4 to 8). |
| Tracker outputs | | | #292 | reset values | `rc_*`, `tl_np_pending_o`. |

## Package

[`pcie_tl_pkg`](../../../../src/rtl/tl/pcie_tl_pkg.sv) holds the Fmt,
Type, message, routing and completion status codes, the `tl_err_o` bit
numbers, the enums (`t_cls` credit class, `t_out` receive outcome,
`t_pkind` posted entry kind, `t_ckind` cfg line kind, `t_crkind` record
kind, `t_reg_st`, `t_pxkind` transmit posted entry kind, `t_txsel` the
arbiter's choice), the structs (the decoded header `t_hdr`, the TLP being
received `t_rx_cur`, the queue entries `t_pq_ent`, `t_cq_ent`,
`t_cfg_ent`, `t_txp_ent`, `t_txnp_ent`, the queued completion
`t_cpl_desc`, the former's read context `t_cpl_ctx`, the block nets
`t_hd_ctx`, `t_cpl_rec`, the slot types `t_rx_beat`, `t_rq_ctx`, the slot power payload
`t_slot_pwr`, and each
block's state), and the pure helpers:

| Helper | Does |
| --- | --- |
| `be_word`, `hdr_decode` | A big-endian header DW as an address word; every header field from the stream DWs ([TLP formats](../../arch/tl/TLP.md)). |
| `credit_class`, `data_credits` | The class and data credits the `dll` counted ([credit release](../../arch/tl/HAS.md#credit-release)). |
| `kind_known`, `is_cpl` | Step 1 of the [checks](../../arch/tl/ROUTING.md#checks); a completion with a defined Fmt. |
| `be_ok`, `cross_4k`, `msg_tc0`, `msg_silent` | Steps 3, 4 and 6; the messages dropped without an error. |
| `lowest_free` | The lowest free read handle. |
| `ep_after` | The [epoch](#receive-queues) comparison (also the [transmit epochs](#transmit-lines)). |
| `be_lo`, `be_hi`, `byte_total`, `be_mask` | The lowest and highest enabled byte, a read's total bytes T, a byte-enable mask ([byte count](../../arch/tl/COMPLETIONS.md#byte-count-and-lower-address)). |
| `dw_credits` | Data credits of a TLP sent. |
| `tx_dw0`, `tx_dw1_req` | Header DW 0 and a request's DW 1 as they sit on the stream ([TLP formats](../../arch/tl/TLP.md)). |

## Receive parser

[`pcie_tl_rx`](../../../../src/rtl/tl/pcie_tl_rx.sv) takes every beat the
`link` delivers (`tlp_rx_ready_o` is 1 out of reset). Its state
(`t_rx_state`) counts the beats of the TLP and keeps the header size and
whether it is a completion; DW 0 to 2 are captured. At the last header
DW (beat 2 or 3) the header, the captured DWs and the beat on the stream,
is judged combinationally by the [checks](../../arch/tl/ROUTING.md#checks)
in order and the [routing](../../arch/tl/ROUTING.md#routing), with the
register state of that cycle, and the result is registered as `cur_q`
(`t_rx_cur`): the outcome, the error bit, the class and data credits, and
the fields the entries need. The BAR decode compares the address bits
from 31 down to the BAR's size (a mask from `BAR0_ADDR_W`, 12 for BAR2).

Data beats are the Length DWs after the header of a TLP with data. Those
of a BAR0 or BAR2 write go to the posted data queue as they arrive; the
first of a configuration write or a message is kept in `cur_q`. A digest DW after them
(TD 1, [TLP formats](../../arch/tl/TLP.md#common-fields)) is never stored.
Every other TLP's data is not stored.

In the cycle after the `eop` the TLP's entry is pushed: a posted TLP into
the posted queue (kind `PK_CQ`, `PK_BAR2`, `PK_PME`, or `PK_DISCARD` for
an Unsupported Request, a dropped message, a malformed or a poisoned
request), a BAR0 read into the cq line, a configuration request, a BAR2
read or a non-posted Unsupported Request into the cfg line, and a
malformed non-posted TLP straight to the release (it leaves at once). Its
error event pulses on `tl_err_o` bits 3 to 0 in the same cycle. A
Set_Slot_Power_Limit with data that passed steps 1 to 6 and is not
poisoned is judged with `cur_q.spl` set; it pulses `slot_pwr_valid_o` in
that cycle too, with `t_slot_pwr` from the kept data DW (bits 7:0 the
value, 9:8 the scale), and is a `PK_DISCARD` entry like any dropped
message ([slot power limit](../../arch/core/TL_CFG.md#slot-power-limit)). A
completion with a defined Fmt pushes nothing: its beats went to the
tracker slot as they arrived. Assertions: `a_beat_outside_tlp`,
`a_sop_inside_tlp`, `a_short_tlp` (the `dll`'s whole-TLP contract) and
`a_rel_data_max`.

## Receive queues

[`pcie_tl_rx_q`](../../../../src/rtl/tl/pcie_tl_rx_q.sv) holds the
[receive queues](../../arch/tl/ORDERING.md#receive-queues):

| Queue | Entry | Size |
| --- | --- | --- |
| Posted | `t_pq_ent` | 2^⌈log2 `FC_PH`⌉ + 1 entries |
| Posted data | a DW | 2^⌈log2 (4 × `FC_PD`)⌉ + 1 DWs |
| Non-posted, cq line | `t_cq_ent` | 2^⌈log2 `FC_NPH`⌉ + 1 entries |
| Non-posted, cfg line | `t_cfg_ent` | the same |

Each holds at least what the advertised credits allow (a data credit is 4
DWs), so a credit-correct host never fills one (`a_*_overflow`). Each is
a [queue](#queue). An entry stays at its queue's head until it leaves, so
the head of the posted queue is always the oldest posted request held.

**Epochs.** A non-posted request, and a received completion (#292), may
leave only once every posted request received before it has left. Each
line l (cq, cfg, rc) has an epoch counter `ep_q[l]` and an arm flag. A
line entry takes its line's epoch and arms the line; a posted entry
pushed while a line is armed first steps that line's epoch and disarms
it, and records the epoch of every line. So a line entry's epoch is at
least that of every posted request received before it, and less than
that of every one after. Its head may go when no posted request is held,
or the posted head's epoch for its line is after its own (`ep_after`).
Right after a push into an empty posted queue the head is still loading
and no line goes then; a head that has once been free to go stays free
(`ok_q`) until it leaves, so a later posted request never holds it back.

The comparison is modulo 2^9 and exact: the held posted requests span at
most `FC_PH` epochs behind a line head, and its line's epoch can step at
most once per entry of that line received after it, which hold
non-posted credits while they wait, so at most `FC_NPH` (the tracker: at
most 24 completions outstanding). Counting the posted requests instead
would wrap: a BAR0 read can wait for a handle while any number of posted
requests pass it.

**Posted head.** A `PK_DISCARD` head leaves at once; a `PK_PME` head
leaves while `pme_room` is 1 and pulses `pme_ack`. A `PK_CQ` head leaves
when `u_cq` takes its `eop` beat, a `PK_BAR2` head when `u_reg` sees cfg
take its last access. Assertions: one posted request leaves per cycle,
each by its own owner; the lines leave only when free to go.

### Credit release

The release is registered: a request that leaves at an edge is released
in the next cycle ([HAS, credit release](../../arch/tl/HAS.md#credit-release)).
One posted request leaves per cycle at most, so `fc_rel_p_o` and
`fc_rel_pd_o` are the registered leave and its data credits. Up to three
non-posted requests leave in one cycle (a cq read, a cfg-line request and
a malformed TLP), so the headers and data credits that leave are added to
`owe_h_q` and `owe_d_q`, and `fc_rel_np_o` pulses while any header is
owed, each pulse with up to 8 owed data credits. The data owed is never
above 8 per header owed (`a_owed_data`), and the headers owed never above
`FC_NPH` (`a_owed_bound`).

### Queue

[`pcie_tl_fifo`](../../../../src/rtl/tl/pcie_tl_fifo.sv) is a
first-word-fall-through queue: 2^`ADDR_W` entries in a
[`common_sdp_ram`](../common/ram/common_sdp_ram.md), the head in its read
register. The next entry loads into the head when the head is empty or
popped and the RAM holds an entry written at an earlier edge, so a read
never meets a write, a push shows at the head two edges later, and the
head can be popped every cycle. `empty_o` covers the head and the RAM.

## cq stream

[`pcie_tl_cq`](../../../../src/rtl/tl/pcie_tl_cq.sv) offers the
[completer requests](../../arch/core/APP.md#completer-requests). With no
packet in progress it picks the cq line's head when it is free to go and
a handle is free (an eligible read is older than every waiting write),
else a `PK_CQ` posted head, and offers it in the same cycle; `t_cq_state`
then holds the choice and the handle until the `eop` is taken, so an
offered beat never changes. The header fields come from the queue head,
which stays put until the packet leaves; a write's data beats come from
the posted data head, valid only while a DW is there. A read is one beat
with the lowest free handle (`lowest_free`); its take marks the handle
busy and pulses `rd_take`. Assertions: `a_free_idle_handle`,
`a_read_without_handle`, and that the offered packet's head stays.

## Register access

[`pcie_tl_reg`](../../../../src/rtl/tl/pcie_tl_reg.sv) serves the
[register access](../../arch/core/TL_CFG.md#register-access) one access
at a time (`t_reg_state`: idle, offering, waiting for `reg_cpl_valid`).
In idle it takes the cfg line's head when it is free to go, else a
`PK_BAR2` posted head (an eligible cfg-line request is older than every
waiting posted write). An Unsupported Request makes its record at once
and leaves. Any other request is copied into `cur_q` and offered access
by access: a configuration request in one access with the request's
register, byte enables, data and, for a write, Bus and Device Number; a
BAR2 request one access per DW at the first DW index plus one per DW,
with the first DW's byte enables first, the last DW's last and all four
between. A BAR2 write's data comes from the posted data head. The offer
waits for `cpl_room` (a record will follow) or the data DW, and once up
stays up until taken (`a_offer_dropped`). The request leaves when cfg
takes its last access; each `reg_cpl_valid` of a configuration access or
a BAR2 read DW makes a record with the DW. `a_cpl_without_access` checks
cfg's side of the one-access rule.

## Completions

[`pcie_tl_cpl`](../../../../src/rtl/tl/pcie_tl_cpl.sv) turns the
[completer completions](../../arch/tl/COMPLETIONS.md#completer-completions)
into queued completions through two [completion formers](#completion-former):
`u_cc` for `cc`, `u_cf` for the register engine's records.

- **Handles.** Each `rd_take` writes the read's context (`t_cpl_ctx`:
  Requester ID, Tag, TC, Attr, address bits 6:2, Length, byte enables)
  into an 8-entry memory at its handle; `cc` reads it combinationally at
  the handle of the packet's `sop` beat, then at the handle kept in
  `t_cc_state` until the `eop`. The `eop` take pulses `hd_free` with the
  handle, which `u_cq` frees at that edge.
- **cc.** `cc_ready_o` is the former's room: 1 inside a completion, and at
  a beat that starts one only with room for the longest completion. Each
  beat taken goes to `u_cc` with the read's context. The packet's epoch
  is the cc line's at its `sop` beat (`t_cc_state.ep`), and the `sop`
  arms that line, so every completion of the packet waits for the posted
  requests taken before the `sop` and for none after
  ([APP ordering](../../arch/core/APP.md#ordering)).
- **Records.** A configuration write or an Unsupported Request is one
  completion without data, Byte Count and Lower Address formed here (T
  and B[6:0] for an Unsupported Request of a memory read, else 4 and 0;
  the locked kind for a locked read); a configuration read is one DW
  with Byte Count 4 and Lower Address 0 (`fixed4`); a BAR2 read's DWs go
  through the same split as a BAR0 read's. Each completion takes the cfg
  line epoch when it is queued and arms that line. `cpl_room` is
  `u_cf`'s room.
- Assertions: `a_cc_handle_idle` (a packet answers a handle taken and not
  yet answered), `a_cc_sop`, `a_cc_eop` (exactly the read's DWs),
  `a_rec_order` (BAR2 read records in DW order).

### Completion former

[`pcie_tl_cplf`](../../../../src/rtl/tl/pcie_tl_cplf.sv) takes a read's
DWs in address order, with its context, and queues its completions; or
takes a completion without data formed outside. Its state
(`t_cplf_state`) holds the next DW's index k in the read, the DWs still
due in the open completion, the bytes from the next completion's first
byte to the read's end (rb), and the open completion's Byte Count, Lower
Address and length.

At a DW that opens a completion (none open), the completion's length n
is the rest of the read when it is at most 32 DWs, else 32 less the DW
address bits 5:2 of that DW, which ends it on the highest 64-byte
boundary within 128 bytes ([splitting](../../arch/tl/COMPLETIONS.md#splitting)).
Its Byte Count is T for the read's first completion and rb for a later
one, its Lower Address the first enabled byte's address bits 6:0 for
the first and the DW's for a later one; rb then drops by 4n, less the
disabled bytes before the first enabled one for the first completion.
Every DW goes to a 64-DW data queue with the bytes the request did not
enable as 0 (the first DW by F, the last of a longer read by L); the
descriptor (`t_cpl_desc`) goes to a 4-entry queue with the completion's
last DW, so a queued descriptor always has all its data. Both queues are
[queues](#queue).

`room_o` is 1 while a completion is open, else while the data queue's
RAM holds at most 64 − 32 DWs and the descriptor queue is not full: a
completion that opens always fits whole, and the queue holds two of the
longest. Assertions: a DW only with room, a completion without data only
with none open, and a read never ending inside a completion.

## Transmit lines

[`pcie_tl_txq`](../../../../src/rtl/tl/pcie_tl_txq.sv) holds the
transmit posted line, its data, and the non-posted line
([ordering](../../arch/tl/ORDERING.md#ordering)).

| Line | Holds | Storage |
| --- | --- | --- |
| Posted | `t_txp_ent`: rq writes (`PX_RQ`), interrupt writes with their DW (`PX_IRQ`), error messages and PME_TO_Ack (`PX_MSG`) | 4 entries of flops; up to three go in at one edge, the head shows at the next |
| Posted data | rq write DWs | a 64-DW [queue](#queue) |
| Non-posted | `t_txnp_ent`: rq reads | a 4-entry [queue](#queue) |

- **rq.** On a `sop` beat, `rq_ready_o` is 1 when Bus Master Enable is 1,
  the function is in D0, and its line has room: a write needs a posted
  count of at most 1 and room for 32 data DWs, a read a free non-posted
  entry. A write's entry goes in at its `sop` beat, in the order taken,
  and its DWs into the posted data queue as they come; its later beats
  are always taken (`t_rq_state` counts them). A read is one beat; it
  goes into the non-posted line with the non-posted epoch, arms that
  line, and pulses `rq_rd_take`.
- **Messages.** `msg_ready_o` is 1 while the posted count is at most 2;
  `pme_room` while it is at most 3. Each source checks room for itself
  and the sources that may go in with it (rq, then messages, then
  PME_TO_Ack, in that order at one edge), so the line never overflows
  (`a_p_overflow`). Neither depends on any other handshake
  ([no-wait rule](../../arch/core/TL_CFG.md#messages)).
- **Epochs.** As in the [receive queues](#receive-queues): the
  non-posted line and the two completion queues each have an epoch and
  an arm flag; a posted entry steps every armed line's epoch and records
  them all. An entry of a line may go when the posted line is empty or
  the posted head's epoch for its line is after its own. The posted head
  is a flop, so there is no loading cycle; the comparison is exact since
  a line's epoch steps at most once per entry of that line still held.
- **Ready.** The posted head may go once all its data is queued: an rq
  write when the posted data queue holds at least its length (its data
  is first there, since earlier writes have gone).
- Assertions: the rq obligations of [APP](../../arch/core/APP.md#requester-requests)
  (length 1 to 32, one 4 KiB block, a read one beat, `sop` and `eop` in
  place); room as checked.

## Transmit arbiter

[`pcie_tl_tx`](../../../../src/rtl/tl/pcie_tl_tx.sv) sends one TLP at a
time ([transmit arbiter](../../arch/tl/ORDERING.md#ordering)). A head is
eligible when its order allows it (the epoch rule) and the link shows 1
header credit and its data credits for its class (`>=` passes all ones).
Round robin over the posted line, the non-posted line and the completion
queue picks one; the completion queue is the two completion formers'
queues, which alternate when both are eligible (`cf_turn`). The choice,
and this function's ID (`bus_num_i`, `dev_num_i`), are held in
`t_tx_state` from the `sop` offer to the `eop` take, so an offered beat
never changes. Header DWs are formed from the head entry
([TLP formats](../../arch/tl/TLP.md)): a 3-DW memory request header when
address bits 63:32 are 0, a 4-DW one otherwise; a message's 4-DW header;
a completion's 3-DW header with the request's TC and Attr bits 1:0. Data
DWs come from the line's data queue (an interrupt write's from its
entry). The head and its data leave at the `eop` take; `rq_sent` pulses
at an rq read's `sop` take. Assertion: the line sent keeps its head.

## Decisions

| Choice | Rationale | Rejected |
| --- | --- | --- |
| Judge at the last header DW, push in the cycle after the `eop` | Data is routed as it arrives, so only the writes served are stored and a digest never is; the push needs no data-complete tracking. | Judging after the `eop` (every TLP's data stored, then skipped one DW per cycle). |
| Entries stay at the queue head until they leave | The posted head is the oldest posted request held, which is all the ordering needs; the cq stream's fields stay put without a copy. | Popping on start and keeping an in-flight copy per consumer. |
| Per-line epochs for ordering | Exact in 9 bits by the credit bounds; one comparison at each head. | Arrival counters (wrap while a read waits for a handle); a flag per waiting entry (cannot be updated in a RAM). |
| Release registered, non-posted releases owed and merged | A request leaves on handshakes `tl` does not control (`cq_ready_i`, `reg_req_ready_i`), so three can leave in one cycle; the `dll` only adds the credits, so merging them is exact. | Holding one handshake back (`cfg` would wait on the application). |
| Queues sized by the advertised credits, in RAM | The HAS sizing; up to 127 headers and 8188 data DWs. | Flop arrays. |
| Completion beats passed raw to the tracker slot | #292 owns the completion checks and buffer; the receive path keeps no completion state. | Parsing completion headers here. |
| Read handles: busy flags in `u_cq`, context in `u_cpl` | The take is in `u_cq`; the context is what the completions need. | A handle table in `u_cq` read by `u_cpl`. |
| Two completion formers, one queue each, alternating in the completion turn | `cc` and cfg records arrive independently; one data queue would interleave their DWs. Completions of different requests have no order between them. | One former with a merge buffer in front. |
| Room for the longest completion at a completion's first DW | `cc_ready_o` and `cpl_room` then need no input combinationally; a 64-DW queue still holds two completions. | Room for the completion's own length (a path from `cc_handle_i` to `cc_ready_o`). |
| Transmit posted line in flops, up to three entries at one edge | An rq write, a message and a PME_TO_Ack can be taken together; none waits on another, and the head is visible at the next edge for the epoch rule. | A RAM queue with a holding register per source (a held message would be taken before an rq write but enter after it). |
| An rq write enters the posted line at its `sop` beat and goes out whole | The order taken is the `sop` order (APP); a write never stalls the link mid-TLP waiting for the application. | Entering at its `eop` (a message taken during the write's data would pass it); cut-through. |
| The ID held from the `sop` offer | A configuration write may change `bus_num_i` while a TLP waits for the link; an offered beat must not change. | Forming DW 1 when it is taken. |

## Timing

- One clock, `clk_i`; reset synchronous (`rst`); no crossing.
- A TLP whose `eop` is taken at edge E: its error event and slot power
  event pulse and its entry is pushed in the cycle after E; it reaches its queue head two
  edges later, so a request with nothing ahead of it is offered on `cq`,
  or handed on as an Unsupported Request, in the third cycle after E,
  and offered on `reg_req` in the fourth.
- A BAR0 write goes out on `cq` one beat per cycle while the application
  takes every beat; a BAR0 read is one beat, and reads follow each other
  in back-to-back cycles while handles are free.
- A register access is offered in the cycle after `u_reg` starts its
  request and after each `reg_cpl_valid` of a multi-DW BAR2 request.
- A request that leaves at edge E is released in the cycle after E, a
  non-posted one later when another is released then.
- A completion queued at edge E is offered on `tlp_tx_*` from the second
  cycle after E (its descriptor queue head), when nothing else is being
  sent and it is its turn; a configuration read's completion therefore
  starts two cycles after the cycle of its `reg_cpl_valid`. An rq request taken at
  edge E is offered from the next cycle (posted, once all its data is
  queued) or the second (non-posted). A TLP goes out one beat per cycle
  while the link takes them, with no gap, and the next starts in the
  cycle after its `eop` is taken.
- Outputs that depend on inputs combinationally: the gating by `rst`
  (`rst_i`, `dl_up_i`); `rq_ready_o` on a `sop` beat, from `rq_write_i`
  (which line's room), `bus_master_en_i` and `d_state_i`; and, while no
  TLP is offered, `tlp_tx_*` from `fc_*_avail_i` (which head may start)
  and `bus_num_i`, `dev_num_i` (its DW 1). The critical path is the
  receive judgment, from
  `tlp_rx_data_i` through `hdr_decode`, the BAR compares (32-bit masked
  equality), the 4 KiB check (a 13-bit add) and the outcome mux into
  `cur_q`.

## Files

| File | Holds |
| --- | --- |
| [`pcie_tl_pkg.sv`](../../../../src/rtl/tl/pcie_tl_pkg.sv) | [Package](#package). |
| [`pcie_tl_fifo.sv`](../../../../src/rtl/tl/pcie_tl_fifo.sv) | [Queue](#queue). |
| [`pcie_tl_rx.sv`](../../../../src/rtl/tl/pcie_tl_rx.sv) | [Receive parser](#receive-parser). |
| [`pcie_tl_rx_q.sv`](../../../../src/rtl/tl/pcie_tl_rx_q.sv) | [Receive queues](#receive-queues), epochs, [credit release](#credit-release). |
| [`pcie_tl_cq.sv`](../../../../src/rtl/tl/pcie_tl_cq.sv) | [cq stream](#cq-stream). |
| [`pcie_tl_reg.sv`](../../../../src/rtl/tl/pcie_tl_reg.sv) | [Register access](#register-access). |
| [`pcie_tl_cplf.sv`](../../../../src/rtl/tl/pcie_tl_cplf.sv) | [Completion former](#completion-former). |
| [`pcie_tl_cpl.sv`](../../../../src/rtl/tl/pcie_tl_cpl.sv) | [Completions](#completions): handles, `cc`, records. |
| [`pcie_tl_txq.sv`](../../../../src/rtl/tl/pcie_tl_txq.sv) | [Transmit lines](#transmit-lines), `rq`, messages, transmit epochs. |
| [`pcie_tl_tx.sv`](../../../../src/rtl/tl/pcie_tl_tx.sv) | [Transmit arbiter](#transmit-arbiter), headers. |
| [`pcie_tl.sv`](../../../../src/rtl/tl/pcie_tl.sv) | [Top](#top): parameter checks, reset, the blocks, the [slots](#slots). |
| [`tl.rtl.f`](../../../../src/rtl/tl/tl.rtl.f) | The shared vocabulary, the shared RAM, the package, the blocks, the top. |
