"""pcie_gpio unit tests (wiki/src/dv/gpio/DV_PLAN.md).

Two adapters back to back on the top pcie_gpio_tb, each on its own clock
(RATIO: b's clock 1/2, 1 or 2 times a's), carrying dll-shaped traffic
(tb/gpio_env.py). Every test brings both ends from reset to up and passes
the environment's checks: the transmitter model at every slot (the slots,
idle words, quiet runs, Hot Reset words, ABORT at link-down and the ready
rule), the scoreboards, the receive stream contract, the status codes,
the pin checker and the RTL assertions (no receive-crossing or buffer
overflow).
"""

import cocotb

from tb.gpio_env import COV_LINK, COV_RX, Env, gm


def seed() -> int:
    return cocotb.RANDOM_SEED


async def up(dut) -> Env:
    env = Env(dut, seed())
    env.start()
    await env.link_up()
    return env


def downs(env) -> tuple[int, int]:
    return env.ends[0].downs, env.ends[1].downs


async def idle_slots(env, d: str, n: int) -> None:
    """Wait until ``n`` more slots of ``d`` have gone out."""
    tx = env.ends[0 if d == "ab" else 1].tx
    target = len(tx.slots) + n
    await env.until(lambda: len(tx.slots) >= target, 40 * n * env.clk_div + 1000,
                    f"{n} slots of {d}", poll=4)


async def settle(env, d: str) -> None:
    """Every started slot of ``d`` handled at the receiver: W is at most 4
    slots of the slower end (MAS), so 8 slots of either end cover it."""
    await idle_slots(env, d, 8)


def assert_up_since(env, before, what):
    assert downs(env) == before and all(e.up for e in env.ends), (
        f"{what}: link went down (downs {before} -> {downs(env)})")


def left_up(end, since: int) -> int:
    """The ltssm code ``end`` went to when it first left up after cycle
    ``since``: 0x04 a run (cause 2 or 3), 0x00 alignment loss, 0x19 Hot Reset."""
    prev = None
    for n, code in end.ltssm_log:
        if n > since and prev == gm.LTSSM_UP:
            return code
        prev = code
    return -1


async def both_down_once(env, before, what, limit_slots=200, via=None, since=None):
    """Both ends went down exactly once since ``before`` and are up again.
    ``via`` (a code per end, or None) is the state each left up through."""
    limit = limit_slots * env.slot_cycles()
    since = since or [e.cycle for e in env.ends]
    await env.until(lambda: downs(env)[0] > before[0] and downs(env)[1] > before[1],
                    limit, f"{what}: both ends down")
    await env.until(lambda: all(e.up for e in env.ends), limit, f"{what}: both ends up again")
    await idle_slots(env, "ab", 100)
    got = downs(env)
    assert got == (before[0] + 1, before[1] + 1), (
        f"{what}: downs a {got[0] - before[0]}, b {got[1] - before[1]}; expected exactly one each")
    assert all(e.up for e in env.ends), f"{what}: not both up after the recovery"
    for e, want, n in zip(env.ends, via or (None, None), since):
        if want is not None:
            got = left_up(e, n)
            assert got == want, (f"{what}: end {e.name} left up through ltssm {got:#04x}, "
                                 f"expected {want:#04x}")


# ------------------------------------------------------------------ L0

@cocotb.test()
async def smoke(dut):
    """L0: reset values, link-up, 20 packets each way, lossless and in order."""
    env = await up(dut)
    env.send(0, 20)
    env.send(1, 20)
    await env.drain()
    await env.cycles(200)
    env.finish()
    for e in env.ends:
        assert e.downs == 0 and e.rcv_err == 0, f"end {e.name}: {e.downs} downs, {e.rcv_err} rcv_err"


# ------------------------------------------------------------- traffic

@cocotb.test()
async def traffic(dut):
    """Sustained load both ways (HAS, transmit and receive): back-to-back
    dll-shaped packets, then random gaps; lossless, in order, the link up
    throughout. Rate matching: the dll offers a beat every cycle inside a
    packet and a sop whenever ready, faster than the pins, so ready holds it
    off between packets (the model checks ready is 1 only with the buffer
    empty); at a clock ratio of 2 the receive crossing takes a slot per 4
    forwarded-clock edges with no overflow (RTL assertion). The one-way
    latency, sop taken to sop delivered, stays within the arch model's
    bound for this ratio."""
    env = await up(dut)
    n = 120
    env.send(0, n)
    env.send(1, n)
    await env.drain()
    COV_LINK.sample(event="sustained_load", ratio=env.ratio_name)
    COV_LINK.sample(event="rate_backpressure", ratio=env.ratio_name)
    for e in env.ends:
        e.valid_p = 0.05
    env.send(0, 40)
    env.send(1, 40)
    await env.drain()
    await env.cycles(200)
    stats = env.finish()
    for e in env.ends:
        assert e.downs == 0 and e.rcv_err == 0, f"end {e.name}: {e.downs} downs, {e.rcv_err} rcv_err"
    # Line use under back-to-back load: each packet's slots, plus the
    # slot-phase and buffer-empty gap before the next sop.
    for d, e in (("ab", env.ends[0]), ("ba", env.ends[1])):
        busy = sum(1 for s in e.tx.slots if len(s.sent) == 4 and gm.decode(s.sent)[1] != gm.IDLE)
        env.log.info("%s: %d packet slots of %d slots sent", d, busy, len(e.tx.slots))
    env.log.info("traffic: %s", stats)


# --------------------------------------------------------- error cases

async def window(env, d, si0, what, pulses_want, extra=12):
    """After slot si0 of ``d`` and ``extra`` more have been handled, the
    receiver's pulses and beats since the mark equal the HAS receive table
    (the model) and ``pulses_want``."""
    s, r = (env.ends[0], env.ends[1]) if d == "ab" else (env.ends[1], env.ends[0])
    mark_err, mark_rx = window.mark
    await env.at_slot(d, si0 + extra)
    await settle(env, d)
    si1 = si0 + extra
    pulses, beats = env.model_window(d, window.start, si1)
    got_pulses = r.rcv_err - mark_err
    got_beats = r.rx_log[mark_rx:]
    assert got_pulses == pulses == pulses_want, (
        f"{what} on {d}: rcv_err pulses {got_pulses}, model {pulses}, expected {pulses_want}")
    want = [(b.sop, b.eop, b.dllp, b.err, None if b.err else b.data) for b in beats]
    got = [(b.sop, b.eop, b.dllp, b.err, None if b.err else b.data) for b in got_beats]
    assert got == want, f"{what} on {d}: delivered {got[:6]}..., model {want[:6]}..."
    assert_up_since(env, window.before, what)


def open_window(env, d):
    s, r = (env.ends[0], env.ends[1]) if d == "ab" else (env.ends[1], env.ends[0])
    window.mark = (r.rcv_err, len(r.rx_log))
    window.before = downs(env)
    window.start = len(s.tx.slots)


async def case_closed(env, d, what, code, data, pulses, follow=()):
    """Replace an idle slot (no packet open) with (code, data), then the
    slots in ``follow``."""
    open_window(env, d)
    si = env.next_slot(d, 3)
    env.replace(d, si, code, data)
    for k, (c, w) in enumerate(follow):
        env.replace(d, si + 1 + k, c, w)
    COV_RX.sample(case=what)
    await window(env, d, si, what, pulses)
    await idle_slots(env, d, 12)


async def case_open(env, d, what, pulses, code=None, data=0, damage=False, drop=False):
    """A 40-beat TLP whose 10th slot is replaced by (code, data), or damaged,
    or cut at the sender by valid falling (``drop``)."""
    s = env.ends[0 if d == "ab" else 1]
    open_window(env, d)
    pkt = env.packets(1, beats=40)[0]
    if drop:
        pkt.drop_at, pkt.drop_len = 12, 3
    s.send([pkt])
    await env.until(lambda: env.find_slot(d, window.start, (gm.SOP_TLP,)) >= 0, 20_000,
                    f"{what}: sop sent")
    si = env.find_slot(d, window.start, (gm.SOP_TLP,)) + 10
    if damage:
        env.damage(d, si)
    elif code is not None:
        env.replace(d, si, code, data)
    COV_RX.sample(case=what)
    await window(env, d, window.start, what, pulses, extra=si - window.start + 45)
    await idle_slots(env, d, 12)


@cocotb.test()
async def errors(dut):
    """Every receive error case of the HAS (receive), each way: with no
    packet open, a damaged marker, an unknown idle word, MID (then EOP and
    ABORT dropped silently), EOP, ABORT; ONE_TLP and ONE_DLLP delivered as
    one-beat packets; inside a 40-beat TLP, an IDLE slot, a SOP, a ONE, an
    ABORT (no pulse; the next MID pulses) and a damaged marker, each closing
    the packet with an error eop; valid falling inside a packet at the
    sender (ABORT on the pins, an error eop, no pulse). The receiver's
    pulses and beats match the HAS receive table (models/) slot for slot,
    and no case takes the link down: one damaged slot is not an alignment
    loss."""
    env = await up(dut)
    for sb in env.sb.values():
        sb.allow_cut = True
    for d in ("ab", "ba"):
        open_window(env, d)
        si = env.next_slot(d, 3)
        env.damage(d, si)
        COV_RX.sample(case="marker_zero")
        await window(env, d, si, "marker_zero", 1)
        await idle_slots(env, d, 12)
        await case_closed(env, d, "unknown_word", gm.IDLE, 0x1234, 1)
        await case_closed(env, d, "mid_closed", gm.MID, 0xBEEF, 1,
                          follow=((gm.EOP, 0x1111), (gm.ABORT, 0)))
        COV_RX.sample(case="discarded")
        await case_closed(env, d, "eop_closed", gm.EOP, 0x2222, 1)
        await case_closed(env, d, "abort_closed", gm.ABORT, 0, 1)
        for code, dllp in ((gm.ONE_TLP, False), (gm.ONE_DLLP, True)):
            env.sb[d].injected.append(([0x0C0D], dllp))
            await case_closed(env, d, "one_closed", code, 0x0C0D, 0)
        await case_open(env, d, "idle_open", 1, gm.IDLE, gm.IDLE_WORD)
        await case_open(env, d, "sop_open", 1, gm.SOP_DLLP, 0x3333)
        await case_open(env, d, "one_open", 1, gm.ONE_TLP, 0x4444)
        await case_open(env, d, "abort_open", 1, gm.ABORT, 0)
        await case_open(env, d, "marker_zero", 1, damage=True)
        await case_open(env, d, "valid_drop", 0, drop=True)
    env.send(0, 10)
    env.send(1, 10)
    await env.drain()
    env.finish()


# ------------------------------------------------------ reset, restart

@cocotb.test()
async def restart(dut):
    """Reset and restart (HAS, clocks and resets; LINK_UP.md, convergence)
    under traffic both ways: b reset briefly, a reset for longer than the
    watchdog, then both together. Each time both ends go down exactly once
    (a reset end counts its own down), come back up, and the traffic
    resumes: packets taken whole after the restart are delivered in order;
    packets in flight at the reset may be lost or cut, and the one on the
    pins at a sender reset may arrive damaged (one damaged slot, LINK_UP.md
    convergence; the scoreboard allows one per sender reset)."""
    env = await up(dut)
    for sb in env.sb.values():
        sb.allow_loss = True
    for i, length in ((1, 12), (0, 200 * env.clk_div), (None, 30)):
        env.send(0, 30)
        env.send(1, 30)
        await env.cycles(300)
        before = downs(env)
        if i is None:
            env.reset(0, length)
            env.reset(1, length)
        else:
            env.reset(i, length)
        await both_down_once(env, before, f"reset of {'both' if i is None else 'ab'[i]}")
        await env.drain()
    # After the last restart the scoreboards are strict again.
    for sb in env.sb.values():
        sb.strict()
    env.send(0, 20)
    env.send(1, 20)
    await env.drain()
    env.finish()


# ----------------------------------------------------------- Hot Reset

@cocotb.test()
async def hot_reset(dut):
    """Hot Reset (LINK_UP.md, Hot Reset) with a packet in flight each way:
    a (downstream) holds hot_reset_req_i for one cycle, then for 3000
    cycles. a sends at least 32 HOT_RESET words (the model counts them),
    ending a packet partly sent with ABORT; b enters Hot Reset on a run of 4
    (ltssm 0x19 at both), closes the open packet with an error eop; both go
    down exactly once and reach up again after the dwell, with no traffic
    lost after it. hot_reset_req_i at the upstream end is ignored."""
    env = await up(dut)
    a, b = env.ends
    for sb in env.sb.values():
        sb.allow_loss = True
    for hold, ev in ((1, "hot_reset"), (3000, "hot_reset_long")):
        a.send(env.packets(1, beats=75))
        b.send(env.packets(1, beats=75))
        await env.until(lambda: env.find_slot("ab", len(a.tx.slots) - 40, (gm.SOP_TLP,)) >= 0,
                        20_000, "a's packet on the pins")
        await idle_slots(env, "ab", 10)
        before, hr = downs(env), (a.hr_entries, b.hr_entries)
        a.wr("hot_reset_req_i", 1)
        await env.cycles(hold)
        a.wr("hot_reset_req_i", 0)
        await both_down_once(env, before, ev, limit_slots=200 + hold // env.slot_cycles())
        assert (a.hr_entries, b.hr_entries) == (hr[0] + 1, hr[1] + 1), (
            f"{ev}: Hot Reset entries a {a.hr_entries - hr[0]}, b {b.hr_entries - hr[1]}")
        COV_RX.sample(case="down_open")
        COV_LINK.sample(event=ev, ratio=env.ratio_name)
        await env.drain()
    # The upstream end ignores hot_reset_req_i.
    before = downs(env)
    b.wr("hot_reset_req_i", 1)
    await idle_slots(env, "ba", 60)
    b.wr("hot_reset_req_i", 0)
    assert_up_since(env, before, "hot_reset_req_i at the upstream end")
    for sb in env.sb.values():
        sb.strict()
    env.send(0, 10)
    env.send(1, 10)
    await env.drain()
    env.finish()


# --------------------------------------------------------- convergence

@cocotb.test()
async def converge(dut):
    """The convergence cases of LINK_UP.md at this top's clock ratio, with
    traffic both ways: one damaged slot each way (both ends stay up), and
    two 9 slots apart; two damaged slots within 8, the second the last of
    the window (both go down once and come back); a partner
    reset at the same slot phase, short (cause 2 on its quiet run) and longer
    than the watchdog, and at a new phase; Hot Reset entry and exit; a forged
    run of 4 bit-8-clear TRAIN words and of 4 HOT_RESET words, and of 64
    TRAIN words with bit 8 set, while runs of 3, 3 and 63 change nothing.
    After each down, both ends are up again within the bound, and each
    end leaves up through the state its cause names."""
    env = await up(dut)
    for sb in env.sb.values():
        sb.allow_loss = True

    def load():
        env.send(0, 6)
        env.send(1, 6)

    # One damaged slot: no state changes.
    for d in ("ab", "ba"):
        load()
        await idle_slots(env, d, 20)
        before = downs(env)
        env.damage(d, env.next_slot(d, 3))
        await idle_slots(env, d, 30)
        assert_up_since(env, before, f"one damaged slot on {d}")
    # One damaged marker right after an EOP: the EOP's last control
    # transfer (code bit 2) and the damaged marker read 1, 0, 0, 0 at
    # another phase; aligned, the front end must not move its phase.
    for d in ("ab", "ba"):
        await env.drain()
        await idle_slots(env, d, 20)
        s_end = env.ends[0 if d == "ab" else 1]
        before, mark = downs(env), len(s_end.tx.slots)
        s_end.send(env.packets(1, beats=30))
        await env.until(lambda: env.find_slot(d, mark, (gm.SOP_TLP,)) >= 0, 20_000, "sop sent")
        env.damage(d, env.find_slot(d, mark, (gm.SOP_TLP,)) + 30)
        await idle_slots(env, d, 60)
        assert_up_since(env, before, f"a damaged marker after an EOP on {d}")
    COV_LINK.sample(event="single_damage", ratio=env.ratio_name)
    # Two damaged slots 9 apart: each is alone in its window, no change.
    for d in ("ab", "ba"):
        load()
        await idle_slots(env, d, 20)
        before = downs(env)
        si = env.next_slot(d, 3)
        env.damage(d, si)
        env.damage(d, si + 9)
        await idle_slots(env, d, 40)
        assert_up_since(env, before, f"two damaged slots 9 apart on {d}")
    # Two damaged slots within 8: alignment loss at the receiver.
    for d in ("ab", "ba"):
        load()
        await idle_slots(env, d, 20)
        before = downs(env)
        si = env.next_slot(d, 3)
        env.damage(d, si)
        env.damage(d, si + 8)          # the last slot of the window
        # The receiver loses alignment; the sender follows on the quiet run.
        via = (gm.LTSSM_TRAINING, gm.LTSSM_RESET) if d == "ab" else (gm.LTSSM_RESET,
                                                                     gm.LTSSM_TRAINING)
        await both_down_once(env, before, f"two damaged slots on {d}", via=via)
    COV_LINK.sample(event="double_damage", ratio=env.ratio_name)
    # Partner resets: same phase short, same phase past the watchdog, new phase.
    # The partner leaves up through 0x04 on the quiet run (cause 2), or
    # 0x00 on the watchdog or the damaged markers of a new phase (cause 1).
    for i, length, phase, ev, code in (
            (1, 10, 0, "restart_same_phase", gm.LTSSM_TRAINING),
            (0, 10, 0, "restart_same_phase", gm.LTSSM_TRAINING),
            (1, 200 * env.clk_div, 0, "restart_watchdog", gm.LTSSM_RESET),
            (1, 10, 2, "restart_new_phase", gm.LTSSM_RESET),
            (0, 10, 1, "restart_new_phase", gm.LTSSM_RESET)):
        load()
        await idle_slots(env, "ab", 20)
        before = downs(env)
        env.reset(i, length, phase)
        via = [None, None]
        via[1 - i] = code
        await both_down_once(env, before, f"{ev} of {'ab'[i]}", via=via)
        COV_LINK.sample(event=ev, ratio=env.ratio_name)
    # Hot Reset entry and exit.
    load()
    await idle_slots(env, "ab", 20)
    before, since = downs(env), [e.cycle for e in env.ends]
    env.ends[0].wr("hot_reset_req_i", 1)
    await env.cycles(2)
    env.ends[0].wr("hot_reset_req_i", 0)
    await both_down_once(env, before, "Hot Reset", since=since,
                         via=(gm.LTSSM_HOT_RESET, gm.LTSSM_HOT_RESET))
    COV_LINK.sample(event="hot_reset", ratio=env.ratio_name)
    # Runs one short of a cause change nothing (LINK_UP.md, rules: shorter
    # runs of TRAIN words are ignored while up).
    for word, k_run in ((gm.TRAIN, 3), (gm.TRAIN_OK, 63), (gm.HOT_RESET, 3)):
        await env.drain()
        await idle_slots(env, "ab", 20)
        before = downs(env)
        si = env.next_slot("ab", 3)
        for k in range(k_run):
            env.replace("ab", si + k, gm.IDLE, word)
        await idle_slots(env, "ab", k_run + 30)
        assert_up_since(env, before, f"a forged run of {k_run} {gm.WORD_NAMES[word]} words")
    # Cause 3: a run of 64 TRAIN words of either value, here bit 8 set.
    await env.drain()
    await idle_slots(env, "ab", 20)
    before = downs(env)
    si = env.next_slot("ab", 3)
    for k in range(64):
        env.replace("ab", si + k, gm.IDLE, gm.TRAIN_OK)
    await both_down_once(env, before, "forged run of 64 TRAIN_OK",
                         via=(gm.LTSSM_TRAINING, gm.LTSSM_TRAINING))
    COV_LINK.sample(event="forged_train_any", ratio=env.ratio_name)
    # Forged runs on a to b: 4 bit-8-clear TRAIN words, then 4 HOT_RESET words.
    for word, ev, via in ((gm.TRAIN, "forged_train", (gm.LTSSM_TRAINING, gm.LTSSM_TRAINING)),
                          (gm.HOT_RESET, "forged_hot_reset",
                           (gm.LTSSM_TRAINING, gm.LTSSM_HOT_RESET))):
        await env.drain()
        await idle_slots(env, "ab", 20)
        before = downs(env)
        si = env.next_slot("ab", 3)
        for k in range(4):
            env.replace("ab", si + k, gm.IDLE, word)
        await both_down_once(env, before, ev, via=via)
        COV_LINK.sample(event=ev, ratio=env.ratio_name)
    await env.drain()
    for sb in env.sb.values():
        sb.strict()
    env.send(0, 10)
    env.send(1, 10)
    await env.drain()
    env.finish()


# ------------------------------------------------- transitions in training

def entered(end, since: int, old: int, new: int) -> bool:
    """``end`` went from ltssm ``old`` to ``new`` after cycle ``since``."""
    prev = None
    for n, code in end.ltssm_log:
        if n > since and prev == old and code == new:
            return True
        prev = code
    return False


async def until_code(env, end, code, what, limit=20_000):
    await env.until(lambda: end.ltssm == code, limit, what, poll=1)


@cocotb.test()
async def train_cases(dut):
    """The link-up rules from the states other than up (LINK_UP.md, rules
    and Hot Reset), each ending with both ends up again: a downstream Hot
    Reset requested while training; alignment loss while training (two
    damaged slots within 8); an upstream end entering Hot Reset from
    training on a forged run of 4 HOT_RESET words; and alignment loss of an
    upstream end in Hot Reset, which leaves it to train and re-enter Hot
    Reset on the downstream end's HOT_RESET words."""
    env = await up(dut)
    a, b = env.ends
    for sb in env.sb.values():
        sb.allow_loss = True

    async def retrain(i=1):
        """Reset end i briefly; wait until the other end trains."""
        env.reset(i, 10)
        await until_code(env, env.ends[1 - i], gm.LTSSM_TRAINING, "partner training")

    # A downstream Hot Reset requested while training.
    await retrain()
    n = a.cycle
    a.wr("hot_reset_req_i", 1)
    await env.cycles(4)
    a.wr("hot_reset_req_i", 0)
    assert entered(a, n, gm.LTSSM_TRAINING, gm.LTSSM_HOT_RESET), "a: no Hot Reset from training"
    await env.link_up(from_reset=False)
    COV_LINK.sample(event="hr_from_training", ratio=env.ratio_name)
    # Alignment loss while training: b loses alignment, then reaches up.
    await retrain(0)
    n = b.cycle
    si = env.next_slot("ab", 2)
    env.damage("ab", si)
    env.damage("ab", si + 3)
    await env.until(lambda: entered(b, n, gm.LTSSM_TRAINING, gm.LTSSM_RESET), 20_000,
                    "b: alignment loss in training")
    await env.link_up(from_reset=False)
    COV_LINK.sample(event="loss_in_training", ratio=env.ratio_name)
    # The upstream end enters Hot Reset from training on a forged run.
    await retrain(0)
    n = b.cycle
    si = env.next_slot("ab", 2)
    for k in range(4):
        env.replace("ab", si + k, gm.IDLE, gm.HOT_RESET)
    await env.until(lambda: entered(b, n, gm.LTSSM_TRAINING, gm.LTSSM_HOT_RESET), 20_000,
                    "b: Hot Reset from training")
    await env.link_up(from_reset=False)
    COV_LINK.sample(event="us_hr_from_training", ratio=env.ratio_name)
    # Alignment loss of the upstream end in Hot Reset, during a long dwell.
    n = b.cycle
    a.wr("hot_reset_req_i", 1)
    await until_code(env, b, gm.LTSSM_HOT_RESET, "b in Hot Reset")
    hr0 = b.hr_entries
    si = env.next_slot("ab", 2)
    env.damage("ab", si)
    env.damage("ab", si + 2)
    await env.until(lambda: entered(b, n, gm.LTSSM_HOT_RESET, gm.LTSSM_RESET), 20_000,
                    "b: alignment loss in Hot Reset")
    await env.until(lambda: b.hr_entries > hr0, 20_000, "b re-enters Hot Reset")
    a.wr("hot_reset_req_i", 0)
    await env.link_up(from_reset=False)
    COV_LINK.sample(event="loss_in_us_hr", ratio=env.ratio_name)
    for sb in env.sb.values():
        sb.strict()
    env.send(0, 10)
    env.send(1, 10)
    await env.drain()
    env.finish()
