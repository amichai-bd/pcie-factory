"""pcie_rc_seq tests (wiki/src/dv/rc_seq/DV_PLAN.md).

Every test drives pcie_rc_seq against the stand-in for rc_tl and compares
every output, every cycle, with the reference model (tb/rc_seq_env.py).
On top of that each test checks what it targets against the MAS directly:
the run's pass or its failing step, and the cycle bounds of the waits.
Stimulus comes from the regression seed (``cocotb.RANDOM_SEED``) alone.
"""

from __future__ import annotations

import random

import cocotb

from tb import rc_seq_env as envm
from tb.rc_seq_env import Env, RUN, SeqError, m, standin

SELFTEST = 4


def rng_of(salt: int) -> random.Random:
    seed = cocotb.RANDOM_SEED
    cocotb.log.info("seed %d", seed)
    return random.Random(seed ^ salt)


# ---------------------------------------------------------------- L0

@cocotb.test()
async def smoke(dut):
    """L0: SELFTEST passes with start_i held 1 through the release of
    rst_i; done_o then holds with start_i still 1 (no second run)."""
    env = Env(dut, SELFTEST)
    await env.expect_run(standin(1, "self"), None, "selftest")
    RUN.sample(event="start_through_reset")
    for _ in range(40):
        o = await env.cycle()
        if not (o.done and o.passed) or env.model.running:
            raise SeqError(f"cycle {env.n}: the result did not hold after the run")


# ---------------------------------------------------------------- L1

@cocotb.test()
async def selftest(dut):
    """SELFTEST passes on random stand-in latencies: every op, every jump
    condition taken and not taken, loops at and over their limit, a REQ of
    every tl-seq op and two illegal ones, a 32-DW read, an MSI, a Hot
    Reset."""
    rng = rng_of(0x5E1F)
    env = Env(dut, SELFTEST)
    for k in range(2):
        si = standin(rng.getrandbits(32), "self", ready_max=rng.randint(0, 4),
                     gap_max=rng.randint(0, 3), same_cycle=rng.choice((0.0, 0.5, 1.0)))
        await env.expect_run(si, None, f"selftest run {k}")
        if si.gap_max and any(c.op == m.MEM_RD and c.len == 32 for _, _, c, _ in si.log):
            RUN.sample(event="multi_beat_gaps")


@cocotb.test()
async def expect_mismatch(dut):
    """Every EXPECT of SELFTEST, one run each: one wrong bit of its mask
    in the read that wrote its register (a 1-DW read, beat k of the 32-DW
    read, the MSI capture's data or offset) fails the run at that EXPECT."""
    rng = rng_of(0xE7)
    env = Env(dut, SELFTEST)
    seed = rng.getrandbits(32)
    for pc, kw in envm.expect_corruptions(SELFTEST, seed):
        await env.expect_run(standin(seed, "self", **kw), pc, f"wrong read for EXPECT {pc}")


@cocotb.test()
async def status_mismatch(dut):
    """A REQ with chk 1 whose last beat has another status fails at that
    REQ, whatever the status; a chk 0 REQ never fails."""
    rng = rng_of(0x57A7)
    env = Env(dut, SELFTEST)
    seed = rng.getrandbits(32)
    seq, _ = envm.reference_run(SELFTEST, seed)
    reqs = []
    for pc, op, _, _ in seq.events:
        if op >= m.REQ:
            reqs.append(pc)
    order = list(enumerate(reqs))
    checked = [(k, pc) for k, pc in order if m.decode(seq.rom[pc]).chk]
    for st in range(len(m.STATUS_NAMES)):
        k, pc = rng.choice([(k, pc) for k, pc in checked if m.decode(seq.rom[pc]).status != st])
        want = pc
        await env.expect_run(standin(seed, "self", status_over={k: st}), want,
                             f"REQ {pc} answered {m.STATUS_NAMES[st]}")
    # The chk 0 REQ before the LINK_DOWN skip: any status passes it, and
    # the skip then fails unless the status is LINK_DOWN.
    chk0 = [(k, pc) for k, pc in order if not m.decode(seq.rom[pc]).chk]
    k, pc = chk0[-1]
    await env.expect_run(standin(seed, "self", status_over={k: m.LINK_DOWN}), None,
                         f"chk 0 REQ {pc} answered LINK_DOWN")
    await env.expect_run(standin(seed, "self", status_over={k: m.CA}), pc + 2,
                         f"chk 0 REQ {pc} answered CA")


@cocotb.test()
async def wait_bounds(dut):
    """WAIT_LINK, WAIT_MSI and HOT_RESET with timeout T: a condition first
    true at cycle 0 or T passes, at T + 1 fails at that step; HOT_RESET
    holds hot_reset_req_o from cycle 0 to the fall plus the hold, and drops
    it as a failing run ends."""
    env = Env(dut, SELFTEST)
    t = m.SELF_WAIT
    for name, knob in (("wait_link", "c_link"), ("wait_msi", "c_msi"), ("hot_reset", "c_hr")):
        for c in (0, t, t + 1):
            want = env.label(name) if c > t else None
            await env.expect_run(standin(7, "self", **{knob: c}), want, f"{name} at cycle {c}")
            hot = [o.hot_reset_req for o in env.trace]
            if name == "hot_reset":
                high = sum(hot)
                # the second HOT_RESET, hold 0, finds link_up 0: one cycle
                expect_high = c + 1 + m.SELF_HOLD + 1 if c <= t else t + 1
                if high != expect_high:
                    raise SeqError(f"hot_reset at cycle {c}: hot_reset_req_o high {high} cycles, "
                                   f"expected {expect_high}")
                if env.trace[-1].hot_reset_req:
                    raise SeqError("hot_reset_req_o still 1 when done_o rose")


@cocotb.test()
async def jump_traps(dut):
    """Each guarded trap, entered, fails at its index: FAIL, reserved ops
    0x07 and 0x0F, conditions 5 and 7, a jump to the FAIL padding past the
    program, and a jump to an index above the ROM (fail_step_o 0x0123)."""
    env = Env(dut, SELFTEST)
    for i, name in enumerate(m.TRAPS):
        si = standin(11 + i, "self")
        si.host[m.SELF_GUARD + 4 * i] = 1
        if name == "PAD":
            want = len(env.rom)
        elif name == "FAR":
            want = m.SELF_FAR
        else:
            want = env.label(f"trap_{name}")
        await env.expect_run(si, want, f"trap {name}")


@cocotb.test()
async def loop_limit(dut):
    """A polling loop of limit 3 passes with exactly 3 takes and fails at
    its jump on the fourth; a JUMP ALWAYS in the body leaves L; the loop
    after another starts from L 0."""
    env = Env(dut, SELFTEST)
    a, b = m.SELF_POLL_A, m.SELF_POLL_B
    cases = (
        ({a: [0, 0, 0, 1]}, None, "A exactly at the limit"),
        ({a: [0, 0, 0, 0, 1]}, env.label("poll_A_j"), "A one take over"),
        ({a: [0]}, env.label("poll_A_j"), "A never"),
        ({b: [0, 0, 0, 1]}, None, "B exactly at the limit"),
        ({b: [0, 0, 0, 0, 1]}, env.label("poll_B_j"), "B one take over"),
    )
    for sched, want, what in cases:
        full = {m.SELF_POLL_A: [0, 0, 0, 1], m.SELF_POLL_B: [0, 0, 0, 1],
                m.SELF_POLL_C: [4, 4, 4, 4, 4, 5], **sched}
        si = standin(5, "self", host_sched=full)
        await env.expect_run(si, want, what)
        if want is not None:
            polls = sum(1 for _, _, c, _ in si.log if c.op == m.HOST_RD and c.addr in sched)
            if polls != m.SELF_LIMIT + 1:
                raise SeqError(f"{what}: {polls} polls, expected {m.SELF_LIMIT + 1}")


@cocotb.test()
async def start_reset(dut):
    """Run start and reset (rc HAS, Sequencer run; MAS, Reset): a pulse
    starts a run; rising start_i during a run is ignored; done_o holds
    until a new rising edge, which starts a second run with done_o falling;
    rst_i in a run sets every output to its reset value, and start_i held
    1 through its release starts a new run."""
    env = Env(dut, SELFTEST)
    await env.reset(standin(21, "self"), start=0)
    await env.cycles(20)
    if env.model.running or any(o.done for o in env.trace):
        raise SeqError("a run started with no rising start_i")
    env.start = 1
    await env.cycle()
    RUN.sample(event="start_pulse")
    for k in range(400):
        env.start = int(k % 37 == 5)
        o = await env.cycle()
        if o.done:
            raise SeqError(f"cycle {env.n}: done_o during the run")
    RUN.sample(event="start_during_run")
    env.start = 0
    passed, step = await env.wait_done()
    if not passed:
        raise SeqError(f"pulsed run failed at step {step}")
    # A second run with no reset, against a fresh stand-in.
    env.new_run(standin(22, "self"))
    await env.cycles(30)
    if not all(o.done and o.passed for o in env.trace) or env.model.running:
        raise SeqError("the result did not hold until the next rising start_i")
    env.start = 1
    await env.cycle()
    o = await env.cycle()
    if o.done:
        raise SeqError("done_o still 1 in the cycle after a restart")
    RUN.sample(event="restart_after_done")
    passed, step = await env.wait_done()
    if not passed:
        raise SeqError(f"second run failed at step {step}")
    # A third run, reset in its Hot Reset hold with start_i held 1.
    env.start = 0
    env.new_run(standin(23, "self"))
    await env.cycles(2)
    env.start = 1
    await env.cycles(2)
    for _ in range(100_000):
        o = await env.cycle()
        if o.hot_reset_req or o.done:
            break
    if not o.hot_reset_req:
        raise SeqError("the run reached no Hot Reset")
    await env.reset(standin(24, "self"), start=1)
    for o in env.trace[1:]:
        if (o.cmd_valid, o.msi_ready, o.hot_reset_req, o.done, o.passed, o.fail_step) != (
                0, 0, 0, 0, 0, 0):
            raise SeqError(f"an output not at its reset value in reset: {o}")
    RUN.sample(event="reset_mid_run")
    passed, step = await env.wait_done()
    if not passed:
        raise SeqError(f"run after a reset failed at step {step}")
    RUN.sample(event="start_through_reset")


async def _program(dut, prog: int, runs: int, every: bool) -> None:
    """Program ``prog`` (the entry's PROGRAM) passes against the stand-in
    playing the endpoint; then, with one wrong bit in the read before an
    EXPECT, fails at that EXPECT: every EXPECT of the image (``every``) or
    three chosen at random."""
    rng = rng_of(0x9A00 + prog * 16 + runs)
    env = Env(dut, prog)
    name = m.PROGRAM_NAMES[prog]
    for _ in range(runs):
        seed = rng.getrandbits(32)
        await env.expect_run(standin(seed, envm.mode_of(prog)), None, f"{name} seed {seed}")
        choices = envm.expect_corruptions(prog, seed)
        for pc, kw in (choices if every else rng.sample(choices, 3)):
            await env.expect_run(standin(seed, envm.mode_of(prog), **kw), pc,
                                 f"{name} seed {seed}, EXPECT {pc} made wrong")


@cocotb.test()
async def program_enum(dut):
    """L1, PROGRAM 0."""
    await _program(dut, 0, 1, True)


@cocotb.test()
async def program_mmio(dut):
    """L1, PROGRAM 1."""
    await _program(dut, 1, 1, True)


@cocotb.test()
async def program_dma_msi(dut):
    """L1, PROGRAM 2."""
    await _program(dut, 2, 1, True)


@cocotb.test()
async def program_errors(dut):
    """L1, PROGRAM 3."""
    await _program(dut, 3, 1, True)


@cocotb.test()
async def program_random_enum(dut):
    """L2, PROGRAM 0, three stand-in seeds."""
    await _program(dut, 0, 3, False)


@cocotb.test()
async def program_random_mmio(dut):
    """L2, PROGRAM 1."""
    await _program(dut, 1, 3, False)


@cocotb.test()
async def program_random_dma_msi(dut):
    """L2, PROGRAM 2."""
    await _program(dut, 2, 3, False)


@cocotb.test()
async def program_random_errors(dut):
    """L2, PROGRAM 3."""
    await _program(dut, 3, 3, False)


async def _timeout(env: Env, fault: str, label: str) -> None:
    """With the stand-in fault ``fault``, the program's wait or polling loop
    at ``label`` runs out after its package timeout or limit and the run
    fails there; the simulator runs freely through the quiet part of a
    long wait (Env.fast_forward), and the cycles around the timeout are
    compared, so the image's timeout must equal the programs page's."""
    rng = rng_of(0x7100 + env.prog)
    env.fast = True
    skipped = env.skipped
    seed = rng.getrandbits(32)
    await env.expect_run(standin(seed, "m4", **{fault: True}), env.label(label),
                         f"{m.PROGRAM_NAMES[env.prog]} with {fault}")
    env.dut._log.info("%d cycles run freely", env.skipped - skipped)


@cocotb.test()
async def timeouts_enum(dut):
    """L2, PROGRAM 0: the link never trains; E3's WAIT_LINK fails after
    25 000 000 cycles."""
    await _timeout(Env(dut, 0), "no_link", "E3")


@cocotb.test()
async def timeouts_dma_msi(dut):
    """L2, PROGRAM 2: the copy raises no MSI; D3's WAIT_MSI fails after
    1 250 000 cycles."""
    await _timeout(Env(dut, 2), "no_msi", "D3")


@cocotb.test()
async def timeouts_errors(dut):
    """L1, PROGRAM 3: the link ignores the Hot Reset request, R10's
    HOT_RESET fails after 125 000 cycles; the error messages never come,
    R5's poll fails at its jump after 4096 takes."""
    env = Env(dut, 3)
    await _timeout(env, "no_hr", "R10_HOT_RESET")
    await _timeout(env, "no_msgs", "R5_JUMP")


@cocotb.test()
async def selftest_random(dut):
    """L2: SELFTEST with random latencies and one random fault of each
    kind, or none, per run."""
    rng = rng_of(0x5E1E)
    env = Env(dut, SELFTEST)
    for k in range(6):
        seed = rng.getrandbits(32)
        kw = dict(ready_max=rng.randint(0, 6), gap_max=rng.randint(0, 4),
                  same_cycle=rng.random())
        kind = rng.choice(("none", "expect", "status", "wait"))
        want = None
        if kind == "expect":
            pc, extra = rng.choice(envm.expect_corruptions(SELFTEST, seed))
            # the corruption numbers commands of a run with default
            # latencies, so keep them
            kw = extra
            want = pc
        elif kind == "status":
            seq, _ = envm.reference_run(SELFTEST, seed)
            reqs = [pc for pc, op, _, _ in seq.events if op >= m.REQ]
            j = rng.randrange(len(reqs))
            w = m.decode(seq.rom[reqs[j]])
            if not w.chk:
                continue
            kw = {"status_over": {j: (w.status + 1 + rng.randrange(7)) % 8}}
            want = reqs[j]
        elif kind == "wait":
            name, knob = rng.choice((("wait_link", "c_link"), ("wait_msi", "c_msi"),
                                     ("hot_reset", "c_hr")))
            c = rng.randint(0, m.SELF_WAIT + 3)
            kw[knob] = c
            want = env.label(name) if c > m.SELF_WAIT else None
        await env.expect_run(standin(seed, "self", **kw), want, f"run {k}: {kind}")
