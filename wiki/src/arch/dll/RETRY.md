# pcie_dll retry

Sequence numbers, the LCRC, Ack and Nak, and replay: how `pcie_dll`
delivers every TLP exactly once and in order. Part of the
[`dll` HAS](HAS.md). All sequence arithmetic is modulo 4096
[cite: PCIe Base Spec, rev 3.0, §3.5.2.1].

## TLP framing

The packet handed to `phy` for a TLP (`pkt_*_dllp` 0) is, in order:

| Bytes | Field | Sent | Used on receive |
| --- | --- | --- | --- |
| 0 | bits 7:4 reserved, bits 3:0 sequence number bits 11:8 | 0, then the TLP's sequence number | bits 7:4 ignored |
| 1 | sequence number bits 7:0 | | |
| 2 to n+1 | the TLP, n bytes | as taken from the upper stream | stored and delivered unchanged |
| n+2 to n+5 | [LCRC](#lcrc) | over bytes 0 to n+1 | checked |

n is 12 to 144, a multiple of 4, so the packet is 18 to 150 bytes and
always an even number of `phy` beats.

## LCRC

A 32-bit CRC over the sequence-number bytes and the TLP
[cite: PCIe Base Spec, rev 3.0, §3.5.2.1]:

- Polynomial `0x04C11DB7` (x^32 + x^26 + x^23 + x^22 + x^16 + x^12 +
  x^11 + x^10 + x^8 + x^7 + x^5 + x^4 + x^2 + x + 1), register seeded with
  all ones.
- Bytes fed in order from byte 0, each byte bit 0 first.
- The remainder is complemented and sent highest-order bit first: LCRC
  byte k holds remainder bits 31−8k down to 24−8k, bit-reversed. This is
  the standard CRC-32 convention (the value of `zlib.crc32`, low byte
  first); its exact mapping is an [open item](HAS.md#open-items).
- A nullified TLP carries the LCRC with every bit inverted. This `dll`
  never sends one but accepts them ([receiving TLPs](#receiving-tlps)).

Example, computed from this definition and checked independently by the
[self-tests](../../../../src/arch/dll/test_pcie_dll_arch.py): sequence 0
and the memory-read TLP `00 00 00 01 00 01 00 0F 00 00 00 00` give the
LCRC bytes `E1 F1 E7 E2`.

## Transmitter state

[cite: PCIe Base Spec, rev 3.0, §3.5.2.1]

| Name | Width | Reset (DL_Inactive) | Meaning |
| --- | --- | --- | --- |
| NEXT_TRANSMIT_SEQ | 12 | 0 | One past the newest TLP sent at least once. |
| ACKD_SEQ | 12 | 4095 | The newest TLP acknowledged. |
| REPLAY_NUM | 2 | 0 | Replays since the last forward progress. |
| REPLAY_TIMER | 9 | 0, stopped | Cycles since the timer last (re)started. |

A TLP accepted from the upper stream gets the next sequence number in
order (the first is 0); NEXT_TRANSMIT_SEQ moves past it when it is first
handed to `phy`. At most 2047 TLPs hold a sequence number and are not
yet acknowledged, counting those accepted and not yet sent: with N the
next sequence number to assign, `(N − ACKD_SEQ − 1)` stays below 2047, and
the [transmit rule](HAS.md#transmit) holds a new TLP back otherwise. The
1024-byte replay buffer reaches its limit long before.

## Replay buffer

Holds every TLP from its acceptance until an Ack or Nak acknowledges it,
as its framed packet (sequence number, TLP and LCRC, at most 150 bytes),
oldest first. Capacity: 1024 bytes, fixed. A TLP is accepted only when it
fits ([transmit](HAS.md#transmit)).

**Sizing** (Q6, for the Gen1 x1 Ack latency): after a TLP's last beat
leaves `dll`, its Ack can take 22 cycles through two `phy` stacks, 4 to be
checked, up to 118 at the partner (the spec's limit, below), 4 for the Ack
DLLP and 22 back: about 170 cycles, or 340 bytes at 2 bytes per cycle,
plus the TLP itself, 150. 512 bytes would be the bare minimum; the model
shows that 640 bytes already keep the line full with a partner at the
limit ([`ack_spec_max`](../../../../src/arch/dll/perf/ack_spec_max.yaml)),
and 1024 bytes (6 maximum TLPs) leave margin for slower stacks.

## Timers

All in `clk_i` cycles at 125 MHz; a symbol time at 2.5 GT/s is 4 ns, half
a cycle.

| Timer | Value | Rule |
| --- | --- | --- |
| Replay timeout | 356 cycles | 711 symbol times for a 128-byte maximum payload on x1 at 2.5 GT/s, rounded up; the timer may expire later than the limit but never earlier [cite: PCIe Base Spec, rev 3.0, §3.5.2.1]. |
| Ack latency | 32 cycles | This design's choice; with a TLP in progress ahead of it (at most 76 cycles) an Ack still starts within the 237-symbol-time (118-cycle) limit [cite: PCIe Base Spec, rev 3.0, §3.5.3.1]. |
| InitFC resend | 128 cycles | At most 34 µs apart [cite: PCIe Base Spec, rev 3.0, §3.3.1]. |
| UpdateFC refresh | 2048 cycles | At most 30 µs apart [cite: PCIe Base Spec, rev 3.0, §2.6.1.2]. |

**Replay timer** [cite: PCIe Base Spec, rev 3.0, §3.5.2.1]:

- Starts from 0, when stopped, as the last beat of any TLP (new or
  replayed) is taken by `phy`.
- Restarts from 0, running, when an Ack or Nak acknowledges at least one
  TLP and TLPs that were sent remain unacknowledged (even if a replay had
  stopped it); stops at 0 when none remain.
- Stops at 0 when a replay starts (it starts again with the first
  replayed TLP's last beat).
- Holds its value while `ltssm_state_i` is not an L0 code (`0x20` to
  `0x2F`): in Recovery, and through a pass from Recovery to Configuration
  and back, during which `link_up_i` stays 1.
- At 356 it stops, pulses Replay timeout on `err_o`, and requests a
  [replay](#replay).

## REPLAY_NUM

Counts replays without forward progress
[cite: PCIe Base Spec, rev 3.0, §3.5.2.1]:

- Each replay that starts (by Nak or timeout) first adds 1. From 3 it
  rolls over to 0: the `dll` pulses `retrain_req_o` and REPLAY_NUM
  rollover on `err_o` in that cycle, and the replay is held.
- **Rollover hold.** From the rollover, no TLP (replayed or new) starts
  on the `phy` stream until `ltssm_state_i` has left L0 and then shown an
  L0 code again: the retrain has run. DLLPs are not held. If the LTSSM
  has not left L0 within 1024 cycles of the rollover (the `phy` ignored
  the request, which it does outside L0), the hold ends then. Once the
  LTSSM has left L0 there is no time bound: the `mac` returns to L0 or
  falls to Detect, which drops `link_up_i` and resets the `dll`. The
  1024 cycles exceed the `mac`'s 4 µs (500-cycle) retrain target
  ([`mac` HAS, Targets](../mac/HAS.md#targets)).
- An Ack or Nak that acknowledges at least one TLP sets it to 0.

## Replay

A replay resends, oldest first, every TLP in the replay buffer that had
been sent when it started, with its original sequence number and LCRC
[cite: PCIe Base Spec, rev 3.0, §3.5.2.1]. TLPs that an Ack removes before
their turn are skipped. New TLPs follow after the replay. A replay
requested while one is running (a Nak or a timeout) is held and starts
when the running one ends, as one replay however many were requested. A
replay with nothing sent and unacknowledged does nothing and does not
count.

## Received Ack and Nak

In DL_Active, for an Ack or Nak with sequence number S
[cite: PCIe Base Spec, rev 3.0, §3.5.2.2]:

1. **Range.** S must be ACKD_SEQ or a TLP sent and not yet acknowledged:
   `(NEXT_TRANSMIT_SEQ − 1 − S)` must not exceed
   `(NEXT_TRANSMIT_SEQ − 1 − ACKD_SEQ)`. Otherwise pulse DL protocol error,
   discard the DLLP, change nothing.
2. **Forward progress.** If S differs from ACKD_SEQ: remove TLPs
   ACKD_SEQ + 1 to S from the replay buffer, set ACKD_SEQ to S and
   REPLAY_NUM to 0, and restart or stop the [replay timer](#timers).
3. **Nak.** A Nak then requests a [replay](#replay) of what remains. An
   Ack with S equal to ACKD_SEQ does nothing.

## Receiver state

[cite: PCIe Base Spec, rev 3.0, §3.5.3.1]

| Name | Width | Reset (DL_Inactive) | Meaning |
| --- | --- | --- | --- |
| NEXT_RCV_SEQ | 12 | 0 | The sequence number expected next. |
| NAK_SCHEDULED | 1 | 0 | A Nak has been scheduled and no good TLP has arrived since. |
| Ack timer | 6 | stopped | Cycles since the oldest TLP not yet covered by an Ack. |

## Receiving TLPs

At the `eop` of a packet tagged TLP, in FC_INIT2 or DL_Active (in other
states it is discarded silently) [cite: PCIe Base Spec, rev 3.0, §3.5.3.1]:

1. **Flagged by `phy`** (`pkt_rx_err_i`): discard; Bad TLP; schedule a Nak.
2. **Too short** to hold a sequence number and an LCRC (below 6 bytes):
   discard; Bad TLP; schedule a Nak.
3. **Ended by EDB** (`pkt_rx_nullify_i`): if the LCRC is the inverse of the
   computed one, discard silently (a nullified TLP). Otherwise discard;
   Bad TLP; schedule a Nak.
4. **LCRC mismatch:** discard; Bad TLP; schedule a Nak. A packet whose
   length is wrong but whose LCRC is good is not a transmission error: no
   compliant transmitter sends it, and a Nak would only replay it until
   REPLAY_NUM rolls over. It goes on to the sequence check and, if
   expected, is malformed (below).
5. The LCRC is good. In FC_INIT2 it ends initialisation
   ([control states](HAS.md#data-link-control-states)). With S its
   sequence number and D = `(S − NEXT_RCV_SEQ)` modulo 4096:
   - **D = 0, expected, malformed:** the packet is malformed when any of these holds: its length is not 2 more
     than a multiple of 4; the TLP is shorter than a 3-DW header (packet
     below 18 bytes); its byte count differs from the size the header's
     Fmt and Length give; or its payload is above 128 bytes, the maximum
     payload (Q6). Accept it at
     this layer (add 1 to NEXT_RCV_SEQ, clear NAK_SCHEDULED, start the
     Ack timer if stopped) but do not store or deliver it; return its
     header's credits at once (CREDITS_RECEIVED and CREDITS_ALLOCATED
     both advance); Malformed TLP.
   - **D = 0, expected, does not fit:** if its class's free credits do not
     cover it, discard; Receiver overflow; no Ack or Nak
     ([receive buffer](FLOW_CONTROL.md#receive-buffer)). NEXT_RCV_SEQ
     does not move, so TLPs that follow arrive ahead (next item): Bad TLP
     and one Nak, and the partner replays from the overflowed TLP.
   - **D = 0, expected:** otherwise store it, add 1 to NEXT_RCV_SEQ,
     clear NAK_SCHEDULED, start the Ack timer if stopped.
   - **D from 1 to 2047, ahead** (TLPs were lost): discard; Bad TLP;
     schedule a Nak.
   - **D from 2048 to 4095, a duplicate** (already received): discard; no
     error; schedule an Ack at once.

To **schedule a Nak**: if NAK_SCHEDULED is 0, set it and make a Nak due;
otherwise nothing. A Nak is sent once per setting of NAK_SCHEDULED.

## Ack and Nak scheduling

- A due Nak is sent at the first [opportunity](#transmit-priority) with
  NEXT_RCV_SEQ − 1.
- An Ack with NEXT_RCV_SEQ − 1 is due when the Ack timer reaches 32
  cycles, or at once after a duplicate.
- Sending either an Ack or a Nak clears both "due" flags and stops the Ack
  timer: it covers every TLP received so far.
- Neither is sent outside DL_Active.

## Transmit priority

At the end of each packet on the `phy` stream (and whenever the stream is
idle), the `dll` starts the first of these that is ready; a packet once
started is always finished:

1. A due Nak.
2. A due Ack.
3. In DL_Init, the next InitFC DLLP of the group being sent
   ([initialisation](FLOW_CONTROL.md#initialisation)).
4. A due UpdateFC, classes in round-robin order
   ([UpdateFC](FLOW_CONTROL.md#updatefc-scheduling)).
5. The next TLP of a running [replay](#replay).
6. The next new TLP, once complete in the replay buffer.

An Ack or Nak therefore waits behind at most one packet, which with the
32-cycle Ack timer keeps it within the 118-cycle limit
([Targets](HAS.md#targets)).
