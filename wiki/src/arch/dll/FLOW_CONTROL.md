# pcie_dll flow control

Credit-based flow control for VC0: initialisation, accounting on both
sides, the receive buffer and UpdateFC. Part of the
[`dll` HAS](HAS.md). The spec places credit accounting in the transaction
layer; in this design `pcie_dll` owns it (decision record
[0003](../../decisions/0003-m2-grill.md#decisions), Q1, Q5), so the upper
layer only sees the [credits available](HAS.md#credits-available) and,
with `FC_RELEASE` 1, says when it is done with a received TLP
([credit release](#credit-release)).

## Classes and credits

Every TLP belongs to one class, taken from header byte 0 (Fmt in bits 7:5,
Type in bits 4:0) and costs one header credit plus its data credits
[cite: PCIe Base Spec, rev 3.0, §2.2.1]
[cite: PCIe Base Spec, rev 3.0, §2.6.1]:

| Class | TLPs (Type) | Data credits |
| --- | --- | --- |
| Posted (P) | Memory write (Type `00000` with data); messages (Type `10xxx`, with or without data). | Payload length in DWs (header bytes 2 bits 1:0 and 3; 0 means 1024), divided by 4 and rounded up, when Fmt bit 6 (with data) is 1; else 0. |
| Completion (Cpl) | Completions (Type `01010` and `01011`, with or without data). | Likewise. |
| Non-posted (NP) | Every other Type: memory, I/O and configuration reads and writes other than memory write, locked reads, atomic operations. | Likewise. |

A data credit is 16 bytes. Header and data credit counters are 8 and 12
bits, all arithmetic modulo 256 and 4096. Fmt bit 5 (4-DW header) and the
data length give the TLP's size in bytes for the replay buffer.

## Initialisation

[cite: PCIe Base Spec, rev 3.0, §3.3]
[cite: PCIe Base Spec, rev 3.0, §3.3.1]

- **FC_INIT1.** On entry, and then every 128 cycles from the start of the
  previous group, the `dll` sends a group of three DLLPs back to back:
  InitFC1-P, InitFC1-NP, InitFC1-Cpl, each with its class's `FC_*H` and
  `FC_*D` parameters ([fields](DLLP.md#fields)). The first InitFC1 or
  InitFC2 received for a class records its HdrFC and DataFC as that
  class's credit limits (0: that field is infinite) and sets the class's
  flag; later ones for that class are ignored. When all three flags are
  set and no group is being sent, the `dll` enters FC_INIT2.
- **FC_INIT2.** Groups of InitFC2-P, -NP, -Cpl with the same values, at
  once on entry and every 128 cycles. The first InitFC2 or UpdateFC (any
  class) or good TLP received sets the done flag; when it is set and no
  group is being sent, the `dll` enters DL_Active. An UpdateFC received in
  FC_INIT2 also sets its class's limits ([transmit](#transmit-accounting)).
- **DL_Active.** On entry an UpdateFC is due at once for every class,
  infinite fields sent as 0: a partner still in FC_INIT2 needs one to
  finish, since this side has stopped sending InitFC2 and sends TLPs only
  if its upper layer has any. This holds with every class infinite.
- Lost or corrupted InitFC DLLPs are covered by the resends.

## Transmit accounting

Per class, for headers and for data separately
[cite: PCIe Base Spec, rev 3.0, §2.6.1.1]:

| Counter | Reset | Rule |
| --- | --- | --- |
| CREDIT_LIMIT | 0 | Set from InitFC in FC_INIT1, then from each UpdateFC in FC_INIT2 and DL_Active, unless the field is infinite. |
| CREDITS_CONSUMED | 0 | Adds a TLP's credits when its `sop` is accepted from the upper stream. A replay consumes nothing. |

- **Gate.** A TLP needing N credits of a field may be accepted only if
  `(CREDIT_LIMIT − (CREDITS_CONSUMED + N))` modulo 2^w is at most 2^(w−1)
  (w = 8 or 12), for both its fields; an infinite field always passes.
- **Available.** `fc_*_avail_o` shows `(CREDIT_LIMIT − CREDITS_CONSUMED)`
  modulo 2^w in DL_Active, all ones for an infinite field (a finite value
  never reaches all ones, since a limit runs at most 2^(w−1) ahead), and 0
  otherwise. It changes in the cycle after an accepted `sop` or a received
  UpdateFC.

## Receive accounting

Per class and field, for finite fields only
[cite: PCIe Base Spec, rev 3.0, §2.6.1.2]:

| Counter | Reset | Rule |
| --- | --- | --- |
| CREDITS_ALLOCATED | the `FC_*` parameter | Adds a TLP's credits when the upper layer takes its `eop` beat, or at once for a malformed TLP; with `FC_RELEASE` 1, a posted or non-posted TLP's when it is [released](#credit-release) instead. |
| CREDITS_RECEIVED | 0 | Adds a TLP's credits when it is stored (good, expected, and covered), or accepted as malformed. Duplicates, bad and overflowed TLPs add nothing. |

A malformed TLP's credits are those its header states, as the
transmitter counted them: 1 header credit plus the data credits its
Length implies, in the class of its Fmt and Type, whatever bytes actually
arrived. A malformed TLP of fewer than 4 bytes has no header and counts no
credits ([receiving TLPs](RETRY.md#receiving-tlps)).

## Credit release

With `FC_RELEASE` 1 ([port](HAS.md#credit-release), decision record
[0004](../../decisions/0004-m3-grill.md#updates), Q9):

| Event | Finite field of P or NP | Infinite field of P or NP | Completion |
| --- | --- | --- | --- |
| The upper layer takes the TLP's `eop` | nothing | the reserve frees its credits | CREDITS_ALLOCATED adds its credits |
| `fc_rel_p_i` or `fc_rel_np_i` | CREDITS_ALLOCATED adds 1 header and the given data credits | nothing | (no release) |

- A malformed TLP still returns its credits at once: it is never delivered.
- The receive buffer frees a TLP's space at delivery, before its credits
  return, so the credits the partner may use never exceed the free space:
  the overflow check and the sizing below hold unchanged.
- Until released, delivered TLPs count against the advertisement: the
  upper layer holds at most the advertised credits of each finite field,
  and a credit-correct partner never makes it hold more.
- A release in DL_Inactive is ignored; every counter restarts there.

## Receive buffer

Holds stored TLPs (without sequence number and LCRC) in arrival order
until the upper layer takes them. Its capacity, in credits, is per class
and field: the advertised value for a finite field, and a fixed reserve
of 8 headers and 24 data credits (384 bytes) for an infinite one. The
MAS may share one memory between classes as long as each class gets this
space.

- **Overflow check.** An expected, good TLP is covered when, for each
  finite field, its credits are at most
  `(CREDITS_ALLOCATED − CREDITS_RECEIVED)` modulo 2^w, and for each
  infinite field they fit in the reserve's free space
  [cite: PCIe Base Spec, rev 3.0, §2.6.1.2]. A TLP that fails it is
  discarded with a Receiver overflow pulse and neither acknowledged nor
  Nak'd. The TLPs behind it then arrive ahead of NEXT_RCV_SEQ, pulse Bad
  TLP and schedule one Nak, and the partner replays from the overflowed
  TLP; if the cause persists, the replays overflow again, REPLAY_NUM rolls
  over and the link retrains ([receiving TLPs](RETRY.md#receiving-tlps)).
- **Never with a compliant pair.** A partner that respects the
  advertised credits never fails the check for a finite field. For an
  infinite field the upper layer must never stall
  ([obligation](HAS.md#receive)): it then takes a 4-byte beat per cycle,
  faster than the 2-byte line fills, so the reserve covers the TLPs queued
  behind one maximum TLP being drained.

## UpdateFC scheduling

In DL_Active, for every class
[cite: PCIe Base Spec, rev 3.0, §2.6.1.2]:

- **Due**, for every class, at once on entering DL_Active and when 2048
  cycles have passed since the class's last UpdateFC (the periodic
  refresh, which recovers a lost one); and, for a class with a finite
  field, when CREDITS_ALLOCATED differs from the values last sent (credits
  were returned, at delivery or on a [release](#credit-release)). An infinite class's credits never change, so it gets
  only the entry UpdateFC and the refresh.
- **Content:** HdrFC and DataFC are CREDITS_ALLOCATED modulo 256 and 4096;
  an infinite field is sent as 0.
- **Order:** due classes take turns P, NP, Cpl, round robin, at
  [priority](RETRY.md#transmit-priority) 4. One UpdateFC carries every
  credit returned so far for its class, so returns coalesce while it waits.
