# pcie_tl ordering and credits

The queues of [`pcie_tl`](HAS.md), the order in which requests and
completions leave them, and the credit rules. Part of the `tl` HAS.

## Receive queues

| Queue | Holds | Leaves `tl` |
| --- | --- | --- |
| Posted | Every posted request and message, in arrival order: BAR0 writes, BAR2 writes, and the posted requests `tl` drops or answers ([routing](ROUTING.md)). At most `FC_PH` headers and `FC_PD` × 16 bytes of data. | In arrival order, one at a time: a BAR0 write when its `eop` beat is taken on `cq`; a BAR2 write when `cfg` takes its last DW access; any other when it reaches the head. |
| Non-posted, `cq` line | BAR0 reads, in arrival order. | When taken on `cq`, which needs a free [handle](COMPLETIONS.md#read-handles). |
| Non-posted, `cfg` line | Configuration requests, BAR2 reads and Unsupported Requests, in arrival order. | When `cfg` takes the access (the last one of a BAR2 read), or when the Unsupported Request completion enters the transmit completion queue. |

The two non-posted lines share `FC_NPH` headers and `FC_NPD` × 16 bytes.
A malformed non-posted TLP leaves at once.

## Ordering

`tl` keeps these rules, a subset of what PCI Express allows
[cite: PCIe Base Spec, rev 3.0, §2.4.1]:

| Rule | How |
| --- | --- |
| Posted requests stay in order. | One posted queue, in order, to whichever of `cq` and `cfg` it goes. |
| A non-posted request never passes an earlier posted one. | It leaves only once every posted request that arrived before it has left. |
| A posted request passes a non-posted one that waits. | A BAR0 write goes to `cq` while a BAR0 read waits for a handle ([APP ordering](../core/APP.md#ordering)). |
| Non-posted requests pass one another across the two lines. | Configuration, BAR2 and Unsupported Requests never wait for BAR0 reads, nor BAR0 reads for them ([`core` credits](../core/HAS.md#credits)). |
| A received completion never passes an earlier received posted request. | It is offered on `rc` only once every posted request that arrived before it has left `tl`. It never waits for a non-posted one. |
| Sent posted requests stay in order. | One transmit posted line, in the order taken: `rq` writes, interrupt writes and error messages from `cfg`, PME_TO_Ack. An interrupt write therefore follows every `rq` write taken before it. |
| Sent non-posted requests stay in order, and never pass an earlier sent posted request. | One transmit non-posted line: `rq` reads in the order taken, each leaving only after every posted request taken before it. |
| A sent posted request passes a non-posted one that waits [cite: PCIe Base Spec, rev 3.0, §2.4.1]. | The posted line never waits for the non-posted line: a write goes while an earlier read waits for non-posted credits, which avoids the deadlock the spec rule exists for. |
| A sent completion never passes an earlier sent posted request. | A completion leaves only after every posted request taken into the posted line before it: before its `cc` `sop` beat for a BAR0 read, before it is formed for the others ([APP ordering](../core/APP.md#ordering)). |

**Transmit arbiter.** At most one TLP starts per cycle. The heads of the
posted line, the non-posted line and the completion queue (each when the
rules above let it go) take turns, round robin; a head whose credits the
link does not show available is skipped, so a TLP waiting for credits
never holds back one of another line. The application's
obligation never to make taking a `cq` write depend on an `rc` beat
([APP](../core/APP.md#ordering)) keeps these rules free of deadlock.

**Requests already taken.** When `bus_master_en_i` falls or `d_state_i`
leaves D0, `rq` takes no new request, but requests and messages already
in the transmit lines are still sent: completions may wait
behind them, the configuration completion that restores the function
among them.

## Messages from cfg

`tl` takes a message only at an edge where it sees `msg_valid_i` 1 and
drives `msg_ready_o` 1, and samples `msg_kind_i`, `msg_addr_i` and
`msg_data_i` at that edge. `cfg` may withdraw an offer (drop `msg_valid_i`
without `msg_ready_o`) when the message may no longer go out: outside D0,
Bus Master Enable or the matching enable cleared, or reset
([withdrawal](../core/TL_CFG.md#withdrawal)); `tl` never
assumes an offer stays up and keeps no state for an offer it has not
taken. `msg_ready_o` depends only on room in the transmit posted line,
never on the register-access handshakes (the
[no-wait rule](../core/TL_CFG.md#messages)). A message taken is sent
unless `tl` is reset first.

## Credits

`link` advertises finite posted and non-posted credits and infinite
completion credits ([`core` credits](../core/HAS.md#credits)).

- **Receive.** `tl` takes every TLP at once. The posted and non-posted
  queues hold at most what the advertised credits allow, because each
  request's credits return to the host only when it leaves `tl`
  (decision record [0004](../../decisions/0004-m3-grill.md#updates), Q9):
  a credit-correct host never fills them past that, and the `dll`'s own
  receive check stops a host that is not. Completions need no release:
  `tl` holds received completion data only for its at most 8 outstanding
  reads of at most 128 bytes, and drops anything else at once.
- **Release.** In the cycle a request [leaves `tl`](#receive-queues),
  `tl` pulses `fc_rel_p_o` or `fc_rel_np_o` with its data credits
  ([credit release](HAS.md#credit-release)); the `link`
  [credit-release](../dll/HAS.md#credit-release) inputs, with
  `FC_RELEASE` 1, return those credits to the host then, not at delivery.
- **Transmit.** `tl` offers a TLP's `sop` beat only when the matching
  `fc_*_avail_i` show at least 1 header credit and its data credits
  (all ones is infinite); the `dll` gates on the same credits.
