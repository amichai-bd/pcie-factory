"""pcie_tl receive-path tests (wiki/src/dv/tl/DV_PLAN.md, plan): every
request type and error case of the ROUTING page, the receive queues and
their order, the credit release, outside D0 and link down. The checks are
the environment's (tb/tl_env.py); each test adds what it could fail on.
"""

import random

import cocotb

from tb import tl_env
from tb.tl_env import ref
from tb.tl_env import TlError

T = ref


def routing_tlps(r: ref.Regs) -> list[bytes]:
    """One TLP per request type and error case of ROUTING.md."""
    b0, b2 = r.bar0_base, r.bar2_base
    out = [
        # Served: BAR0 (cq), 3- and 4-DW headers, every write length class.
        T.memory(True, b0, 1, 0xF, 0), T.memory(True, b0 + 0x7C, 32), T.memory(False, b0, 1, 0, 0),
        T.memory(False, b0 + 0x100, 1024 - 0x40), T.memory(True, b0 + 8, 2, 0b0101, 0b1010),
        T.memory(False, b0 + 0x10, 4, four_dw=True), T.memory(True, b0 + 0x20, 3, 0b1000, 0b0001,
                                                              tc=5, attr=3),
        # Served: BAR2 (cfg), reads and writes, several DWs.
        T.memory(True, b2 + 0x10, 3, 0b1110, 0b0011), T.memory(False, b2 + 0x7F8, 4),
        T.memory(False, b2, 1, 0b0110, 0, tag=0x91, rid=0xABCD),
        # Served: configuration Type 0.
        T.config(False, 0x000), T.config(True, 0x3C1, 0b0011, bus=3, dev=7, data=0x12345678),
        T.config(False, 0x0FF, 0b1000, tag=0xFE),
        # Unsupported Request.
        T.memory(True, 0x4000, 1, 0xF, 0), T.memory(False, 0x4000, 2),
        T.memory(False, (1 << 32) | b0, 1, 0xF, 0), T.memory(True, (5 << 40) | b0, 1, 0xF, 0),
        T.memory(False, b0, 1, 0xF, 0, at=2), T.memory(True, b2, 1, 0xF, 0, at=1),
        T.memory(False, b0 + 4, 2, 0xF, 0xF, locked=True, tag=3), T.config(False, 0, fn=5),
        T.config(True, 4, type1=True), T.config(False, 4, type1=True), T.io(False, 0x10),
        T.io(True, 0x10, data=7), T.atomic(T.TYPE_FETCH_ADD, b0), T.atomic(T.TYPE_SWAP, b0, 2),
        T.atomic(T.TYPE_CAS, b0, 4), T.atomic(T.TYPE_CAS, (1 << 33) | b0, 8),
        # Messages: dropped, answered, unsupported.
        *[T.message(c) for c in T.DROPPED_MSGS], T.message(T.VENDOR_TYPE1, data=[1, 2, 3]),
        T.message(T.PME_TURN_OFF, 0b011),
        *[T.message(c) for c in (T.VENDOR_TYPE0, 0x20, 0x27, 0x18, T.PME_TO_ACK, 0x30, 0x31,
                                 0x33, 0x10, 0x12, 0x99)],
        T.message(T.VENDOR_TYPE0, data=[9] * 32),
        # Malformed: step 1 (kind), every undefined Fmt of a Type, prefixes.
        T.raw(0b100, 0), T.raw(0b101, 0x10), T.raw(0b110, 0, data_dws=1), T.raw(0b111, 0x0A),
        T.raw(0b010, T.TYPE_MEM_LOCKED, data_dws=1), T.raw(0b001, T.TYPE_IO),
        T.raw(0b011, T.TYPE_CFG0, data_dws=1), T.raw(0b001, T.TYPE_CFG1),
        T.raw(0b000, T.TYPE_FETCH_ADD), T.raw(0b001, T.TYPE_CAS), T.raw(0b000, T.TYPE_MSG),
        T.raw(0b010, 0x13, data_dws=1), T.raw(0b001, T.TYPE_CPL), T.raw(0b011, T.TYPE_CPL_LOCKED,
                                                                         data_dws=1),
        T.raw(0b000, 0b00011), T.raw(0b000, 0b01111), T.raw(0b000, 0b00110),
        # Step 3: byte enables.
        T.memory(True, b0, 1, 0xF, 0xF), T.memory(False, b0, 2, 0, 0xF),
        T.memory(False, b0, 3, 0xF, 0), T.memory(True, b0 + 4, 2, 0b0101, 0b1111),
        T.memory(False, b0, 4, 0b0111, 0xF), T.memory(True, b0, 3, 0xF, 0b1110),
        # Step 4: a 4 KiB boundary crossed.
        T.memory(False, b0 + 0xFFC, 2), T.memory(True, b0 + 0xF84, 32), T.memory(False, b0 + 4, 1024),
        # Step 5: configuration and I/O.
        T.config(False, 0, length=2, lbe=0xF), T.config(True, 0, lbe=1), T.config(False, 0, tc=1),
        T.config(False, 0, attr=1), T.io(False, 0, tc=7), T.config(True, 0, type1=True, tc=3),
        # Step 6: message TC.
        T.message(0x20, tc=1), T.message(T.UNLOCK, tc=2), T.message(T.PME_TURN_OFF, tc=1),
        T.message(T.SET_SLOT_POWER_LIMIT, tc=4, data=[1]), T.message(0x7F, tc=3),
        # Step 8: poisoned, and Unsupported Request first.
        T.memory(True, b0, 2, ep=1), T.memory(True, b2, 1, 0xF, 0, ep=1),
        T.memory(False, b0, 1, 0xF, 0, ep=1), T.config(True, 8, ep=1, data=5),
        T.config(False, 8, ep=1), T.memory(True, 0x4000, 1, 0xF, 0, ep=1),
        T.memory(False, 0x4000, 1, 0xF, 0, ep=1), T.config(False, 0, fn=1, ep=1),
        T.message(T.PME_TURN_OFF, ep=1), T.message(T.UNLOCK, ep=1), T.message(0x20, ep=1),
        # Completions go to the tracker slot.
        T.completion(data=[1, 2]), T.completion(status=1), T.completion(locked=True),
        T.completion(data=list(range(32)), tag=7),
    ]
    # A digest (TD 1) changes nothing: its DW is never data (TLP.md, TD).
    D = T.with_digest
    out += [D(T.memory(True, b0 + 0x80, 32)), D(T.memory(True, b2 + 4, 2)),
            D(T.memory(False, b0 + 0x10, 2)), D(T.config(True, 7, data=0xCAFE)),
            D(T.config(False, 7)), D(T.message(T.VENDOR_TYPE1, data=[4] * 3)),
            D(T.message(T.PME_TURN_OFF)), D(T.memory(True, 0x4000, 1, 0xF, 0)),
            D(T.completion(data=[1, 2, 3])), D(T.raw(0b100, 0))]
    return out


@cocotb.test()
async def rx_routing(dut):
    """L1: every request type and error case of ROUTING.md, one TLP each,
    in D0 with memory space on, then the same requests with memory space
    off and in D3hot (BAR decode off, configuration served)."""
    env = await tl_env.started(dut, cocotb.RANDOM_SEED, cq_ready_p=0.7, reg_ready_p=0.7,
                               reg_lat=3)
    tlps = routing_tlps(env.regs)
    reads = 0
    for tlp in tlps:
        v = ref.judge(tlp, env.regs)
        if v.outcome == "cq" and v.cls == "NP":
            reads += 1
        env.send(tlp)
    # The completer handles are never freed until the completion former
    # (#291) lands: eight reads are taken, the rest wait.
    await env.drain(stuck_reads=max(0, reads - 8))
    seen = set(env.outcomes)
    assert seen == set(tl_env.OUTCOMES), f"outcomes reached {seen}"
    for regs in ({"mem_space_en": False}, {"mem_space_en": True, "d_state": 3}):
        await env.link_down()
        env.set_regs(**regs)
        before = len(env.outcomes)
        for tlp in tlps:
            env.send(tlp)
        await env.drain()
        new = env.outcomes[before:]
        assert "cq" not in new and "bar2" not in new and "cfg" in new, regs


@cocotb.test()
async def rx_order(dut):
    """L1: the receive queues' order (ORDERING.md): a read waits for a free
    handle and a later write passes it; configuration requests pass waiting
    BAR0 reads; nothing non-posted passes a posted request the application
    or cfg holds; posted requests leave in order across cq and cfg."""
    env = await tl_env.started(dut, cocotb.RANDOM_SEED)
    b0, b2 = env.regs.bar0_base, env.regs.bar2_base
    # Ten reads: eight take a handle, two wait; a write and a configuration
    # read pass them.
    for i in range(10):
        env.send(ref.memory(False, b0 + 4 * i, 1, 0xF, 0, tag=i))
    env.send(ref.memory(True, b0 + 0x200, 4))
    env.send(ref.config(False, 0x10, tag=0x40))
    await env.drain(stuck_reads=2)
    if len(env.handles) != 8:
        raise TlError(f"{len(env.handles)} handles in use, expected 8")
    # The application stalls cq: a configuration read and a BAR2 write
    # behind a BAR0 write wait for it; a later BAR0 read too.
    await env.link_down()
    env.cq_stall_until = env.cycle + 200
    env.send(ref.memory(True, b0, 8))
    env.send(ref.config(False, 0x11))
    env.send(ref.memory(True, b2 + 0x40, 2))
    env.send(ref.memory(False, b0 + 0x40, 2))
    env.send(ref.memory(False, b2, 2))
    await env.run(150)
    if not env.cfg_jobs or env.cfg_jobs[0]["outcome"] != "cfg" or not env.bar2_writes:
        raise TlError("requests behind a stalled BAR0 write did not wait")
    await env.drain()
    # cfg stalls: BAR0 writes and reads behind a BAR2 write wait; posted
    # order holds across both.
    env.reg_stall_until = env.cycle + 120
    env.send(ref.memory(True, b2 + 0x100, 4))
    env.send(ref.memory(True, b0 + 0x300, 2))
    env.send(ref.memory(False, b0 + 0x300, 2))
    env.send(ref.message(ref.PME_TURN_OFF))
    env.send(ref.memory(True, b0 + 0x308, 1, 0xF, 0))
    await env.run(100)
    if not env.cq_writes:
        raise TlError("a BAR0 write passed a stalled BAR2 write")
    await env.drain()
    # A configuration request that arrived before a BAR0 write does not
    # wait for it: it is behind another one cfg holds when the write
    # arrives, and the application then holds the write; both requests
    # still complete.
    await env.link_down()
    env.reg_stall_until = env.cycle + 40
    env.cq_stall_until = env.cycle + 400
    env.send(ref.config(False, 0x21, tag=0x76))       # held by the cfg stall
    env.send(ref.config(False, 0x22, tag=0x77))       # waits behind it in the cfg line
    env.send(ref.memory(True, b0 + 0x10, 4))
    await env.run(200)
    if env.cfg_jobs or not env.cq_writes:
        raise TlError("a configuration request waited for a later BAR0 write")
    await env.drain()
    # A BAR0 write goes out one beat per cycle while the application takes
    # every beat (MAS, timing).
    env.send(ref.memory(True, b0 + 0x400, 32))
    await env.drain()
    first, last, n = env.cq_spans[-1]
    if last - first + 1 != n:
        raise TlError(f"a {n}-beat write took cycles {first} to {last} on cq")
    # Reads taken while Unsupported Requests are handed on: two non-posted
    # leave in one cycle and their releases merge (MAS, credit release).
    # Both lines wait behind a BAR2 write cfg holds, so they go together.
    await env.link_down()
    env.reg_stall_until = env.cycle + 80
    env.send(ref.memory(True, b2 + 0x20, 1, 0xF, 0))
    for i in range(6):
        env.send(ref.memory(False, b0 + 8 * i, 1, 0xF, 0))
    for _ in range(4):
        env.send(ref.memory(False, 0x4000, 1, 0xF, 0))
        env.send(ref.io(False, 0x40))
        env.send(ref.raw(0b000, 0b00011))
    await env.drain()
    if env.two_np == 0:
        raise TlError("no cycle with a cq read and a cfg-line request leaving together")


def random_tlp(rng: random.Random, r: ref.Regs, reads_left: list[int]) -> bytes:
    b0, b2 = r.bar0_base, r.bar2_base
    w0 = 1 << r.bar0_addr_w
    pick = rng.random()

    def addr_len(base, size, max_len):
        n = rng.randint(1, max_len)
        a = base + 4 * rng.randrange(0, size // 4)
        if (a & 0xFFF) + 4 * n > 4096:
            a = (a & ~0xFFF) + 4096 - 4 * n
        return a, n

    def bes(n):
        if n == 1:
            return rng.choice([0xF, 0x1, 0x6, 0x8, 0x0, 0xC]), 0
        return rng.choice([0xF, 0xE, 0xC, 0x8]), rng.choice([0xF, 0x7, 0x3, 0x1])

    tc, attr = rng.randrange(8), rng.randrange(4)
    if pick < 0.30:                                          # BAR0 write
        a, n = addr_len(b0, w0, 32)
        f, l = bes(n)
        return ref.memory(True, a, n, f, l, tc=tc, attr=attr, ep=int(rng.random() < 0.03),
                          data=[rng.getrandbits(32) for _ in range(n)],
                          four_dw=rng.random() < 0.2)
    if pick < 0.42:
        if reads_left[0] > 0:                                # BAR0 read
            reads_left[0] -= 1
            a, n = addr_len(b0, w0, 64)
            f, l = bes(n)
            return ref.memory(False, a, n, f, l, rid=rng.getrandbits(16), tag=rng.getrandbits(8),
                              tc=tc, attr=attr)
        return ref.memory(True, b0, 1, 0xF, 0)
    if pick < 0.52:                                          # BAR2
        a, n = addr_len(b2, 4096, 8)
        f, l = bes(n)
        return ref.memory(rng.random() < 0.5, a, n, f, l, rid=rng.getrandbits(16),
                          tag=rng.getrandbits(8), data=[rng.getrandbits(32) for _ in range(n)])
    if pick < 0.64:                                          # configuration
        return ref.config(rng.random() < 0.5, rng.randrange(1024), rng.randrange(16),
                          bus=rng.randrange(256), dev=rng.randrange(32),
                          fn=0 if rng.random() < 0.9 else rng.randrange(1, 8),
                          rid=rng.getrandbits(16), tag=rng.getrandbits(8),
                          data=rng.getrandbits(32), ep=int(rng.random() < 0.05))
    if pick < 0.72:                                          # messages
        code = rng.choice(list(ref.DROPPED_MSGS) + [ref.PME_TURN_OFF, 0x7E, 0x20, 0x30, 0x77])
        data = [rng.getrandbits(32) for _ in range(rng.randint(1, 4))] if rng.random() < 0.3 else None
        return ref.message(code, rng.randrange(8), tc=int(rng.random() < 0.1), data=data)
    if pick < 0.80:                                          # unsupported addresses, kinds
        k = rng.randrange(5)
        if k == 0:
            return ref.memory(rng.random() < 0.5, 0x10000 + 4 * rng.randrange(256), 1, 0xF, 0)
        if k == 1:
            return ref.io(rng.random() < 0.5, 4 * rng.randrange(64))
        if k == 2:
            return ref.memory(False, b0, 1, 0xF, 0, locked=True)
        if k == 3:
            return ref.atomic(rng.choice([ref.TYPE_FETCH_ADD, ref.TYPE_SWAP, ref.TYPE_CAS]), b0,
                              rng.choice([1, 2, 4]))
        return ref.config(rng.random() < 0.5, 0, type1=True)
    if pick < 0.90:                                          # completions
        n = rng.randint(0, 8)
        return ref.completion(status=rng.randrange(8), data=[rng.getrandbits(32)
                                                              for _ in range(n)] or None,
                              tag=rng.getrandbits(8), locked=rng.random() < 0.1)
    # Malformed of every step.
    k = rng.randrange(4)
    if k == 0:
        fmt = rng.choice([0b100, 0b101, 0b110, 0b111])
        return ref.raw(fmt, rng.randrange(32), data_dws=1 if fmt & 2 else 0)   # Length 1
    if k == 1:
        return ref.memory(False, b0, 3, 0x5, 0xF)
    if k == 2:
        return ref.memory(True, b0 + 0xFF8, 4)
    return ref.config(False, 0, tc=1 + rng.randrange(7))


def random_tlp_td(rng: random.Random, r: ref.Regs, reads_left: list[int]) -> bytes:
    """A random TLP, one in eight with a digest."""
    tlp = random_tlp(rng, r, reads_left)
    return ref.with_digest(tlp, rng.getrandbits(32)) if rng.random() < 0.125 else tlp


async def random_traffic(dut, *, segments: int, per_segment: int, **kw) -> tl_env.TlEnv:
    seed = cocotb.RANDOM_SEED
    rng = random.Random(seed ^ 0x290)
    env = await tl_env.started(dut, seed, **kw)
    for s in range(segments):
        if s:
            await env.link_down(rng.randint(1, 6))
            # The register state changes between segments.
            env.set_regs(mem_space_en=rng.random() < 0.85, d_state=0 if rng.random() < 0.85 else 3)
        env.cq_stall_until = env.cycle + rng.choice([0, 0, 60, 300])
        env.reg_stall_until = env.cycle + rng.choice([0, 0, 50, 200])
        reads = [8]
        for _ in range(per_segment):
            env.send(random_tlp_td(rng, env.regs, reads))
        # Stop the link partway through a segment now and then.
        if rng.random() < 0.3:
            await env.run(rng.randint(5, 200))
            await env.link_down(rng.randint(1, 4))
            continue
        await env.drain(limit=40000)
    return env


@cocotb.test()
async def rx_random(dut):
    """L1, seeded: random TLPs of every kind with application and cfg
    stalls and gaps, in segments separated by link-downs, some cut short."""
    seed = cocotb.RANDOM_SEED
    rng = random.Random(seed)
    await random_traffic(dut, segments=6, per_segment=80, cq_ready_p=rng.choice([1.0, 0.6, 0.3]),
                         reg_ready_p=rng.choice([1.0, 0.5]), reg_lat=rng.choice([1, 4]),
                         gap_p=rng.choice([0.0, 0.3]))


@cocotb.test()
async def rx_random_long(dut):
    """L2, seeded: the random traffic of rx_random, longer."""
    seed = cocotb.RANDOM_SEED
    rng = random.Random(seed)
    await random_traffic(dut, segments=24, per_segment=150, cq_ready_p=rng.choice([1.0, 0.5, 0.2]),
                         reg_ready_p=rng.choice([1.0, 0.4]), reg_lat=rng.choice([1, 6]),
                         gap_p=rng.choice([0.0, 0.5]))


@cocotb.test()
async def rx_link_down(dut):
    """L1: the data link going down mid-TLP and with requests queued drops
    them all, every handshake output returns to its reset value, the
    credits need no release, and traffic resumes cleanly."""
    env = await tl_env.started(dut, cocotb.RANDOM_SEED, cq_ready_p=0.0)
    b0, b2 = env.regs.bar0_base, env.regs.bar2_base
    for i in range(4):
        env.send(ref.memory(True, b0 + 0x80 * i, 32))
        env.send(ref.memory(False, b0 + 4 * i, 1, 0xF, 0))
        env.send(ref.config(True, i, data=i))
    env.reg_stall_until = env.cycle + 1000
    await env.run(60)
    if not env.beats and not env.txq:
        raise TlError("nothing in flight at the link-down")
    await env.link_down(3)
    env.cq_ready_p = 1.0
    env.reg_stall_until = 0
    env.send(ref.memory(True, b2, 2))
    env.send(ref.memory(True, b0, 2))
    env.send(ref.config(False, 2))
    await env.drain()
    # A reset by rst_i acts the same.
    env.send(ref.memory(True, b0, 32))
    await env.run(10)
    env.rst = 1
    await env.run(3)
    env._clear()
    env.rst = 0
    env.send(ref.memory(False, b0, 1, 0xF, 0))
    await env.drain()


@cocotb.test()
async def rx_np_collide(dut):
    """L1, FC_NPD 16 or more: three non-posted requests leave at one edge, a
    malformed I/O write with 8 data credits (Length 32, step 5), a
    configuration write (1) and a BAR0 read (0). Their releases go one per
    cycle from the next, the data credits merged up to 8 per pulse and the
    rest carried (HAS, credit release; the environment checks every
    pulse)."""
    env = await tl_env.started(dut, cocotb.RANDOM_SEED)
    if env.fc["npd"] < 16:
        raise TlError(f"rx_np_collide needs FC_NPD of 16 or more, has {env.fc['npd']}")
    b0 = env.regs.bar0_base
    io_wr = ref.raw(0b010, ref.TYPE_IO, length=32, data_dws=32)
    for k in range(3):
        env.cq_stall_until = env.reg_stall_until = env.cycle + 10_000
        env.send(ref.config(True, 0x30 + k, data=k))
        env.send(ref.memory(False, b0 + 0x40 * k, 1, 0xF, 0))
        await env.run(30)                      # both offered, held by the stalls
        if not (env.cfg_jobs and env.cq_reads):
            raise TlError("the configuration write and the read are not waiting")
        env.send(io_wr)
        while not env.beats:
            await env.step()
        start, n = env.last_start
        # The malformed TLP leaves at the edge ending its push cycle, the
        # cycle after its eop: release both stalls for exactly that edge.
        env.cq_stall_until = env.reg_stall_until = start + n
        await env.drain()
    if env.three_np != 3:
        raise TlError(f"{env.three_np} cycles with three non-posted leaves, 3 expected")
