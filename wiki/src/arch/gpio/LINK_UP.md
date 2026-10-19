# pcie_gpio link-up and Hot Reset

The link-state rules of the [`gpio` HAS](HAS.md): how an end aligns, goes
up and goes down, Hot Reset, and why the rules converge. The slots and idle
words are on the HAS ([slots](HAS.md#slots), [idle words](HAS.md#idle-words)).

## Terms

- **Slot counts.** Received runs are counted in received slots, one per
  slot the partner sent. Runs that an end sends are counted in its own
  slots. Neither depends on the two ends' clock ratio, which the
  [clock contract](HAS.md#clocks-and-resets) allows from 1/2 to 2.
- **Run.** Consecutive received slots of one kind. Every slot outside the
  kind breaks the run: other words, ignored and discarded slots, and
  damaged slots.
- **Damaged slot.** A slot whose transfer 0 reads 0, or one that pulses
  `rcv_err_o` ([receive](HAS.md#receive)). A slot counts once, however
  many rules it breaks.
- **`W`.** The one-way latency: from a slot's first transfer at the sender
  (counting `L_tx` and the slot in flight) to its handling at the
  receiver's `clk_i` side. The MAS keeps `W` at most 4 slots of the
  slower end (at most 1.75: [MAS timing](../../rtl/gpio/MAS.md#timing);
  the wait for a slot start is not in `W`: an idle word is made at the
  slot start).
  That is at most 8 slots of the faster end at a ratio of 2.
- **Quiet run.** At least 16 TRAIN words with bit 8 clear, counted in the
  sender's own slots and sent whatever its alignment. An end sends one
  after reset, after any down from up, after alignment loss, at Hot Reset
  entry (upstream) and at the end of the Hot Reset dwell (downstream).

## States

| State | `ltssm_state_o` | `link_up_o` | Sends |
| --- | --- | --- | --- |
| Reset (`rst_i` high) | `0x00` | 0 | Nothing: the pins hold their [reset values](HAS.md#reset-values). |
| Not aligned | `0x00` | 0 | TRAIN, bit 8 clear |
| Training: aligned, not up | `0x04` | 0 | TRAIN: bit 8 clear during a quiet run, set after it |
| Up | `0x20` | 1 | Packets and IDLE |
| Hot Reset | `0x19` | 0 | HOT_RESET (downstream); TRAIN as in training (upstream) |

An end sends bit 8 set only when its receiver is aligned and its quiet run
is over.

## Rules

- **Alignment.** The receiver becomes aligned after 8 consecutive slots,
  at one slot phase, whose control transfers read 1, 0, 0, 0. It loses
  alignment in two cases:
  - a second damaged slot within 8 slots of a first;
  - no slot arriving for 64 × `CLK_DIV` `clk_i` cycles: 16 own slot
    times, at least 8 of the partner's (no pulse: the partner stopped).

  Reset clears alignment.
- **Up.** A training end goes up after a run of 8 good slots, all
  received after its own quiet run ended. Good slots are TRAIN words with
  bit 8 set, IDLE words and undamaged packet slots; packet slots received
  while not up are not delivered.
- **Down.** An up end goes down to training (or to not aligned) on any of
  these causes. The runs of causes 2 and 3 count only slots received while
  up, starting at 0 when the end goes up; words received in any other
  state, such as those a downstream end ignores during its Hot Reset dwell,
  never feed them.
  1. alignment loss;
  2. a run of 4 TRAIN words with bit 8 clear;
  3. a run of 64 TRAIN words of either bit-8 value: a backstop against any
     state where one end is up and the other trains;
  4. a Hot Reset (below).

  Shorter runs of TRAIN words are ignored while up. Every down starts a
  quiet run at once, except a downstream Hot Reset: that end sends
  HOT_RESET words through its dwell and starts its quiet run at the end of
  the dwell.
- **At link-down,** a transmitted packet partly sent ends with an ABORT
  slot, and an open received packet is closed with an error `eop`
  ([HAS link-up](HAS.md#link-up)).

## Hot Reset

Hot Reset is emulated, so the root port can reset the endpoint as it can
over the `phy` [cite: PCIe Base Spec, rev 3.0, §4.2.6.11].

- **Downstream** (`port_role_i` 0):
  - The adapter is in Hot Reset from any state but reset while
    `hot_reset_req_i` is 1, and then until it has sent at least 32
    HOT_RESET words.
  - In Hot Reset it ignores every received word; its receiver still
    tracks alignment.
  - When Hot Reset ends, it trains, starting with a quiet run.
  - In the upstream role, `hot_reset_req_i` is ignored.
- **Upstream** (`port_role_i` 1):
  - A run of 4 HOT_RESET words, in any state while aligned, enters Hot
    Reset and starts a quiet run. Further HOT_RESET words while in Hot
    Reset keep it there and do not restart the quiet run; a new entry
    after leaving Hot Reset starts a new one.
  - A run of 4 TRAIN words, of either bit-8 value, leaves it to training.
- `link_up_o` falls on entry, so the `dll` goes to DL_Inactive, as with a
  `phy` Hot Reset.

## Convergence

Each case below holds for any clock ratio from 1/2 to 2 and any `CLK_DIV`.

- **Every down is symmetric, by construction.** Whatever takes end A down,
  A then sends at least 16 bit-8-clear words. The partner B receives every
  one of them, at any ratio. One damaged slot splits the 16 into runs, one
  of them at least 8 long. So an up B always sees a run of 4 (cause 2) and
  goes down too. Each `dll` goes down once, and both ends then train.
- **No stale up.** A counts its run of 8 only after its own quiet run has
  ended, 16 of A's slot times after it started. B goes down on receiving
  4 of A's quiet words in a run (after at most 8 if one is damaged: a
  damaged slot breaks the run), and the good slots B sent
  before that keep arriving at A until the times below, in A's slot times
  after A's quiet run started, with `W` (which includes the slot in flight)
  at its bound; figures in brackets are with one damaged slot:

  | B's rate | B's last stale good slot reaches A by | Overlap with A's count |
  | --- | --- | --- |
  | Equal | 4 + 2 × 4 = 12 (8 + 2 × 4 = 16) | None (none: A counts only after 16) |
  | Twice A's (`W` = 4 A slots) | 12 (16) | None (none) |
  | Half A's (`W` = 8 A slots) | 4 + 2 × 8 = 20 (8 + 2 × 8 = 24) | 4 (8) A slot times: at most 2 (4) of B's slots |

  Every overlap is shorter than the 8 good slots A needs, so A cannot go
  up on B's stale slots.
- **Reaching up, no livelock.** Say A goes up first, at time t. Its run
  shows that B is aligned and past its quiet run. A has sent good slots
  since its own run started, so B's run of 8 completes within 8 of A's
  slots plus `W`, or 16 of A's slots plus `W` if one damaged slot breaks
  the run (at most 8 slots lost, then 8 more). B sends TRAIN with bit 8
  set until then. Those words keep arriving at A until 2 × `W` + 16 A
  slots after t (`W` includes the slot in flight, so nothing is added).
  At a ratio of 2 with B the faster end (`W` = 8 of B's slots, an A slot
  = 2 of B's), that is at most 16 + 32 = 48 of B's slots. Every trailing
  word has bit 8 set, and 48 is below the run of 64 (cause 3), so
  A stays up. Without a damaged slot, both ends are up within 8 + 16 +
  8 slots, plus 3 × `W`, of the later transmitter starting (counted in the
  slower end's slots). After that, neither end goes down unless a cause
  occurs.
- **A single damaged slot.** It is one damaged slot, and alignment loss
  needs two. A forged idle word is one word, below every run (4, 4, 8,
  64). In a packet, a damaged data bit reaches the `dll` as a bad packet.
  A damaged code bit reaches it as a cut, merged or dropped packet. The
  `dll` recovers either one by a Nak or a replay. So both ends and both
  `dll`s stay up.
- **Forged runs.** Four or more damaged slots in a row can forge a run of
  4 bit-8-clear TRAIN words or 4 HOT_RESET words. The end that sees the
  run goes down or enters Hot Reset, which starts its quiet run, so the
  partner follows (the first case). The result is a spurious but
  symmetric down, never a hang.
- **Restart at the same phase.** A partner that resets stops its
  forwarded clock while `rst_i` is high; the watchdog catches a reset of
  16 slot times or more. It then sends its quiet run, so the up end goes
  down on cause 2.
- **Restart at a new phase.** Its markers fall on other transfers, so the
  first two slots read as damaged: alignment loss, cause 1. Either way
  both ends then train and reach up.
- **Hot Reset.**
  - The downstream end sends at least 32 HOT_RESET words, so the upstream
    end sees its run of 4. Both go down once and both send quiet runs:
    the downstream end after the dwell, the upstream end at entry.
  - The upstream end leaves Hot Reset on the downstream end's quiet TRAIN
    words, and the pair reaches up as above.
  - A downstream end that enters Hot Reset while not aligned keeps bit 8
    clear afterwards until it aligns.

## Verification

The DV cases for these rules are in the [DV plan](../../dv/gpio/DV_PLAN.md)
([#378](https://github.com/amichai-bd/pcie-factory/issues/378)).
