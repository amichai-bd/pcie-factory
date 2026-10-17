# pcie_dll independent link partner

The far-end data link layer that the `dll` and `link` testbenches put
opposite the design: a pure-Python model,
[`pcie_dll_partner.py`](../../../../src/dv/dll/models/pcie_dll_partner.py),
with its CRCs in
[`pcie_dll_partner_crc.py`](../../../../src/dv/dll/models/pcie_dll_partner_crc.py).
It is the independent oracle of decision record
[0003](../../decisions/0003-m2-grill.md#decisions), Q3: the second opinion
on protocol behaviour beside the
[architecture model](../../arch/dll/HAS.md#architecture-model). Part of the
`dll` DV ([DV plan](DV_PLAN.md)).

## Independence

| Rule | How it holds |
| --- | --- |
| A different author from the architecture model | Written by a separate crewmate under [#210](https://github.com/amichai-bd/pcie-factory/issues/210); the PR records it. |
| Written from the spec and the HAS | Sources: the [`dll` HAS](../../arch/dll/HAS.md) and its [DLLP](../../arch/dll/DLLP.md), [retry](../../arch/dll/RETRY.md) and [flow-control](../../arch/dll/FLOW_CONTROL.md) pages, at the [#259](https://github.com/amichai-bd/pcie-factory/pull/259) head `724c6e9`, merged to `main` as `69b4ac5`, and the author's own reading of the rules they cite. |
| No shared code | Nothing under `src/arch/dll/`, the architecture model, its CRC code or its self-tests, was read or imported. The partner has its own CRCs, framing and state machines. |
| Checked | `test_independence_no_shared_code` scans the partner modules and their tests. It fails on an import other than the standard library, pytest and the partner's own modules. It also fails on a dynamic import (`importlib`, `runpy`, `__import__`, `exec`, `eval`), and on code in a partner module that names an `arch/` path or the architecture model's module names. |
| Disagreements stay visible | Where the partner's reading may differ from the HAS, it is listed below as a [possible disagreement](#possible-disagreements) for the HAS Open items. It is never settled by looking at the model. |

The CRCs are checked only against values found without the spec or the
model: published generic catalogue check values, the standard library's
CRC-32, a long-division implementation and a literal implementation of
the HAS sentences ([CRCs](#crcs)). The HAS example values are compared
separately, as an agreement check.

## Interface

One call per `clk_i` edge, on the `phy` upper packet stream
([HAS, lower packet stream](../../arch/dll/HAS.md#lower-packet-stream-and-link-status)):
`beat_out = partner.clock(beat_in)`. A `Beat` is one 16-bit beat: `data`
(bits 7:0 the earlier byte), `sop`, `eop`, `dllp`, and, with `eop`, `err`
and `nullify`. None is an idle cycle.

| Member | Kind | Meaning |
| --- | --- | --- |
| `link_up` | input | The physical layer's LinkUp. 0 resets the partner to DL_Inactive: every counter, timer, flag and credit is cleared, and the replay buffer and the receive store are emptied. TLPs lost from the replay buffer are counted in `stats["lost_on_reset"]`. TLPs lost from the receive store (not yet taken by `upper_ready`) are not counted. The upper transmit queue (`send`) is kept and resent after the next initialisation. |
| `tx_ready` | input | The stream takes the partner's beat this cycle (0 holds it, even inside a packet). |
| `in_l0` | input | The LTSSM is in L0. 0 holds the replay timer; a replay held after a rollover waits for it to fall and rise again. |
| `upper_ready` | input | The partner's upper layer takes stored TLPs, one per cycle. 0 stops credits being returned. The partner's store is unbounded, so it may stall even with an infinite class; the HAS's never-stall obligation binds the `dll`'s upper layer, not the partner. |
| `send(tlp)` | call | Queue a TLP (header and data, no sequence number or LCRC). `check=False` lets a malformed TLP through. |
| `received` | output | TLPs delivered upward, in order. |
| `retrain_req` | output | 1 for the cycle of a REPLAY_NUM rollover. |
| `avail(cls)` | output | Credits available to send, per field; None is infinite. |
| `violations` | output | Every far-end rule break that injection cannot explain, with the cycle. A test asserts it stays empty. |
| `stats` | output | Event counts: packets sent and replayed, Acks, Naks, bad TLPs and DLLPs, duplicates, timeouts, rollovers and the injected errors. |
| `ack_wait_max` | output | The longest time from one of the partner's TLPs leaving it to the Ack or Nak that removed it, in cycles. |

`Pair` joins two partners through a `Line` of fixed delay each way (22
cycles by default, the two `phy` stacks of the
[HAS targets](../../arch/dll/HAS.md#targets)). Helpers build test TLPs
(`make_tlp`), DLLPs (`make_dllp`) and framed TLPs (`frame_tlp`).

## Behaviour

It does what a `dll` does, with these simplifications for a model: TLPs
come straight from the upper queue (no store and forward, so a TLP gets
its sequence number when it starts on the line), the receive store is
unbounded, and the upper layer takes one TLP per cycle.

- **Control states.** DL_Inactive, FC_INIT1, FC_INIT2, DL_Active, entered
  as the [control states](../../arch/dll/HAS.md#data-link-control-states)
  say. TLPs are sent only in DL_Active and received from FC_INIT2.
- **DLLPs.** Built and checked from the
  [field tables](../../arch/dll/DLLP.md#fields) with the CRC-16. A bad
  CRC-16, a flagged DLLP or a wrong length is counted and dropped.
- **TLPs.** Framed with a 12-bit sequence number and the LCRC
  ([TLP framing](../../arch/dll/RETRY.md#tlp-framing)). Receive applies
  the [receive rules](../../arch/dll/RETRY.md#receiving-tlps) in order:
  flagged, too short (below 6 bytes), nullified, LCRC, then sequence. A
  good LCRC over a bad length is not a transmission error: an expected
  TLP with a length that is not whole DWs, below a 3-DW header, other than
  its header's, or with a payload above 128 bytes is malformed
  (acknowledged, not delivered, the credits its header claims returned at
  once). Other expected TLPs overflow or are stored. TLPs ahead are bad, with one Nak per
  NAK_SCHEDULED. Duplicates get an Ack at once.
- **Ack and Nak.** The Ack timer (32 cycles from the TLP's `eop`) and the
  [transmit priority](../../arch/dll/RETRY.md#transmit-priority): Nak, Ack,
  InitFC, UpdateFC, replay, new TLP.
- **Replay.** Replay buffer, the range check on received Acks and Naks,
  forward progress, the replay timer (356 cycles, held outside L0),
  REPLAY_NUM, and the rollover hold, which keeps TLPs (not DLLPs) back
  until the link has left L0 and returned, or for 1024 cycles if it never
  leaves ([REPLAY_NUM](../../arch/dll/RETRY.md#replay_num)). A replay
  requested during a replay runs once after it.
- **Flow control.** InitFC1 and InitFC2 groups every 128 cycles, recording
  the far end's credits ([initialisation](../../arch/dll/FLOW_CONTROL.md#initialisation)).
  The transmit gate and the modular counters
  ([accounting](../../arch/dll/FLOW_CONTROL.md#transmit-accounting)). The
  receive-side overflow check. UpdateFC for every class, at once on
  entering DL_Active, when credits are returned and on the 2048-cycle
  refresh, with infinite fields sent as 0
  ([UpdateFC](../../arch/dll/FLOW_CONTROL.md#updatefc-scheduling)). The
  default advertisement is the HAS parameter defaults: P 8 and 32, NP 8
  and 8, completions infinite.

### Far-end checks

Beyond what a receiver needs, the partner judges the far end's transmit
side and records a violation for each of these. The HAS says a `dll`
never sends any of them:

- an Ack or Nak outside the range of unacknowledged TLPs (DL protocol error);
- a TLP that the advertised credits do not cover (receiver overflow);
- a malformed TLP;
- a TLP prefix (Fmt `100`): this design has no prefixes, so a `dll` never
  sends one. It is accounted by the HAS Type rule;
- nonzero reserved bits in a DLLP or in a TLP's sequence-number bytes;
- a DLLP type or virtual channel this design never sends;
- InitFC values that change between resends;
- an UpdateFC that moves a credit limit back, or that is nonzero for an
  infinite field;
- a beat outside a packet, a `sop` inside one, or a `dllp` tag that
  changes inside a packet.

## Error injection

`Inject` holds per-packet rates, drawn from the partner's own seeded
generator, and `*_next` counts that apply to the next packets first. The
other hooks are attributes or calls on the partner.

| Hook | Effect |
| --- | --- |
| `tx_tlp_drop`, `tx_tlp_drop_next` | One of the partner's TLPs (new or replayed) takes its line time but is driven as idle. |
| `tx_tlp_corrupt`, `tx_tlp_corrupt_next` | One random bit of the framed TLP is inverted. |
| `tx_tlp_nullify`, `tx_tlp_nullify_next` | A nullified copy (inverted LCRC, `nullify` on `eop`) goes first, then the TLP itself. |
| `tx_tlp_flag`, `tx_dllp_flag` (and `_next`) | A TLP or DLLP of the partner is sent intact with `err` on its `eop` beat, as if the `phy` had flagged it. |
| `tx_dllp_drop`, `tx_dllp_corrupt` (and `_next`) | The same for the partner's DLLPs. |
| `rx_tlp_drop`, `rx_dllp_drop` (and `_next`) | A received packet is lost as if on the line. |
| `rx_tlp_nak` (and `_next`) | A good, expected TLP is treated as bad: discarded with a Nak. |
| `force_nak()` | A Nak is due now, whatever NAK_SCHEDULED says. |
| `ack_delay` | Extra cycles before a due Ack. A value above the replay timeout makes the far end time out. |
| `withhold_credits` | No UpdateFC is sent. Returned credits pile up until it is cleared. |
| `upper_ready = 0` | The partner's upper layer stalls, so credits stop being returned. |

## CRCs

`crc` is a generic bit-serial CRC in the usual parameter model (width,
polynomial, seed, input and output reflection, final XOR). The link CRCs
use it with the [CRC-16](../../arch/dll/DLLP.md#crc-16) and
[LCRC](../../arch/dll/RETRY.md#lcrc) polynomials, a seed of all ones,
reflected input and output, and a complemented result, sent low byte
first. The partner's reading of the line mapping is as follows. Bytes go
on the line in order, each bit 0 first. The first bit on the line is the
highest-order message coefficient. The complemented remainder is sent
highest-order coefficient first, so CRC byte 0 bit 0 carries
x^(w−1).

| Check | Against |
| --- | --- |
| Engine | Catalogue check values of "123456789" for seven CRC-16 and CRC-32 variants with different reflection, seed and final-XOR settings. |
| LCRC | The standard library's CRC-32, on random data. |
| Both CRCs | Polynomial long division of the line-order message, on random data. |
| Both CRCs | Constant residue: data followed by its own CRC always leaves the same remainder. |
| CRC-16 | All single-bit errors in a DLLP are detected. |
| CRC-16 known answers | A literal statement of the DLLP page's sentences. UpdateFC-NP with HdrFC `A5`h and DataFC `9C3`h (body `90 29 49 C3`) gives bytes `B3 94`. Nak 5 (body `10 00 00 05`) gives `7D 70`. |
| HAS examples (agreement) | The CRC-16 table on the DLLP page and the LCRC example on the retry page, all reproduced. |

## Unit tests

[`test_pcie_dll_partner_crc.py`](../../../../src/dv/dll/models/test_pcie_dll_partner_crc.py)
and [`test_pcie_dll_partner.py`](../../../../src/dv/dll/models/test_pcie_dll_partner.py)
run with `python3 -m pytest src/dv/dll/models`, and in `ci-gate` through
`./build ci unit`. They need no simulator.

| Area | Tests |
| --- | --- |
| Formats | DLLP fields placed bit by bit from the field tables; TLP classes and credits. |
| Wrap | Sequence numbers preloaded near 4095 crossing to 0 with expected TLPs, Ack, a duplicate, a Nak and a replay, and a range error behind ACKD_SEQ (`test_sequence_wrap`). A loopback of 520 maximum-payload MWr wraps the 8-bit header and 12-bit data counters on both sides (`test_credit_counters_wrap_loopback`). |
| One partner, hand-driven | Initialisation and InitFC resend; entry UpdateFC, infinite classes included; receive rules: expected, ahead, NAK_SCHEDULED, duplicate, nullified, flagged, too short, LCRC, malformed lengths and payloads; Ack timing (32 cycles); DLLP checks; UpdateFC checks; Ack, Nak and replay, a TLP going out at a Nak included; replay timeout at 356 cycles; rollover hold through a retrain and its 1024-cycle fallback; replay timer held outside L0; overflow; the credit gate; a received TLP prefix; the flag and nullify hooks; link-down reset. |
| Two partners back to back | Initialisation at three skews. Clean bidirectional traffic. Lossless, exactly once and in order, with every injection hook on, three seeds, finite and default credits, upper-layer stalls with finite credits. Delayed Acks and forced Naks. Withheld credits. Rollover and recovery. A sender that ignores credits is caught. Same seed, same run. |
| Independence | No imports or paths outside the partner and the standard library. |

## Cross-check with the architecture model

The `dll` testbench runs the partner, the architecture model and the RTL
against each other for the features the RTL has (DLLPs, InitFC, the
control states), as the [DV plan](DV_PLAN.md#cross-check-rtl-architecture-model-partner)
describes ([#211](https://github.com/amichai-bd/pcie-factory/issues/211)).
The TLP receive path and Ack/Nak scheduling are cross-checked in the
`rx_*` entries ([#213](https://github.com/amichai-bd/pcie-factory/issues/213)).
Since [#212](https://github.com/amichai-bd/pcie-factory/issues/212) the
partner is also the far end of the TLP transmit tests, with its fault
hooks, and receives the architecture model's TLPs in the rollover
scenario. A reading difference found there, when a running replay ends,
is a [HAS open item](../../arch/dll/HAS.md#open-items). Credit accounting
is cross-checked in the flow-control entries
([#214](https://github.com/amichai-bd/pcie-factory/issues/214)): the
partner judges every TLP the DUT's credit gate lets through, and sends as
the DUT's UpdateFCs allow. Any disagreement goes to the HAS Open items, not
into either model silently.

## Possible disagreements

Points where the partner's reading may differ from the HAS at `724c6e9`,
for the [HAS Open items](../../arch/dll/HAS.md#open-items). None is settled
by the architecture model. Rows 1 to 3 have firstmate rulings in
[#261](https://github.com/amichai-bd/pcie-factory/issues/261). The Base Spec
section numbers cited below are unverified: no local copy of the spec was
available (`./build specs list`).

| # | HAS rule | Partner's reading | Partner does |
| --- | --- | --- | --- |
| 1 | A malformed TLP's "header's credits" are returned at once ([receiving TLPs](../../arch/dll/RETRY.md#receiving-tlps)). | All the credits its header claims: one header credit and the data credits its Length gives, which is what the sender consumed. If only the header credit came back, data credits would leak. A malformed TLP shorter than one DW has no Fmt, Type or Length to read, and the HAS does not say what it returns. | Returns both; nothing for a TLP under 4 bytes. Adopted ([#261](https://github.com/amichai-bd/pcie-factory/issues/261)). |
| 2 | An overflowed TLP is discarded without Ack or Nak, so it is replayed ([receive buffer](../../arch/dll/FLOW_CONTROL.md#receive-buffer)). | The Base Spec puts the overflow check in the transaction layer, after the data link layer has accepted and acknowledged the TLP, so a compliant receiver would acknowledge it and drop it [cite: PCIe Base Spec, rev 3.0, §2.6.1.2]. The HAS's choice never loses a TLP and is visible only with a credit-incorrect partner. | Follows the HAS, and flags any overflow as a far-end violation. The HAS keeps its rule and records this reading ([#261](https://github.com/amichai-bd/pcie-factory/issues/261)). |
| 3 | `FC_NPD` may be 1 ([parameters](../../arch/dll/HAS.md#parameters)). | The non-posted class includes atomic operations. A compare-and-swap with 16-byte operands carries 32 bytes, which is 2 data credits. With 1 advertised, such a TLP could never be sent and the sender stalls for good. The minimum may need to be 2 while the upper stream is opaque [cite: PCIe Base Spec, rev 3.0, §2.6.1] [cite: PCIe Base Spec, rev 3.0, §6.15]. | Rejects a configuration with NP data credits of 1: the minimum is 2, or 0 for infinite ([#261](https://github.com/amichai-bd/pcie-factory/issues/261)). |
| 4 | The CRC-16 and LCRC line mapping (open item). | Derived from the DLLP page text alone, it gives the same bytes as every HAS example, and the known answers `B3 94` and `7D 70` ([CRCs](#crcs)). Both readings come from the same sentences, so this agreement shows the page is self-consistent, not that it matches the Base Spec; the open item stays with the captain. | As the HAS. |
| 5 | DL_Up from FC_INIT2, TLPs only from DL_Active, the "group being sent is complete" exit rule (open item). | Could not be confirmed without the spec. No observable difference: a far end that waits for its group is compliant either way. | The same rules. |
| 6 | Classes come from the Type field ([classes](../../arch/dll/FLOW_CONTROL.md#classes-and-credits)); the HAS has no TLP prefixes. | A received TLP whose byte 0 is a prefix (Fmt `100`) is not a TLP of this design: no `dll` sends one, so the HAS gives it no class of its own. | Flags it as a far-end violation; accounts it by the Type rule. |

A seventh point was found by the `dll` RTL review
([#211](https://github.com/amichai-bd/pcie-factory/issues/211)): the
HAS now finishes a packet whose `sop` the `phy` took before the link went
down ([decisions](../../arch/dll/HAS.md#decisions)), while the partner
drops its packet on link-down. It is a [HAS open item](../../arch/dll/HAS.md#open-items);
the partner is unchanged. An eighth was found by the `dll` receive tests
([#213](https://github.com/amichai-bd/pcie-factory/issues/213)): the
partner leaves a TLP still on the line when a Nak or timeout arrives out
of the replay, where the architecture model resends it. Resolved in
[#212](https://github.com/amichai-bd/pcie-factory/issues/212) as a
[HAS decision](../../arch/dll/HAS.md#decisions): a TLP handed to the line
is sent, so the replay resends it; the partner is corrected to do so
(`test_nak_during_a_tlp_replays_it_too`).

Found independently and already resolved in #259 (by `feb7ccb`):

- With every class infinite, no UpdateFC was ever sent. The side that left
  FC_INIT2 first could then leave its partner in FC_INIT2 for good. The
  fix is the entry UpdateFC and refresh for every class.
- After a rollover, the replay could start before the retrain did. The fix
  is the rollover hold.
