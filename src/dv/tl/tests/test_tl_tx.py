"""pcie_tl transmit-path tests (wiki/src/dv/tl/DV_PLAN.md, plan): completer
completions and their splitting, Unsupported Request completions, rq
requests, interrupt writes, error messages and PME_TO_Ack, the transmit
order and the credit gate. The checks are the environment's
(tb/tl_env.py): every TLP sent against the transmit reference, in the
order the ORDERING page allows; each test adds what it could fail on.
"""

import random

import cocotb

from tb import tl_env
from tb.tl_env import ref
from tb.tl_env import TlError
from tests.test_tl_rx import random_tlp_td

T = ref


def completer_reads(r: ref.Regs) -> list[tuple[bytes, int]]:
    """(read, completions it must make): the COMPLETIONS.md splitting table
    and the byte-enable cases, through BAR0 and BAR2."""
    b0, b2 = r.bar0_base, r.bar2_base
    return [
        (T.memory(False, b0 + 0x40, 32), 1),                       # 128 at a 64-byte boundary
        (T.memory(False, b0 + 0x104, 32), 1),                      # 128 at offset 4
        (T.memory(False, b0 + 0x204, 128), 5),                     # 512 at offset 4
        (T.memory(False, b0, 1024), 32),                           # 4 KiB aligned
        (T.memory(False, b0 + 0x304, 64), 3),                      # 256 at offset 4
        (T.memory(False, b0 + 0x7C, 2), 1),                        # 8 bytes at 0x7C
        (T.memory(False, b0 + 0x10, 1, 0, 0), 1),                  # zero-length read
        (T.memory(False, b0 + 0x14, 1, 0b1001, 0, tc=3, attr=2), 1),
        (T.memory(False, b0 + 0x18, 2, 0b1100, 0b0011, rid=0xBEEF, tag=0xA5), 1),
        (T.memory(False, b0 + 0x3C, 3, 0b1110, 0b0111, four_dw=True, tc=7, attr=3), 1),
        (T.memory(False, b0 + 0x7F0, 40, 0b1000, 0b0001), 2),
        (T.memory(False, b2 + 0x204, 128), 5),                     # BAR2: split the same way
        (T.memory(False, b2 + 0x7C, 3, 0b1100, 0b0001, tag=0x33), 2),
        (T.memory(False, b2 + 0x8, 1, 0b0010, 0), 1),
    ]


def other_completions(r: ref.Regs) -> list[bytes]:
    """Configuration requests and Unsupported Requests (COMPLETIONS.md)."""
    b0 = r.bar0_base
    return [
        T.config(False, 0x004, 0b0110, rid=0x0102, tag=0x11),
        T.config(True, 0x3C1, 0b0011, bus=5, dev=9, data=0x12345678, tag=0x12),
        T.config(False, 0x000, tag=0x13),                          # carries the captured ID
        T.memory(False, 0x4000_0004, 4, 0b1110, 0b0011, tag=0x21),  # memory read, no BAR
        T.memory(False, b0 + 0x87, 2, 0b1110, 0b0001, locked=True, tag=0x22),
        T.memory(False, b0 + 0x100, 1024, at=1, tag=0x23),         # Byte Count 4096 as 0
        T.io(False, 0x40, tag=0x24), T.io(True, 0x44, data=3, tag=0x25),
        T.config(False, 8, type1=True, tag=0x26), T.config(False, 0, fn=3, tag=0x27),
        T.config(True, 8, ep=1, data=5, tag=0x28),                 # poisoned: discarded, UR
        T.atomic(T.TYPE_SWAP, b0, 2, tag=0x29),
    ]


@cocotb.test()
async def tx_completions(dut):
    """L1: every read of the COMPLETIONS.md splitting table and the
    byte-enable cases through BAR0 and BAR2, configuration completions and
    Unsupported Request completions (memory, locked, I/O, Type 1, function,
    poisoned, atomic); then the same with the link and application
    stalling, so the completion queues fill and cc waits."""
    env = await tl_env.started(dut, cocotb.RANDOM_SEED, reg_lat=3)
    reads = completer_reads(env.regs)
    for tlp, _ in reads:
        env.send(tlp)
    for tlp in other_completions(env.regs):
        env.send(tlp)
    await env.drain()
    want = sorted(n for tlp, n in reads if ref.judge(tlp, env.regs).outcome == "cq")
    got = sorted(env.splits)
    if got != want:
        raise TlError(f"completions per cc read {got}, expected {want}")
    kinds = set(env.tx_kinds)
    for k in ("cpl_data", "cpl_nodata", "cpl_ur", "cpl_locked"):
        if k not in kinds:
            raise TlError(f"no {k} completion sent")
    if (env.bus, env.dev) != (5, 9):
        raise TlError("the configuration write's numbers were not captured")
    # Stalls: the link takes a beat one cycle in four, the application
    # answers at once with gaps, cfg is slow.
    await env.link_down()
    env.tx_ready_p, env.cc_lat, env.cc_gap_p, env.reg_lat = 0.25, 0, 0.3, 6
    for _ in range(3):
        for tlp, _ in reads:
            env.send(tlp)
    for tlp in other_completions(env.regs):
        env.send(tlp)
    await env.drain(limit=60000)


@cocotb.test()
async def tx_requests(dut):
    """L1: rq writes and reads of every length class, 3- and 4-DW headers;
    interrupt writes and error messages from cfg, one withdrawn; PME_TO_Ack;
    rq held while bus mastering is off and in D3hot; posted requests in
    the order taken across rq, cfg and PME_TO_Ack; reads and completions
    behind the posted requests taken before them; a write passing reads
    that wait for non-posted credits."""
    env = await tl_env.started(dut, cocotb.RANDOM_SEED)
    rng = random.Random(cocotb.RANDOM_SEED)
    # Every length and both header sizes.
    for n in (1, 2, 3, 31, 32):
        for addr in (0x1000 + 4 * n, (0x7 << 40) | 0x2000):
            f, l = (0b0110, 0) if n == 1 else (0b1110, 0b0011)
            env.request(True, addr, n, f, l)
            env.request(False, addr + 0x100, n, f, l, tag=n & 7)
    for kind, addr in ((0, 0xFEE0_0000), (0, 0x1_0000_0040), (1, 0), (2, 0), (3, 0)):
        env.offer_msg(kind, addr, rng.getrandbits(32))
    await env.drain()
    for k in ("mwr32", "mwr64", "mrd32", "mrd64", "irq32", "irq64", "err_cor",
              "err_nonfatal", "err_fatal"):
        if k not in env.tx_kinds:
            raise TlError(f"no {k} sent")
    # Bus mastering off, then D3hot: rq takes no request; once allowed, all go.
    for regs in ({"bm": False}, {"d_state": 3}):
        if "bm" in regs:
            env.bm_en = False
        else:
            env.set_regs(d_state=3)
        env.request(True, 0x3000, 4)
        env.request(False, 0x3000, 4, tag=2)
        await env.run(60)
        if env.rq_cur is None or env.rq_cur["k"] or len(env.rq_q) != 1:
            raise TlError("rq took a request while not allowed")
        env.bm_en = True
        env.regs.d_state = 0
        await env.drain()
    # A read and a completion behind a write the link holds for posted
    # credits: both wait for it.
    env.cr_avail["P"] = [0, 0]
    env.cr_hold.add("P")
    env.request(True, 0x4000, 32)
    env.request(False, 0x4200, 2, tag=5)
    await env.run(40)
    env.send(ref.config(False, 0x10, tag=0x44))
    env.send(ref.memory(False, env.regs.bar0_base + 0x20, 4, tag=0x45))
    await env.run(200)
    if len(env.tx_np) != 1 or len(env.cpl_reqs) != 2 or len(env.tx_post) != 1:
        raise TlError("a read or a completion passed a posted request held for credits")
    env.cr_hold.discard("P")
    env.cr_avail["P"] = [16, 64]
    await env.drain()
    # Posted order across rq, cfg and PME_TO_Ack, all queued behind held
    # posted credits: the posted line fills, a message waits, one is
    # withdrawn, a PME_Turn_Off waits in the receive queue.
    env.cr_avail["P"] = [0, 0]
    env.cr_hold.add("P")
    env.request(True, 0x4000, 32)
    env.send(ref.message(ref.PME_TURN_OFF))
    env.offer_msg(0, 0xFEE0_0010, 7)
    env.offer_msg(1)
    env.offer_msg(2, withdraw=3)                     # not taken in time: withdrawn
    env.offer_msg(3)
    env.request(True, 0x4100, 1, 0xF, 0)
    await env.run(200)
    env.cr_hold.discard("P")
    env.cr_avail["P"] = [16, 64]
    await env.drain()
    if "pme_to_ack" not in env.tx_kinds or env.tx_kinds.count("err_nonfatal") != 1:
        raise TlError("PME_TO_Ack not sent, or the withdrawn message was sent")
    # Reads held for non-posted credits; later writes and an interrupt pass them.
    env.cr_avail["NP"] = [0, 0]
    env.cr_hold.add("NP")
    env.request(False, 0x5000, 1, 0xF, 0, tag=1)
    env.request(False, 0x5010, 8, tag=2)
    env.request(True, 0x5100, 8)
    env.offer_msg(0, 0xFEE0_0020, 9)
    await env.run(150)
    if env.tx_post or len(env.tx_np) != 2:
        raise TlError("posted requests did not pass reads waiting for credits")
    env.cr_hold.discard("NP")
    env.cr_avail["NP"] = [8, 8]
    await env.drain()


async def hold(env: tl_env.TlEnv, cls: str, avail: list[int], cycles: int = 80) -> None:
    """Set the partner's credits of a class and hold them for a while."""
    env.cr_avail[cls] = list(avail)
    env.cr_hold.add(cls)
    await env.run(cycles)


def sent(env: tl_env.TlEnv, *kinds: str) -> int:
    return sum(env.tx_kinds.count(k) for k in kinds)


@cocotb.test()
async def tx_credits(dut):
    """L1: the credit gate of each class and field (ORDERING.md, transmit
    credits): a TLP goes only with 1 header credit and its data credits
    available, the other classes passing it; the posted line filling to
    its last entry, a message and a PME_Turn_Off waiting for room."""
    env = await tl_env.started(dut, cocotb.RANDOM_SEED)
    b0 = env.regs.bar0_base
    # Posted: no header credit holds a message and a write; then one
    # header credit sends one; then data credits for one 32-DW write only.
    await hold(env, "P", [0, 64], 0)
    env.offer_msg(1)
    env.request(True, 0x1000, 1, 0xF, 0)
    await env.run(80)
    if sent(env, "err_cor", "mwr32"):
        raise TlError("a posted TLP sent with no posted header credit")
    await hold(env, "P", [1, 64])
    if sent(env, "err_cor", "mwr32") != 1:
        raise TlError("one posted header credit did not send exactly one TLP")
    await hold(env, "P", [16, 9])
    env.request(True, 0x2000, 32)
    env.request(True, 0x3000, 32)
    await env.run(150)
    if sent(env, "mwr32") != 2:
        raise TlError("posted data credits for one 32-DW write did not send exactly one")
    env.cr_hold.discard("P")
    env.cr_avail["P"] = [16, 64]
    await env.drain()
    # Non-posted: no header credit holds reads; a write passes them.
    await hold(env, "NP", [0, 0], 0)
    env.request(False, 0x4000, 4, tag=3)
    env.request(True, 0x4100, 2)
    await env.run(80)
    if sent(env, "mrd32") or len(env.tx_np) != 1:
        raise TlError("a read sent with no non-posted header credit, or the write waited")
    env.cr_hold.discard("NP")
    env.cr_avail["NP"] = [8, 8]
    await env.drain()
    # Completions: no header credit holds them; then data credits for one
    # 128-byte completion only; an rq write passes them.
    await hold(env, "CPL", [0, 0xFFF], 0)
    env.send(ref.config(False, 0x20, tag=0x50))
    env.send(ref.memory(False, b0 + 0x100, 32, tag=0x51))
    env.send(ref.memory(False, b0 + 0x200, 32, tag=0x52))
    await env.run(150)
    if sent(env, "cpl_data") or not env.cpl_reqs:
        raise TlError("a completion sent with no completion header credit")
    env.request(True, 0x5000, 1, 0xF, 0)
    await hold(env, "CPL", [8, 9], 150)
    if env.tx_post:
        raise TlError("an rq write waited for completion credits")
    if len(env.cpl_reqs) != 1:
        raise TlError("nine completion data credits did not send exactly the configuration "
                      "completion and one 128-byte completion")
    env.cr_hold.discard("CPL")
    env.cr_avail["CPL"] = None
    await env.drain()
    # The posted line fills (two writes, a message, a PME_TO_Ack); a second
    # message and a second PME_Turn_Off wait for room.
    await hold(env, "P", [0, 0], 0)
    env.request(True, 0x6000, 8)
    env.request(True, 0x6100, 8)
    await env.run(30)
    env.offer_msg(0, 0xFEE0_0000, 1)
    await env.run(10)
    env.send(ref.message(ref.PME_TURN_OFF))
    env.send(ref.message(ref.PME_TURN_OFF))
    env.offer_msg(3)
    await env.run(100)
    if len(env.tx_post) != 4 or len(env.pme) != 1 or env.msg_cur is None:
        raise TlError(f"posted line: {len(env.tx_post)} taken, {len(env.pme)} PME_Turn_Off "
                      "held, a message waiting expected 4, 1, yes")
    env.cr_hold.discard("P")
    env.cr_avail["P"] = [16, 64]
    await env.drain()
    await data_credit_edges(env)


# Lengths that are not a multiple of 4, so n / 4 rounded up and rounded
# down differ: 1, 2 and 8 data credits (HAS, credit release).
EDGE_LENS = (1, 5, 30)


async def data_credit_edges(env: tl_env.TlEnv) -> None:
    """Each TLP of n data DWs waits with n / 4 rounded up, less one, data
    credits available and goes with exactly n / 4 rounded up (ORDERING.md,
    transmit credits), on the posted line (rq writes) and on both
    completion sources: cc (BAR0 reads) and cfg (BAR2 reads). The
    environment fails any TLP offered without its data credits; here the
    TLP must also go once they are there."""
    b0, b2 = env.regs.bar0_base, env.regs.bar2_base
    cases = ([("P", "mwr32", lambda i, n: env.request(True, 0x7000 + 0x100 * i, n))]
             + [("CPL", "cpl_data", lambda i, n, b=b: env.send(
                 ref.memory(False, b + 0x400 + 0x80 * i, n, lbe=0 if n == 1 else 0xF,
                            tag=0x60 + i))) for b in (b0, b2)])
    for cls, kind, offer in cases:
        for i, n in enumerate(EDGE_LENS):
            need = (n + 3) // 4
            await hold(env, cls, [8, need - 1], 0)
            before = sent(env, kind)
            offer(i, n)
            await env.run(150)
            if sent(env, kind) != before:
                raise TlError(f"a {n}-DW {kind} sent with {need - 1} data credits, {need} due")
            await hold(env, cls, [8, need], 150)
            if sent(env, kind) != before + 1:
                raise TlError(f"a {n}-DW {kind} not sent with exactly {need} data credits")
            env.cr_hold.discard(cls)
            env.cr_avail[cls] = None if cls == "CPL" else [16, 64]
            await env.drain()


async def random_tx(dut, *, segments: int, per_segment: int) -> tl_env.TlEnv:
    """Random host traffic with random rq requests and messages, random
    credits (infinite or as small as one TLP), link and application stalls,
    segments separated by link-downs, some cut short."""
    seed = cocotb.RANDOM_SEED
    rng = random.Random(seed ^ 0x291)
    env = await tl_env.started(dut, seed, cq_ready_p=rng.choice([1.0, 0.5]),
                               reg_ready_p=rng.choice([1.0, 0.5]), reg_lat=rng.choice([1, 4]))
    for s in range(segments):
        if s:
            await env.link_down(rng.randint(1, 6))
            env.set_regs(mem_space_en=rng.random() < 0.9, d_state=0 if rng.random() < 0.9 else 3)
        env.tx_ready_p = rng.choice([1.0, 0.7, 0.3])
        env.cc_lat, env.cc_gap_p = rng.choice([0, 4, 40]), rng.choice([0.0, 0.3])
        env.rq_gap_p = rng.choice([0.0, 0.3])
        env.bm_en = rng.random() < 0.9
        for cls, small in (("P", (1, 8)), ("NP", (1, 0)), ("CPL", (1, 8))):
            pick = rng.random()
            env.cr_avail[cls] = None if pick < 0.4 else (list(small) if pick < 0.7
                                                          else [rng.randint(2, 20),
                                                                rng.randint(8, 80)])
        env.cr_lat = rng.choice([0, 20, 100])
        reads = [8]
        for _ in range(per_segment):
            pick = rng.random()
            if pick < 0.55:
                env.send(random_tlp_td(rng, env.regs, reads))
            elif pick < 0.85:
                if env.regs.d_state:
                    continue                  # outside D0 the application issues nothing
                n = rng.randint(1, 32)
                # Every address bit takes both values: a random 64 KiB block
                # below 4 GiB (3-DW header) or above it (4-DW header).
                base = rng.choice([rng.getrandbits(16) << 16,
                                   ((rng.getrandbits(32) | 1) << 32) | (rng.getrandbits(16) << 16)])
                a = base + 4 * rng.randrange(1024 - n + 1) + 0x1000 * rng.randrange(16)
                f = rng.choice([0xF, 0xE, 0xC, 0x8]) if n > 1 else rng.choice([0xF, 0x1, 0x6, 0x0])
                l = rng.choice([0xF, 0x7, 0x3, 0x1]) if n > 1 else 0
                env.request(rng.random() < 0.6, a, n, f, l, tag=rng.randrange(8))
            else:
                kind = rng.randrange(4)
                env.offer_msg(kind, (rng.getrandbits(30) << 2) | (rng.choice([0, 1]) << 40),
                              rng.getrandbits(32), withdraw=rng.choice([None, None, 1, 5]))
        if rng.random() < 0.25:
            await env.run(rng.randint(5, 300))
            await env.link_down(rng.randint(1, 4))
            continue
        if not env.bm_en:
            # rq holds every request (the environment checks rq_ready_o).
            await env.run(rng.randint(20, 200))
            env.bm_en = True
        for cls in ("P", "NP", "CPL"):
            env.cr_hold.discard(cls)
        await env.drain(limit=200000)
    return env


@cocotb.test()
async def tx_random(dut):
    """L1, seeded: random host traffic, rq requests and messages with random
    credits and stalls, in segments separated by link-downs."""
    await random_tx(dut, segments=5, per_segment=60)


@cocotb.test()
async def tx_random_long(dut):
    """L2, seeded: the random traffic of tx_random, longer."""
    await random_tx(dut, segments=20, per_segment=150)
