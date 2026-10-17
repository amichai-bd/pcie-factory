# pcie_dll partner: passive mode

In the `link` e2e the [independent partner](PARTNER.md) is a checker, not
a far end ([`link` HAS, passive checker](../../arch/link/HAS.md#passive-checker),
[#215](https://github.com/amichai-bd/pcie-factory/issues/215)):
[`pcie_dll_partner_passive.py`](../../../../src/dv/dll/models/pcie_dll_partner_passive.py).
`PassiveChecker` takes, per direction (`"ab"` sent by end a, `"ba"` the
reverse), the two packet views the `link` testbench's line injector
records, and `check()` judges both transmitters against the partner's
sources. It keeps the partner's [independence](PARTNER.md#independence):
the standard library and the partner's own modules only, fed plain data.

## Views

A `ViewPacket` is one packet: start and end time, the TLP/DLLP tag, the
bytes between its framing symbols (a TLP's sequence number, TLP and LCRC;
a DLLP's 6 bytes) and `err` when the receiver sees it as bad on the line
(an invalid symbol, or cut short). Times are in any one unit, `cycle`
being one `clk_i` cycle in it. The recorder places each view at a `link`
port, so the bounds apply to the line alone:

| View | Recorded | Time |
| --- | --- | --- |
| sent | before injection: what the transmitter put on the line | at the transmitter's serial output (the injector sits there) |
| delivered | after injection, before the `phy` channel model | at the receiver's serial input: the injector's output plus the channel delay after it |

## Checks

| Check | Direction and views | Flags |
| --- | --- | --- |
| `credits` | The sender's TLPs, sent view of its direction; the limits from InitFC (first per class) and UpdateFC in the delivered reverse view, ended before the TLP started. Only first transmissions consume, by the [accounting](../../arch/dll/FLOW_CONTROL.md#transmit-accounting) modular rule. | A TLP whose credits exceed the limit, or sent before the class was advertised. |
| `sequence` | The sent view of one direction; the Acks and Naks in the delivered reverse view. | A TLP neither new (one past the last new one) nor sent and unacknowledged; a replay not starting at the oldest unacknowledged TLP; a replay not resending the rest in order (skipping only TLPs an Ack removed, [replay](../../arch/dll/RETRY.md#replay)); a new TLP or a second replay before a replay completed; a TLP cut short while the sender was in L0. |
| `acknak` | Acks and Naks in the sent view of one direction; the TLPs of the delivered view of the other, followed through NEXT_RCV_SEQ. | A sequence number ahead of the last TLP that arrived good and in order. |
| `ack_timing` | The end of each TLP that arrived good and in order, and of each good duplicate (Acked at once, [receiving TLPs](../../arch/dll/RETRY.md#receiving-tlps)), in the delivered view; the start of the first Ack or Nak covering it in the reverse sent view. | A gap above `ack_bound` (140 cycles, the `link` HAS line bound), or none by the end of the record. |

## Allowances

Each keeps a check looser than the sender's real knowledge, never
stricter, and each is tied to a trigger the testbench reports:

- **Ack arrival.** An Ack or Nak counts from its end on the line, before
  the sender could act on it. A replay may start anywhere from the oldest
  TLP not acknowledged by Acks that ended `ack_window` (40) cycles before
  it (the sender's `phy` receive and decode time) to the oldest not
  acknowledged by every Ack before it.
- **Rollovers and retrains.** `rollover(end, t)` records a REPLAY_NUM
  rollover pulse; `retrain(end, start, stop)` a range in which the end was
  out of L0 (the testbench closes it at the return to L0 or at the
  link-down).
  - *Rollover withdrawal:* a replay may stop short by its newest TLP only
    when the sender's rollover pulse falls while its last TLP goes out,
    from `cut_slack` cycles before that TLP's start to `cut_slack` cycles
    after its end: the rollover withdraws the TLP offered behind it, not
    started, and the replay after the retrain resends it
    ([REPLAY_NUM](../../arch/dll/RETRY.md#replay_num)). A later rollover
    cannot have withdrawn it, as the newest TLP was due on the line by
    then, and at a plain Recovery the offered TLP just waits.
    `stats["withdrawn_<d>"]`. Without such a pulse a short replay is
    flagged however long the gap. The sender's link-down needs no window:
    it ends the sender's epoch, so a short replay before it is the last
    that epoch judges.
  - *Cut TLPs:* a TLP the `phy` cut short in the sent view counts as sent
    (its sequence number, and its credits when its first DW is there:
    `cut_<d>`, `credit_unread_<d>`) only when it ends within `cut_slack`
    (20) cycles of the sender's out-of-L0 range or link-down; otherwise it
    is flagged.
- **Link-down.** `link_down(end, t)` starts a new epoch for that end: its
  sequence numbers, credits and NEXT_RCV_SEQ restart. The transmit checks
  follow the sender's epochs (sent by start, delivered DLLPs by end), the
  Ack checks the receiver's; a TLP that arrived within `ack_bound` of the
  receiver's link-down needs no Ack.
- **Exemptions.** `exempt(start, stop, why)`: no Ack timing bound for TLPs
  arriving in the range (the testbench marks each end's out-of-L0 range,
  ±140 cycles). With `line=True` the range is a line impairment after the
  views (the `phy` channel's flips, slips and idle, from 140 cycles before
  to 3000 after, [`link` HAS](../../arch/link/HAS.md#passive-checker)): an
  Ack delivered in it counts as maybe seen, which widens the accepted
  replay start.
- `inject(time, direction, cls, what)` keeps the injection log;
  `injections()` filters it.

`stats` counts what each check judged (`new_<d>`, `credit_checked_<d>`,
`replays_<d>`, `acknak_<d>`, `ack_timing_<d>` and per receiver epoch
`ack_timing_<d>_epoch<k>`, `dup_ack_timing_<d>`, the exemptions and
allowances) so a test can show it is not vacuous;
`ack_gap_max` is the largest Ack gap judged, in cycles.

## Unit tests

[`test_pcie_dll_partner_passive.py`](../../../../src/dv/dll/models/test_pcie_dll_partner_passive.py),
run with `python3 -m pytest src/dv/dll/models` and in `ci-gate`:

| Area | Tests |
| --- | --- |
| Partners through tap lines | Clean traffic: no violation, every TLP judged. Drops and corruptions both ways at three credit settings, a rollover, a link-down mid-TLP with the epochs recorded: none; without the epochs: flagged. A sender ignoring credits: caught. |
| Each check, hand-built views | A replay not from the oldest; a skipped TLP; a new TLP inside a replay; a wrong new number; a credit overrun (UpdateFC and replays respected); an Ack ahead; a slow, missing or late duplicate Ack. |
| Each allowance, hand-built views | The Ack window; the rollover withdrawal with the pulse inside its window at both edges (accepted), just before it, just after it or long after the last TLP (flagged), at a plain Recovery or with nothing after a timeout gap, or short by two (flagged); a short replay followed by the sender's link-down, through `check()` (accepted with the link-down recorded, flagged without); a cut TLP at a retrain (accepted) and in L0 (flagged); line-unsure Acks. |
