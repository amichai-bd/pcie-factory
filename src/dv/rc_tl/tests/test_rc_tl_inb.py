"""pcie_rc_tl inbound side: the receive checks, inbound routing, host
memory and HOST_*, the completer, the MSI capture, messages, the credit
releases and the transmit priority between the completer and the requester
(wiki/src/rtl/rc_tl/INBOUND.md, REQUESTER.md#transmit, MAS.md#commands,
wiki/src/arch/rc/HAS.md#inbound-routing).

The endpoint side sends its requests and messages with the builders of
tb/rc_tl_ep.py; the completions the DUT sends are decoded with `Cpl`. Each
test states its expected completions, host memory, captures, releases and
error record bits from the specs itself, besides the environment's
per-cycle check against the reference model.
"""

import cocotb

from tb.rc_tl_env import FC_INPUTS, Env
from tb.rc_tl_ep import EP_RID, Cpl, mem_rd, mem_wr, msg, tlp
from pcie_rc_tl_model import (CFG_RD, E_COR, E_FATAL, E_IN_UR, E_MAL, E_MSG, E_MSI_DROP,
                              E_NONFATAL, E_POIS, ERR_CLR, ERR_RD, HDR_RD, HOST_RD, HOST_WR,
                              LOCAL_UR, MEM_RD, MEM_WR, OK, TC0_CODES, Cmd, Rsp, read_bytes)

BUS, PRI, BASE, LIMIT = 0x05, 0x2A, 0x100, 0x1FF   # Secondary, Primary Bus Numbers, window
IN_WIN = BASE << 20
MSI = 0xFEE0_0000
CID = PRI << 8                                     # the Completer ID {PRI, 0, 0}
CS_SC, CS_UR = 0, 1


def seed() -> int:
    return int(cocotb.RANDOM_SEED or 0)


def bit(*bits: int) -> int:
    return sum(1 << b for b in bits)


async def open_routes(env: Env, bme: int = 1) -> None:
    """Memory Space Enable 1, Bus Master Enable `bme`, the bus numbers and
    the window, apart from host memory and the MSI window."""
    await env.hdr_wr(1, 0x0000_0002 | (bme << 2))
    await env.hdr_wr(6, (0x07 << 16) | (BUS << 8) | PRI)
    await env.hdr_wr(8, (LIMIT << 20) | (BASE << 4))


async def setup(dut, bme: int = 1, **kw) -> Env:
    env = Env(dut, seed())
    for k, v in kw.items():
        setattr(env, k, v)
    await env.start()
    await open_routes(env, bme)
    return env


async def expect_err(env: Env, want: int, where: str) -> None:
    """The error record holds exactly `want`; then clear it."""
    got = await env.err_rd()
    assert got == want, f"{where}: error record expected 0x{want:04x} got 0x{got:04x}"
    await env.err_clr()


async def host_wr(env: Env, off: int, data: int, be: int = 0xF) -> None:
    beats = await env.command(Cmd(HOST_WR, off, first_be=be, data=data))
    assert beats == [Rsp(OK)], f"HOST_WR 0x{off:x}: {beats}"


async def host_rd(env: Env, off: int) -> int:
    beats = await env.command(Cmd(HOST_RD, off))
    assert len(beats) == 1 and beats[0].status == OK, f"HOST_RD 0x{off:x}: {beats}"
    return beats[0].data


def host_size(env: Env) -> int:
    return 1 << env.p.host_mem_addr_w


# --------------------------------------------------------------- helpers

async def quiesce(env: Env, extra: int = 4) -> None:
    """Idle until the receive stream is sent and the completer, its queue
    and the transmit stage are empty (the model's state, used only to wait),
    then `extra` cycles."""
    m = env.m
    for _ in range(20000):
        if (env.rx_cur is None and not env.rx_q and m.cp is None and m.np_head is None
                and not m.npq and m.arb is None and not m.out.valid and m.wr is None):
            break
        await env.tick()
    else:
        raise AssertionError(f"cycle {env.cycle}: inbound traffic never drained")
    await env.idle(extra)


async def send(env: Env, *tlps: list[int], idle: int = 0) -> list[Cpl]:
    """Send these TLPs; return the completions the DUT sent for them."""
    k = len(env.cpls)
    for t in tlps:
        env.queue_rx(t, idle)
    await quiesce(env)
    return [Cpl.decode(c) for c in env.cpls[k:]]


def lo_be(be: int) -> int:
    return (be & -be).bit_length() - 1 if be else 0


def expect_cpls(a: int, n: int, fbe: int, lbe: int) -> list[tuple[int, int, int]]:
    """A read's completions (INBOUND.md, Completer): (DWs, Byte Count, Lower
    Address) each. T: n = 1, the span of F's enabled bytes (1 when F is 0);
    else 4n less the disabled bytes below F's first and above L's last. A
    completion takes the rest when at most 32 DWs, else ends on the highest
    64-byte boundary within 128 bytes of its first DW."""
    b = a + lo_be(fbe)
    if n == 1:
        t = (fbe.bit_length() - lo_be(fbe)) if fbe else 1
    else:
        t = 4 * n - lo_be(fbe) - (4 - lbe.bit_length())
    out, dw, left, first = [], a >> 2, n, b
    while left:
        k = left if left <= 32 else 32 - dw % 16
        out.append((k, (b + t - first) % 4096, first & 0x7F))
        dw += k
        left -= k
        first = dw * 4
    return out


def masked(mem: dict[int, int], a: int, n: int, fbe: int, lbe: int) -> list[int]:
    """The read's DWs from the test's host image, bytes F and L do not
    enable 0."""
    out = []
    for j in range(n):
        be = fbe if j == 0 else (lbe if j == n - 1 else 0xF)
        m = sum(0xFF << (8 * i) for i in range(4) if (be >> i) & 1)
        out.append(mem.get((a >> 2) + j, 0) & m)
    return out


def check_cpl(c: Cpl, *, status: int, rid: int, tag: int, k: int, bc: int, la: int,
              tc: int = 0, attr: int = 0, locked: bool = False, cid: int = CID,
              data: list[int] | None = None, where: str = "") -> None:
    """Every field of a completion (TLP.md, Completions; INBOUND.md)."""
    want = {"fmt": 0b010 if k else 0b000, "typ": 0x0B if locked else 0x0A, "tc": tc,
            "attr": attr, "td": 0, "ep": 0, "length": k, "cid": cid, "status": status,
            "bcm": 0, "bc": bc, "rid": rid, "tag": tag, "la": la}
    got = {f: getattr(c, f) for f in want}
    assert got == want, f"{where}: completion fields expected {want} got {got}"
    if data is not None:
        assert c.data == data, (f"{where}: completion data expected "
                                f"{[hex(x) for x in data]} got {[hex(x) for x in c.data]}")


async def fill_host(env: Env, mem: dict[int, int] | None = None) -> None:
    """Every host DW written with HOST_WR, a seeded non-zero pattern: host
    memory has no initial contents (INBOUND.md, Host memory)."""
    for dw in range(host_size(env) // 4):
        v = env.rng.getrandbits(32) | 0x0100_0000
        await host_wr(env, dw << 2, v)
        if mem is not None:
            mem[dw] = v


# --------------------------------------------------------------- HOST_*

@cocotb.test()
async def host_cmds(dut):
    """HOST_RD and HOST_WR (TL_SEQ.md, Host memory access; MAS, Commands):
    every first_be merges its bytes; every offset bit below HOST_MEM_ADDR_W
    selects its own DW; the refusals (check 3, check 4 for every bit at or
    above HOST_MEM_ADDR_W and bits 1:0, check 6) finish LOCAL_UR and change
    nothing; HOST_* finish OK with the link down; the beat of an
    uncontended HOST_WR is one cycle after the take's beat cycle, a
    HOST_RD's two."""
    env = await setup(dut)
    await case_host_cmds(env)


async def case_host_cmds(env: Env):
    """HOST_RD and HOST_WR (TL_SEQ.md, Host memory access; MAS, Commands):
    every first_be merges its bytes; every offset bit below HOST_MEM_ADDR_W
    selects its own DW; the refusals (check 3, check 4 for every bit at or
    above HOST_MEM_ADDR_W and bits 1:0, check 6) finish LOCAL_UR and change
    nothing; HOST_* finish OK with the link down; the beat of an
    uncontended HOST_WR is one cycle after the take's beat cycle, a
    HOST_RD's two."""
    dut = env.dut
    w = env.p.host_mem_addr_w
    # Every offset bit: each power-of-two offset holds its own value.
    for b in range(2, w):
        await host_wr(env, 1 << b, 0x1000 + b)
    await host_wr(env, 0, 0xFFFF)
    for b in range(2, w):
        assert await host_rd(env, 1 << b) == 0x1000 + b, f"offset bit {b}"
    assert await host_rd(env, 0) == 0xFFFF
    # first_be merges: start from all ones, write zeros under each mask.
    for be in range(16):
        off = 4 * (2 * be + 3)          # odd DWs: no power-of-two offset
        await host_wr(env, off, 0xFFFF_FFFF)
        await host_wr(env, off, 0x0000_0000, be)
        keep = sum(0xFF << (8 * i) for i in range(4) if not (be >> i) & 1)
        assert await host_rd(env, off) == keep, f"first_be {be:04b}"
    # Refusals change nothing.
    await host_wr(env, 0xF8, 0x600D_600D)
    refused = [Cmd(HOST_WR, 0xF8, n, data=0xBAD) for n in (0, 2, 3, 32, 63)]
    refused += [Cmd(HOST_RD, 0xF8, n) for n in (0, 2, 63)]
    refused += [Cmd(op, 0xF8 | (1 << b), data=0xBAD) for b in (0, 1) for op in (HOST_RD, HOST_WR)]
    refused += [Cmd(op, 0xF8 | (1 << b), data=0xBAD) for b in range(w, 32)
                for op in (HOST_RD, HOST_WR)]
    refused += [Cmd(op, 0xF8, 1, 0xF, lbe, 0xBAD) for lbe in range(1, 16)
                for op in (HOST_RD, HOST_WR)]
    for c in refused:
        await env.expect_cmd(c, LOCAL_UR, data=0)
    assert await host_rd(env, 0xF8) == 0x600D_600D
    for b in range(2, w):
        assert await host_rd(env, 1 << b) == 0x1000 + b, f"offset bit {b} after refusals"
    # Any first_be passes with len 1 (0 writes nothing), any last_be 0.
    await env.expect_cmd(Cmd(HOST_WR, 0xF8, first_be=0, data=0), OK)
    assert await host_rd(env, 0xF8) == 0x600D_600D
    # The link does not matter to a local command.
    await env.expect_cmd(Cmd(HOST_WR, 0xF4, data=0x1234_5678), OK, dl_up=0)
    await env.expect_cmd(Cmd(HOST_RD, 0xF4), OK, 0x1234_5678, dl_up=0)
    env.drv["dl_up_i"] = 1
    # Timing, uncontended: HOST_WR's grant at the edge after the take, its
    # beat after it; HOST_RD's data in the cycle after the grant, its beat
    # after that.
    for c, lag in ((Cmd(HOST_WR, 0xF0, data=7), 1), (Cmd(HOST_RD, 0xF0), 2)):
        k = await env.offer(c)
        take = env.cycle - 1
        beats = await env.command_beats(k)
        assert len(beats) == 1 and env.beat_cycles[-1] == take + lag, (
            f"{c}: take at {take}, beat at {env.beat_cycles[-1]}")
    assert await host_rd(env, 0xF0) == 7
    assert await env.err_rd() == 0


# --------------------------------------------------------------- posted

@cocotb.test()
async def inb_write(dut):
    """Posted writes to host memory (INBOUND.md, Receive checks; rc HAS,
    Inbound routing): every Length 1 to 32 at several offsets, the lanes by
    First BE, all four, then Last BE, a zero-length write, a digest DW not
    written, a 4-DW header below 4 GiB served; each released in the cycle
    after its eop with its data credits. Not written, released: Bus Master
    Enable 0, inside the window, outside host memory, a 4-DW address above
    4 GiB (bit 11 each); poisoned (bit 5 only), poisoned and Unsupported
    (bit 11 only)."""
    env = await setup(dut)
    await case_inb_write(env)


async def case_inb_write(env: Env):
    """Posted writes to host memory (INBOUND.md, Receive checks; rc HAS,
    Inbound routing): every Length 1 to 32 at several offsets, the lanes by
    First BE, all four, then Last BE, a zero-length write, a digest DW not
    written, a 4-DW header below 4 GiB served; each released in the cycle
    after its eop with its data credits. Not written, released: Bus Master
    Enable 0, inside the window, outside host memory, a 4-DW address above
    4 GiB (bit 11 each); poisoned (bit 5 only), poisoned and Unsupported
    (bit 11 only)."""
    dut = env.dut
    mem: dict[int, int] = {}
    await fill_host(env, mem)
    r = env.rng
    cases = []
    for n in range(1, 33):
        a = 4 * r.randrange(0, 1024 - n + 1)
        fbe, lbe = legal_bes(r, n, a)
        cases.append((a, n, fbe, lbe, r.random() < 0.3, r.random() < 0.3))
    cases += [(0x100, 1, 0x0, 0, False, False), (0x108, 2, 0x5, 0xA, False, True),
              (0x200, 3, 0x8, 0x1, True, True)]
    for a, n, fbe, lbe, td, hdr4 in cases:
        data = [r.getrandbits(32) for _ in range(n)]
        rels = len(env.rel_p)
        await send(env, mem_wr(a, data, fbe, lbe, hdr4, td=td))
        for j in range(n):
            be = fbe if j == 0 else (lbe if j == n - 1 else 0xF)
            m = sum(0xFF << (8 * i) for i in range(4) if (be >> i) & 1)
            mem[(a >> 2) + j] = (mem[(a >> 2) + j] & ~m) | (data[j] & m)
        assert env.rel_p[rels:] == [(env.rx_eop_cycles[-1], (n + 3) // 4)], (
            f"write {n} DWs: releases {env.rel_p[rels:]}, eop at {env.rx_eop_cycles[-1]}")
    for dw in range(1024):
        assert await host_rd(env, dw << 2) == mem[dw], f"host DW {dw}"
    assert await env.err_rd() == 0
    # Not written: each sets its bit and is released.
    snap = dict(mem)
    refused = [
        (mem_wr(IN_WIN | 0x40, [0xBAD]), bit(E_IN_UR)),                 # the window
        (mem_wr(host_size(env), [0xBAD]), bit(E_IN_UR)),                # past host memory
        (mem_wr(0x8000_0000, [0xBAD, 0xBAD]), bit(E_IN_UR)),            # elsewhere
        (mem_wr((1 << 32) | 0x40, [0xBAD], hdr4=True), bit(E_IN_UR)),   # above 4 GiB
        (mem_wr(0x40, [0xBAD], ep=1), bit(E_POIS)),                     # poisoned
        (mem_wr(MSI, [0xBAD], ep=1), bit(E_POIS)),                      # poisoned MSI
        (mem_wr(IN_WIN, [0xBAD], ep=1), bit(E_IN_UR)),                  # UR outranks
    ]
    for t, want in refused:
        rels = len(env.rel_p)
        await send(env, t)
        assert len(env.rel_p) == rels + 1 and not env.msi_taken and int(dut.msi_valid_o.value) == 0
        await expect_err(env, want, tlp_name(t))
    # Bus Master Enable 0: an Unsupported Request, poisoned or not.
    await env.hdr_wr(1, 0x0000_0002)
    for ep in (0, 1):
        await send(env, mem_wr(0x40, [0xBAD], ep=ep), mem_wr(MSI, [0xBAD], ep=ep))
        await expect_err(env, bit(E_IN_UR), f"Bus Master Enable 0, EP {ep}")
    await env.hdr_wr(1, 0x0000_0006)
    for dw in range(0, 1024, 7):
        assert await host_rd(env, dw << 2) == snap[dw], f"host DW {dw} changed"
    assert not env.cpls
    # The window row comes first: a window over host memory (Memory Base
    # and Limit 0, their reset values) or over the MSI window claims what
    # it covers; an empty window (Base above Limit) claims nothing.
    await env.hdr_wr(8, 0x0000_0000)
    await send(env, mem_wr(0x40, [0xBAD]))
    await expect_err(env, bit(E_IN_UR), "window over host memory")
    got = await send(env, mem_rd(0x40, 1, tag=1))
    check_cpl(got[0], status=CS_UR, rid=EP_RID, tag=1, k=0, bc=4, la=0x40, where="window")
    await expect_err(env, bit(E_IN_UR), "read under the window")
    await env.hdr_wr(8, (0xFEE << 20) | (0xFEE << 4))
    await send(env, mem_wr(MSI, [0xBAD]))
    assert int(dut.msi_valid_o.value) == 0
    await expect_err(env, bit(E_IN_UR), "window over the MSI window")
    await env.hdr_wr(8, 0x0000_0010)                    # Base 1, Limit 0
    await send(env, mem_wr(0x40, [0x600D]))
    assert await host_rd(env, 0x40) == 0x600D and await env.err_rd() == 0
    await env.hdr_wr(8, (LIMIT << 20) | (BASE << 4))


def tlp_name(t: list[int]) -> str:
    return " ".join(f"{x:08x}" for x in t[:4])


# --------------------------------------------------------------- completer

@cocotb.test()
async def inb_read(dut):
    """The completer (INBOUND.md, Completer): host memory reads of 1 to 64
    DWs at every alignment in a 64-byte block, of 1024 DWs (Byte Count 0),
    a zero-length read; each split on the read completion boundary with
    its Byte Count, Lower Address, bytes outside F and L 0, TC, Attr,
    Requester ID and Tag of the request and the Completer ID from the
    Primary Bus Number; Unsupported Request completions for Bus Master
    Enable 0, the window, outside host memory, above 4 GiB (Byte Count T,
    Lower Address B), a locked read (CplLk), configuration, I/O and atomic
    requests (Byte Count 4, Lower Address 0), poisoned reads (bit 5, not
    11); each non-posted request released with its data credits."""
    env = await setup(dut)
    await case_inb_read(env)


async def case_inb_read(env: Env):
    """The completer (INBOUND.md, Completer): host memory reads of 1 to 64
    DWs at every alignment in a 64-byte block, of 1024 DWs (Byte Count 0),
    a zero-length read; each split on the read completion boundary with
    its Byte Count, Lower Address, bytes outside F and L 0, TC, Attr,
    Requester ID and Tag of the request and the Completer ID from the
    Primary Bus Number; Unsupported Request completions for Bus Master
    Enable 0, the window, outside host memory, above 4 GiB (Byte Count T,
    Lower Address B), a locked read (CplLk), configuration, I/O and atomic
    requests (Byte Count 4, Lower Address 0), poisoned reads (bit 5, not
    11); each non-posted request released with its data credits."""
    dut = env.dut
    mem: dict[int, int] = {}
    await fill_host(env, mem)
    r = env.rng
    reads = [(4 * d, n, 0xF, 0xF if n > 1 else 0) for d in range(16) for n in (1, 2, 16, 17, 33)]
    reads += [(0x20, 64, 0xF, 0xF), (0x24, 64, 0xE, 0x7), (0, 1024, 0xF, 0xF),
              (0x40, 1024 - 16, 0xC, 0x3), (0x30, 1, 0x0, 0), (0x34, 1, 0x6, 0),
              (0x38, 2, 0x8, 0x1), (0x38, 2, 0x5, 0xA)]
    for k, (a, n, fbe, lbe) in enumerate(reads):
        tc, attr, tag = r.getrandbits(3), r.getrandbits(2), r.getrandbits(8)
        rels = len(env.rel_np)
        got = await send(env, mem_rd(a, n, fbe, lbe, tag=tag, tc=tc, attr=attr, td=k & 1))
        want = expect_cpls(a, n, fbe, lbe)
        data = masked(mem, a, n, fbe, lbe)
        assert len(got) == len(want), f"read 0x{a:x} n {n}: {len(got)} completions"
        j = 0
        for c, (kk, bc, la) in zip(got, want):
            check_cpl(c, status=CS_SC, rid=EP_RID, tag=tag, k=kk, bc=bc, la=la, tc=tc,
                      attr=attr, data=data[j:j + kk], where=f"read 0x{a:x} n {n}")
            j += kk
        assert [d for _, d in env.rel_np[rels:]] == [0], f"read release {env.rel_np[rels:]}"
    # The Completer ID follows the Primary Bus Number.
    await env.hdr_wr(6, (0x07 << 16) | (BUS << 8) | 0x77)
    got = await send(env, mem_rd(0x10, 1, tag=9))
    check_cpl(got[0], status=CS_SC, rid=EP_RID, tag=9, k=1, bc=4, la=0x10, cid=0x7700,
              data=[mem[4]])
    await env.hdr_wr(6, (0x07 << 16) | (BUS << 8) | PRI)
    assert await env.err_rd() == 0
    # Unsupported Requests: one completion without data, bit 11.
    t, b = 6, 0x46   # a 2-DW read at 0x44 of bytes 0x46 to 0x48: T 3, B 0x46
    urs = [(mem_rd(IN_WIN | 0x44, 2, 0xC, 0x1, tag=t), 3, b, False, 0),
           (mem_rd(host_size(env) + 0x44, 2, 0xC, 0x1, tag=t), 3, b, False, 0),
           (mem_rd((1 << 32) | 0x44, 2, 0xC, 0x1, hdr4=True, tag=t), 3, b, False, 0),
           (mem_rd(0x44, 2, 0xC, 0x1, locked=True, tag=t), 3, b, True, 0),
           (mem_rd(MSI | 0x44, 2, 0xC, 0x1, tag=t), 3, b, False, 0),
           (tlp(0b000, 0x04, tag=t), 4, 0, False, 0),                   # configuration read
           (tlp(0b010, 0x05, tag=t, data=[1]), 4, 0, False, 1),         # Type 1 write
           (tlp(0b000, 0x02, tag=t), 4, 0, False, 0),                   # I/O read
           (tlp(0b010, 0x02, tag=t, data=[1]), 4, 0, False, 1),         # I/O write
           (tlp(0b010, 0x0C, addr=0x40, tag=t, data=[1]), 4, 0, False, 1),          # FetchAdd
           (tlp(0b010, 0x0E, addr=0x40, length=8, tag=t, data=[1] * 8), 4, 0, False, 2)]  # CAS
    for tl, bc, la, locked, dc in urs:
        rels = len(env.rel_np)
        got = await send(env, tl)
        assert len(got) == 1, f"{tlp_name(tl)}: {len(got)} completions"
        check_cpl(got[0], status=CS_UR, rid=EP_RID, tag=t, k=0, bc=bc, la=la, locked=locked,
                  where=tlp_name(tl))
        assert [d for _, d in env.rel_np[rels:]] == [dc], f"{tlp_name(tl)}: {env.rel_np[rels:]}"
        await expect_err(env, bit(E_IN_UR), tlp_name(tl))
    # Poisoned: a read routing would serve gets an Unsupported Request
    # completion and bit 5 only; one it refuses, bit 11 only.
    got = await send(env, mem_rd(0x44, 2, 0xC, 0x1, tag=3, ep=1))
    check_cpl(got[0], status=CS_UR, rid=EP_RID, tag=3, k=0, bc=3, la=0x46, where="poisoned")
    await expect_err(env, bit(E_POIS), "poisoned read")
    got = await send(env, mem_rd(IN_WIN, 1, tag=4, ep=1))
    check_cpl(got[0], status=CS_UR, rid=EP_RID, tag=4, k=0, bc=4, la=0, where="poisoned UR")
    await expect_err(env, bit(E_IN_UR), "poisoned and refused")
    # Bus Master Enable 0: an Unsupported Request (rc HAS, Inbound routing).
    await env.hdr_wr(1, 0x0000_0002)
    got = await send(env, mem_rd(0x44, 2, 0xC, 0x1, tag=5))
    check_cpl(got[0], status=CS_UR, rid=EP_RID, tag=5, k=0, bc=3, la=0x46, where="BME 0")
    await expect_err(env, bit(E_IN_UR), "Bus Master Enable 0")
    await env.hdr_wr(1, 0x0000_0006)
    got = await send(env, mem_rd(0x44, 1, tag=6))
    check_cpl(got[0], status=CS_SC, rid=EP_RID, tag=6, k=1, bc=4, la=0x44, data=[mem[0x11]])


# --------------------------------------------------------------- MSI

@cocotb.test()
async def msi_capture(dut):
    """The MSI capture (INBOUND.md, MSI capture; TL_SEQ.md): a 1-DW write
    into the window is captured at its eop, offset address bits 11:2, data
    as received whatever its byte enables, in arrival order; four held, a
    fifth and sixth dropped with bit 12; a take at the edge a capture
    arrives full makes room; msi_valid_o never follows msi_ready_i within a
    cycle; captures survive link down and empty on rst_i; when one is
    captured, every DMA write before it is in host memory. Not captured:
    Bus Master Enable 0, 2 DWs, a read (Unsupported Requests, bit 11)."""
    env = await setup(dut)
    await case_msi_capture(env)


async def case_msi_capture(env: Env):
    """The MSI capture (INBOUND.md, MSI capture; TL_SEQ.md): a 1-DW write
    into the window is captured at its eop, offset address bits 11:2, data
    as received whatever its byte enables, in arrival order; four held, a
    fifth and sixth dropped with bit 12; a take at the edge a capture
    arrives full makes room; msi_valid_o never follows msi_ready_i within a
    cycle; captures survive link down and empty on rst_i; when one is
    captured, every DMA write before it is in host memory. Not captured:
    Bus Master Enable 0, 2 DWs, a read (Unsupported Requests, bit 11)."""
    dut = env.dut
    caps = [(0, 0x1111_1111, 0xF), (1023, 0x2222_2222, 0x0), (0x155, 0xDEAD_BEEF, 0x3),
            (0x2AA, 0x0BAD_F00D, 0x8)]
    for k, (off, data, be) in enumerate(caps):
        # Every other one with a digest DW after its data.
        await send(env, mem_wr(MSI | (off << 2), [data], be, td=k & 1))
        assert env.rel_p[-1] == (env.rx_eop_cycles[-1], 1)
    assert int(dut.msi_valid_o.value) == 1
    assert env.m.msi == [(o, d) for o, d, _ in caps]
    # Full: the fifth and sixth are dropped.
    await send(env, mem_wr(MSI | 0x10, [5]), mem_wr(MSI | 0x14, [6]))
    await expect_err(env, bit(E_MSI_DROP), "capture full")
    # msi_valid_o does not follow msi_ready_i inside a cycle.
    for ready in (1, 0, 1, 0):
        env.drv["msi_ready_i"] = ready
        await env.apply()
        assert int(dut.msi_valid_o.value) == 1 and int(dut.msi_o.value) == (caps[0][0] << 32) | caps[0][1]
    env.drv["msi_ready_i"] = 0
    # Taken one per cycle, oldest first.
    env.drv["msi_ready_i"] = 1
    await env.idle(4)
    env.drv["msi_ready_i"] = 0
    assert env.msi_taken[-4:] == [(o, d) for o, d, _ in caps]
    await env.idle(2)
    assert int(dut.msi_valid_o.value) == 0 and int(dut.msi_o.value) == 0
    # A take at the edge a capture arrives full makes room.
    for k in range(4):
        await send(env, mem_wr(MSI | (k << 2), [k]))
    env.queue_rx(mem_wr(MSI | 0x40, [0x40]))
    while env.rx_cur is None:
        await env.tick()
    while env.rx_cur is not None:
        env.drv["msi_ready_i"] = int(env.rx_idx == len(env.rx_cur) - 1)
        await env.tick()
    env.drv["msi_ready_i"] = 0
    await env.idle(2)
    assert env.m.msi == [(1, 1), (2, 2), (3, 3), (0x10, 0x40)] and await env.err_rd() == 0
    # Survives link down; empties on rst_i.
    env.drv["dl_up_i"] = 0
    await env.idle(3)
    env.drv["dl_up_i"] = 1
    await env.idle(2)
    assert int(dut.msi_valid_o.value) == 1 and env.m.msi[0] == (1, 1)
    await env.reset(2)
    await open_routes(env)
    assert int(dut.msi_valid_o.value) == 0
    # Not captured: Unsupported Requests.
    await send(env, mem_wr(MSI | 0x8, [1, 2]))
    await expect_err(env, bit(E_IN_UR), "2-DW write")
    got = await send(env, mem_rd(MSI | 0x8, 1, tag=1))
    check_cpl(got[0], status=CS_UR, rid=EP_RID, tag=1, k=0, bc=4, la=0x8, where="read")
    await expect_err(env, bit(E_IN_UR), "read of the window")
    await env.hdr_wr(1, 0x0000_0002)
    await send(env, mem_wr(MSI | 0x8, [1]))
    await expect_err(env, bit(E_IN_UR), "Bus Master Enable 0")
    await env.hdr_wr(1, 0x0000_0006)
    assert int(dut.msi_valid_o.value) == 0
    # DMA writes, then their MSI: each write is in host memory by the cycle
    # the capture is offered.
    data = [env.rng.getrandbits(32) for _ in range(32)]
    env.queue_rx(mem_wr(0x80, data))
    env.queue_rx(mem_wr(MSI, [0x5A]))
    while int(dut.msi_valid_o.value) == 0:
        await env.tick()
    env.drv["msi_ready_i"] = 1
    await env.tick()
    env.drv["msi_ready_i"] = 0
    for j in range(32):
        assert await host_rd(env, 0x80 + 4 * j) == data[j], f"DMA DW {j}"


# --------------------------------------------------------------- messages

@cocotb.test()
async def inb_msg(dut):
    """Messages (INBOUND.md, Receive checks; rc HAS, Inbound routing):
    ERR_COR, ERR_NONFATAL and ERR_FATAL set bits 7, 8 and 9, any other code
    bit 10, with any routing subfield and with data; a TC-0 code with TC
    not 0 is malformed (bit 6), another code with TC not 0 is not;
    poisoned, bit 5 only; Bus Master Enable 0 does not gate them; each is
    released in the cycle after its eop with its data credits."""
    env = await setup(dut)
    await case_inb_msg(env)


async def case_inb_msg(env: Env):
    """Messages (INBOUND.md, Receive checks; rc HAS, Inbound routing):
    ERR_COR, ERR_NONFATAL and ERR_FATAL set bits 7, 8 and 9, any other code
    bit 10, with any routing subfield and with data; a TC-0 code with TC
    not 0 is malformed (bit 6), another code with TC not 0 is not;
    poisoned, bit 5 only; Bus Master Enable 0 does not gate them; each is
    released in the cycle after its eop with its data credits."""
    dut = env.dut
    cases = [(msg(0x30), bit(E_COR)), (msg(0x31, 0b100), bit(E_NONFATAL)),
             (msg(0x33, 0b011), bit(E_FATAL)), (msg(0x30, data=[1, 2, 3, 4, 5]), bit(E_COR)),
             (msg(0x7E, tc=5), bit(E_MSG)), (msg(0x7F, data=[1]), bit(E_MSG)),
             (msg(0x1B, 0b101), bit(E_MSG)), (msg(0x20, 0b100), bit(E_MSG)),
             (msg(0x50, data=[3]), bit(E_MSG)), (msg(0x30, ep=1), bit(E_POIS)),
             (msg(0x7E, ep=1, data=[1]), bit(E_POIS))]
    cases += [(msg(code, tc=tc), bit(E_MAL)) for code in sorted(TC0_CODES) for tc in (1, 7)]
    cases += [(msg(0x30, ep=1, tc=2), bit(E_MAL))]
    for bme in (1, 0):
        await env.hdr_wr(1, 0x0000_0002 | (bme << 2))
        for t, want in cases:
            rels = len(env.rel_p)
            await send(env, t)
            n = ((t[0] >> 24) & 0xFF) if t[0] & 0x40 else 0
            assert env.rel_p[rels:] == [(env.rx_eop_cycles[-1], (n + 3) // 4)], tlp_name(t)
            await expect_err(env, want, tlp_name(t))
    assert not env.cpls and not env.msi_taken


# --------------------------------------------------------------- malformed

KINDS = {0x00: (0, 1, 2, 3), 0x01: (0, 1), 0x02: (0, 2), 0x04: (0, 2), 0x05: (0, 2),
         0x0A: (0, 2), 0x0B: (0, 2), 0x0C: (2, 3), 0x0D: (2, 3), 0x0E: (2, 3)}


@cocotb.test()
async def inb_malformed(dut):
    """The receive checks (INBOUND.md, step 1 and 3 to 6): every Fmt and
    Type pair that is no kind (TLP.md, Kinds) is malformed, bit 6, and so
    are the memory byte-enable breaks, a request crossing 4 KiB, a
    configuration or I/O request with Length, Last BE, TC or Attr wrong;
    malformed outranks poisoned; each is dropped and released by the class
    the dll counted (a completion kind not), a non-posted one through the
    queue, with no completion. The legal neighbours are not malformed."""
    env = await setup(dut)
    await case_inb_malformed(env)


async def case_inb_malformed(env: Env):
    """The receive checks (INBOUND.md, step 1 and 3 to 6): every Fmt and
    Type pair that is no kind (TLP.md, Kinds) is malformed, bit 6, and so
    are the memory byte-enable breaks, a request crossing 4 KiB, a
    configuration or I/O request with Length, Last BE, TC or Attr wrong;
    malformed outranks poisoned; each is dropped and released by the class
    the dll counted (a completion kind not), a non-posted one through the
    queue, with no completion. The legal neighbours are not malformed."""
    dut = env.dut
    await fill_host(env)
    mal = []
    for fmt in range(8):
        for typ in range(32):
            known = (fmt in (1, 3)) if typ >> 3 == 0b10 else fmt in KINDS.get(typ, ())
            if known:
                continue
            n = 2
            mal.append(tlp(fmt, typ, addr=0x40, length=n, fbe=0xF, lbe=0xF,
                           data=[0xBAD] * n if fmt & 2 else None))
    mal += [mem_wr(0x40, [1], 0xF, 0x1), mem_rd(0x40, 1, 0xF, 0x8),          # Length 1, Last BE
            mem_wr(0x40, [1, 2], 0x0, 0xF), mem_rd(0x40, 2, 0xF, 0x0),        # a zero BE
            mem_rd(0x44, 2, 0x5, 0xA), mem_wr(0x40, [1, 2, 3], 0x7, 0xF),     # gaps
            mem_rd(0x40, 3, 0xF, 0x2), mem_rd(0xFFC, 2), mem_wr(0xF84, [0] * 32),  # 4 KiB
            mem_rd(0x40, 1024), mem_wr(0x40, [1], 0xF, 0x1, ep=1),
            mem_rd(0x40, 1, 0xF, 0x1, locked=True),
            tlp(0b000, 0x04, length=2), tlp(0b000, 0x04, length=0), tlp(0b010, 0x02, lbe=1, data=[1]),
            tlp(0b000, 0x05, tc=1), tlp(0b000, 0x02, attr=2), tlp(0b010, 0x04, attr=1, data=[1])]
    for t in mal:
        b0 = t[0] & 0xFF
        fmt, typ = b0 >> 5, b0 & 0x1F
        cpl_kind = typ in (0x0A, 0x0B)
        posted = (typ == 0 and fmt & 2) or typ >> 3 == 0b10
        n = ((t[0] >> 24) & 0xFF) if fmt & 2 else 0
        rp, rnp, k = len(env.rel_p), len(env.rel_np), len(env.cpls)
        await send(env, t)
        want_p = [] if cpl_kind or not posted else [(n + 3) // 4]
        want_np = [] if cpl_kind or posted else [(n + 3) // 4]
        assert [d for _, d in env.rel_p[rp:]] == want_p and \
               [d for _, d in env.rel_np[rnp:]] == want_np, (
            f"{tlp_name(t)}: releases {env.rel_p[rp:]} {env.rel_np[rnp:]}")
        assert len(env.cpls) == k, f"{tlp_name(t)} answered"
        await expect_err(env, bit(E_MAL), tlp_name(t))
    # The legal neighbours.
    ok = [mem_rd(0x40, 1, 0x0, 0), mem_rd(0x40, 2, 0x5, 0xA), mem_rd(0x40, 2, 0xE, 0x1),
          mem_rd(0xFF8, 2), mem_wr(0xF80, [0] * 31 + [7]), mem_rd(0, 1024)]
    for t in ok:
        await send(env, t)
        assert await env.err_rd() == 0, tlp_name(t)


@cocotb.test()
async def inb_host_edge(dut):
    """Host memory smaller than 4 KiB (rc HAS, Inbound routing: entirely
    inside host memory): a write or read ending on its last DW is served,
    one straddling its end (inside one 4 KiB block) is an Unsupported
    Request, nothing written."""
    env = await setup(dut)
    await case_inb_host_edge(env)


async def case_inb_host_edge(env: Env):
    """Host memory smaller than 4 KiB (rc HAS, Inbound routing: entirely
    inside host memory): a write or read ending on its last DW is served,
    one straddling its end (inside one 4 KiB block) is an Unsupported
    Request, nothing written."""
    dut = env.dut
    base, size = env.p.host_mem_base, host_size(env)
    assert size < 0x1000 and (base + size) & 0xFFF, "needs a small host memory inside a 4 KiB block"
    end = base + size
    await host_wr(env, size - 8, 0x2222)
    await host_wr(env, size - 4, 0x1111)
    await send(env, mem_wr(end - 8, [0xA, 0xB]))
    assert await host_rd(env, size - 4) == 0xB and await env.err_rd() == 0
    got = await send(env, mem_rd(end - 8, 2, tag=1))
    check_cpl(got[0], status=CS_SC, rid=EP_RID, tag=1, k=2, bc=8, la=(end - 8) & 0x7F,
              data=[0xA, 0xB], where="read to the end")
    await send(env, mem_wr(end - 4, [0xBAD, 0xBAD]))
    await expect_err(env, bit(E_IN_UR), "write past the end")
    assert await host_rd(env, size - 4) == 0xB
    got = await send(env, mem_rd(end - 4, 2, tag=2))
    check_cpl(got[0], status=CS_UR, rid=EP_RID, tag=2, k=0, bc=8, la=(end - 4) & 0x7F,
              where="read past the end")
    await expect_err(env, bit(E_IN_UR), "read past the end")


# --------------------------------------------------------------- ordering

@cocotb.test()
async def inb_order(dut):
    """Ordering and transmit priority (INBOUND.md, Host memory; REQUESTER.md,
    Transmit): a DMA write lands before a later read's completion and a
    later HOST_RD; a requester posted write waiting for credits holds a
    completion back, then goes first; a completion goes before a requester
    non-posted request; a non-posted request waiting for credits does not
    block a completion, and a completion waiting for completion credits
    lets a non-posted request go. Forward progress: under back-to-back
    32-DW writes and long reads, every HOST_* finishes within 34 cycles of
    asking for host memory (the RTL asserts the bound on every access)."""
    env = await setup(dut)
    await case_inb_order(env)


async def case_inb_order(env: Env):
    """Ordering and transmit priority (INBOUND.md, Host memory; REQUESTER.md,
    Transmit): a DMA write lands before a later read's completion and a
    later HOST_RD; a requester posted write waiting for credits holds a
    completion back, then goes first; a completion goes before a requester
    non-posted request; a non-posted request waiting for credits does not
    block a completion, and a completion waiting for completion credits
    lets a non-posted request go. Forward progress: under back-to-back
    32-DW writes and long reads, every HOST_* finishes within 34 cycles of
    asking for host memory (the RTL asserts the bound on every access)."""
    dut = env.dut
    for dw in range(64):
        await host_wr(env, dw << 2, 0)
    # A write, then a read of the same DWs: the completion carries the write.
    data = [0x1000 + j for j in range(32)]
    got = await send(env, mem_wr(0x40, data), mem_rd(0x40, 32, tag=1))
    assert got[0].data == data, "a read passed an earlier write"
    got = await send(env, mem_wr(0x40, [0xAB]), mem_rd(0x40, 1, tag=2))
    assert got[0].data == [0xAB]
    assert await host_rd(env, 0x40) == 0xAB
    # A completion never passes a waiting posted write: hold the completion
    # with no completion credit, offer a posted write with no posted credit,
    # then give the completion its credit: nothing goes until the posted
    # write may, and it goes first.
    env.drv["fc_cplh_avail_i"] = 0
    env.queue_rx(mem_rd(0x80, 1, tag=3))
    await quiesce_rx(env)
    await env.idle(8)
    env.drv["fc_ph_avail_i"] = 0
    k = await env.offer(Cmd(MEM_WR, IN_WIN, data=0x77))
    env.drv["fc_cplh_avail_i"] = 0xFF
    cpls, sent = len(env.cpls), len(env.sent)
    await env.idle(20)
    assert len(env.cpls) == cpls and len(env.sent) == sent, \
        "the completion passed a waiting posted write"
    env.drv["fc_ph_avail_i"] = 0xFF
    await env.command_beats(k)
    await quiesce(env)
    assert len(env.cpls) == cpls + 1 and env.cpl_cycles[-1] > env.tx_eop_cycles[-2], \
        "the posted write did not go before the completion"
    # A completion goes before a non-posted request when both may go.
    env.drv["fc_cplh_avail_i"] = env.drv["fc_nph_avail_i"] = 0
    env.queue_rx(mem_rd(0x80, 1, tag=4))
    await quiesce_rx(env)
    await env.idle(8)
    k = await env.offer(Cmd(MEM_RD, IN_WIN, 1, 0xF))
    await env.idle(4)
    env.drv["fc_cplh_avail_i"] = env.drv["fc_nph_avail_i"] = 0xFF
    await env.command_beats(k)
    await quiesce(env)
    assert env.cpl_cycles[-1] < env.tx_eop_cycles[-1] and Cpl.decode(env.cpls[-1]).tag == 4, \
        "a non-posted request passed a completion"
    # A non-posted request without credits does not block a completion.
    env.drv["fc_nph_avail_i"] = 0
    k = await env.offer(Cmd(MEM_RD, IN_WIN, 1, 0xF))
    got = await send(env, mem_rd(0x80, 1, tag=5))
    assert got and got[0].tag == 5, "a completion waited for a non-posted request"
    env.drv["fc_nph_avail_i"] = 0xFF
    await env.command_beats(k)
    # A completion without completion credits lets a non-posted request go.
    env.drv["fc_cplh_avail_i"] = 0
    env.queue_rx(mem_rd(0x80, 1, tag=0x66))
    await quiesce_rx(env)
    await env.idle(8)
    sent = len(env.sent)
    k = await env.offer(Cmd(MEM_RD, IN_WIN, 1, 0xF))
    await env.command_beats(k)
    assert len(env.sent) == sent + 1 and all(Cpl.decode(c).tag != 0x66 for c in env.cpls)
    env.drv["fc_cplh_avail_i"] = 0xFF
    await quiesce(env)
    assert Cpl.decode(env.cpls[-1]).tag == 0x66
    # Data credits: a 32-DW completion waits for 8.
    env.drv["fc_cpld_avail_i"] = 7
    env.queue_rx(mem_rd(0x0, 32, tag=7))
    await quiesce_rx(env)
    await env.idle(60)
    assert Cpl.decode(env.cpls[-1]).tag == 0x66
    env.drv["fc_cpld_avail_i"] = 8
    await quiesce(env)
    assert Cpl.decode(env.cpls[-1]).tag == 7
    # Forward progress under the heaviest load.
    worst = 0
    for rnd in range(12):
        for _ in range(4):
            env.queue_rx(mem_wr(0x100, [rnd] * 32))
            env.queue_rx(mem_rd(0x0, 64, tag=rnd))
        for _ in range(6):
            c = Cmd(HOST_RD, 0x200) if rnd & 1 else Cmd(HOST_WR, 0x200, data=rnd)
            k = await env.offer(c)
            take = env.cycle - 1
            await env.command_beats(k)
            worst = max(worst, env.beat_cycles[-1] - take - (2 if c.op == HOST_RD else 1))
    await quiesce(env)
    assert 0 < worst <= 33, f"a HOST_* waited {worst} cycles for host memory"
    assert await env.err_rd() == 0


async def quiesce_rx(env: Env) -> None:
    """Idle until the receive stream has sent everything queued."""
    while env.rx_cur is not None or env.rx_q:
        await env.tick()


# --------------------------------------------------------------- link down

@cocotb.test()
async def inb_link_down(dut):
    """Link down (MAS, Reset and link down): every non-posted request held
    is dropped with no completion and no release; a completion being sent
    is abandoned; a write cut by link down keeps the DWs already taken;
    captures, host memory and the error record stay; after link-up new
    requests are served."""
    env = await setup(dut, rx_gap_p=0.0)
    await case_inb_link_down(env)


async def case_inb_link_down(env: Env):
    """Link down (MAS, Reset and link down): every non-posted request held
    is dropped with no completion and no release; a completion being sent
    is abandoned; a write cut by link down keeps the DWs already taken;
    captures, host memory and the error record stay; after link-up new
    requests are served."""
    dut = env.dut
    for dw in range(64):
        await host_wr(env, dw << 2, 0x100 + dw)
    await send(env, mem_wr(MSI, [0x99]), msg(0x30))
    # Fill the queue while the link holds the completer's output.
    env.tx_ready_p = 0.0
    for t in range(env.p.fc_nph + 1):
        env.queue_rx(mem_rd(0x0, 8, tag=t))
    await quiesce_rx(env)
    await env.idle(30)
    rels, cpls = len(env.rel_np), len(env.cpls)
    env.drv["dl_up_i"] = 0
    await env.idle(4)
    env.drv["dl_up_i"] = 1
    env.tx_ready_p = 1.0
    await env.idle(100)
    assert len(env.cpls) == cpls and len(env.rel_np) == rels, "a dropped request was answered"
    # A write cut after 5 of its 16 DWs.
    data = [0xC0DE_0000 + j for j in range(16)]
    t = mem_wr(0x40, data)
    env.queue_rx(t)
    while env.rx_cur is None or env.rx_idx < 3 + 5:
        await env.tick()
    env.drv["dl_up_i"] = 0
    await env.idle(2)
    env.drv["dl_up_i"] = 1
    for j in range(16):
        want = data[j] if j < 5 else 0x100 + 0x10 + j
        assert await host_rd(env, 0x40 + 4 * j) == want, f"cut write DW {j}"
    assert env.m.msi == [(0, 0x99)] and await env.err_rd() == bit(E_COR)
    # A posted release pulsing in the cycle the link goes down is cut at
    # once: dl_up_i falling inside that cycle forces fc_rel_* to 0.
    env.queue_rx(mem_wr(0x80, [1]))
    await quiesce_rx(env)
    assert int(dut.fc_rel_p_o.value) == 1 and int(dut.fc_rel_pd_o.value) == 1
    env.drv["dl_up_i"] = 0
    await env.apply()
    assert int(dut.fc_rel_p_o.value) == 0 and int(dut.fc_rel_pd_o.value) == 0
    await env.tick()
    env.drv["dl_up_i"] = 1
    await env.idle(2)
    # Served again after link-up.
    got = await send(env, mem_rd(0x0, 2, tag=0x42))
    check_cpl(got[0], status=CS_SC, rid=EP_RID, tag=0x42, k=2, bc=8, la=0,
              data=[0x100, 0x101], where="after link-up")


# --------------------------------------------------------------- random

def legal_bes(r, n: int, a: int) -> tuple[int, int]:
    """Byte enables the receive checks accept for n DWs at byte address a."""
    if n == 1:
        return r.getrandbits(4), 0
    if n == 2 and not a & 4 and r.random() < 0.5:
        return r.randrange(1, 16), r.randrange(1, 16)
    return r.choice((0xF, 0xE, 0xC, 0x8)), r.choice((0x1, 0x3, 0x7, 0xF))


def random_tlp(env: Env, r) -> list[int]:
    """A random inbound request or message (DV plan, random_inb)."""
    size = host_size(env)
    base = env.p.host_mem_base
    k = r.random()
    kw = {"tag": r.getrandbits(8), "rid": r.choice((EP_RID, r.getrandbits(16))),
          "td": int(r.random() < 0.15), "ep": int(r.random() < 0.05)}
    hdr4 = r.random() < 0.15
    if k < 0.30:
        n = r.choice((1, 1, 2, 3, 8, 16, 32, r.randrange(1, 33)))
        dw = r.randrange(0, size // 4 - n + 1) if r.random() < 0.95 else r.randrange(size // 4)
        a = base + 4 * dw
        fbe, lbe = legal_bes(r, n, a)
        return mem_wr(a, [r.getrandbits(32) for _ in range(n)], fbe, lbe, hdr4, **kw)
    if k < 0.55:
        n = r.choice((1, 1, 2, 3, 16, 17, 32, 33, 64, r.randrange(1, 65),
                      r.randrange(1, 300) if r.random() < 0.1 else 5))
        n = min(n, size // 4)
        dw = r.randrange(0, size // 4 - n + 1)
        a = base + 4 * dw
        if (a & 0xFFF) + 4 * n > 0x1000:
            a &= ~0xFFF
        fbe, lbe = legal_bes(r, n, a)
        return mem_rd(a, n, fbe, lbe, hdr4, tc=r.getrandbits(3), attr=r.getrandbits(2), **kw)
    if k < 0.65:
        return mem_wr(MSI + 4 * r.randrange(1024), [r.getrandbits(32)], r.getrandbits(4), 0,
                      hdr4, **kw)
    if k < 0.73:
        code = r.choice((0x30, 0x31, 0x33, 0x20, 0x14, 0x7E, 0x7F, r.getrandbits(8)))
        data = [r.getrandbits(32)] if r.random() < 0.3 else None
        return msg(code, r.getrandbits(3), data, tc=r.choice((0, 0, 0, r.getrandbits(3))), **kw)
    if k < 0.83:
        # Unsupported Requests: configuration, I/O, atomic, locked, outside
        # host memory, in the window, a 4-DW address above 4 GiB.
        c = r.randrange(8)
        if c == 0:
            f = r.choice((0b000, 0b010))
            return tlp(f, r.choice((0x04, 0x05, 0x02)),
                       data=[r.getrandbits(32)] if f else None, **kw)
        if c == 1:
            return mem_rd(base + 4 * r.randrange(4), 1, hdr4=True, **kw)
        if c == 2:
            n = r.choice((1, 2, 4))
            return tlp(r.choice((0b010, 0b011)), r.choice((0x0C, 0x0D, 0x0E)),
                       addr=base + 8 * r.randrange(4), length=n,
                       data=[r.getrandbits(32) for _ in range(n)], **kw)
        if c == 3:
            return mem_rd(base + 4 * r.randrange(16), r.randrange(1, 9), locked=True, **kw)
        if c == 4:
            return mem_rd(IN_WIN | (r.getrandbits(18) << 2) & 0xFF000, r.randrange(1, 9), **kw)
        if c == 5:
            return mem_wr(0x8000_0000 | (r.getrandbits(10) << 2), [1], **kw)
        if c == 6:
            return mem_rd((1 << 32) | base, r.randrange(1, 9), hdr4=True, **kw)
        return mem_wr(MSI, [1, 2], **kw)
    if k < 0.92:
        # Malformed: byte enables, 4 KiB, configuration fields, message TC.
        c = r.randrange(5)
        if c == 0:
            return mem_wr(base, [1], 0xF, 0x1, **kw)
        if c == 1:
            return mem_rd(base + 0xFF8, 4, **kw)
        if c == 2:
            return mem_rd(base, 3, 0xF, 0x0, **kw)
        if c == 3:
            return tlp(0b000, 0x04, length=2, **kw)
        return msg(r.choice(sorted(TC0_CODES)), tc=r.randrange(1, 8), **kw)
    # Any Fmt and Type, a consistent length.
    fmt, typ = r.getrandbits(3), r.getrandbits(5)
    n = r.randrange(1, 33)
    data = [r.getrandbits(32) for _ in range(n)] if fmt & 2 else None
    t = tlp(fmt, typ, addr=r.getrandbits(32) & ~3, length=n if data else r.getrandbits(10),
            fbe=r.getrandbits(4), lbe=r.getrandbits(4), data=data, **kw)
    return t


def random_cmd(env: Env, r) -> Cmd:
    k = r.random()
    size = host_size(env)
    if k < 0.45:
        return Cmd(HOST_RD, r.randrange(size // 4) << 2)
    if k < 0.75:
        return Cmd(HOST_WR, r.randrange(size // 4) << 2, first_be=r.getrandbits(4),
                   data=r.getrandbits(32))
    if k < 0.82:
        return Cmd(r.choice((ERR_RD, ERR_CLR, HDR_RD)), 0, data=r.getrandbits(32))
    if k < 0.92:
        return Cmd(r.choice((CFG_RD, MEM_RD, MEM_WR)),
                   (BUS << 20) if r.random() < 0.3 else IN_WIN | (r.getrandbits(10) << 2),
                   first_be=0xF)
    return Cmd(HOST_RD, size | (r.getrandbits(4) << 2))        # refused, check 4


def outage(env: Env, r) -> None:
    name = r.choice(("fc_cplh_avail_i", "fc_cpld_avail_i", "fc_ph_avail_i", "fc_nph_avail_i"))
    env.drv[name] = r.choice((0, 1))
    left = [r.randint(1, 40)]

    def hook(e: Env) -> bool:
        left[0] -= 1
        if left[0] <= 0:
            e.drv[name] = FC_INPUTS[name]
            return False
        return True
    env.hooks.append(hook)


def blip(env: Env, r) -> None:
    wait, length = [r.randint(0, 40)], [r.randint(1, 4)]

    def hook(e: Env) -> bool:
        if wait[0] > 0:
            wait[0] -= 1
            return True
        e.drv["dl_up_i"] = 0
        length[0] -= 1
        if length[0] < 0:
            e.drv["dl_up_i"] = 1
            return False
        return True
    env.hooks.append(hook)


async def run_random(dut, tlps: int) -> None:
    env = await setup(dut, answer_delay=(0, 8))
    r = env.rng
    await fill_host(env)
    env.toggle_idle = True
    sent = 0
    while sent < tlps:
        if r.random() < 0.004:
            env.hooks = []
            env.drv |= FC_INPUTS | {"dl_up_i": 1}
            await env.reset(r.randrange(1, 4))
            await open_routes(env)
        if r.random() < 0.03:
            await open_routes(env, bme=int(r.random() < 0.7))
        if r.random() < 0.05:
            env.tx_ready_p = r.choice((1.0, 1.0, 0.7, 0.3))
            env.rx_gap_p = r.choice((0.0, 0.0, 0.2))
        if r.random() < 0.03:
            outage(env, r)
        if r.random() < 0.01:
            blip(env, r)
        for _ in range(r.randrange(1, 6)):
            env.queue_rx(random_tlp(env, r), idle=r.choice((0, 0, 0, 1, 4)))
            sent += 1
        for _ in range(r.randrange(0, 3)):
            await env.command(random_cmd(env, r))
        await env.idle(r.randrange(0, 12))
    env.hooks = []
    env.drv |= FC_INPUTS | {"dl_up_i": 1}
    env.tx_ready_p = 1.0
    await env.drain(200)
    env.dut._log.info(f"{env.cycle} cycles, {sent} TLPs sent, {len(env.cpls)} completions, "
                      f"{len(env.msi_taken)} MSIs taken, {len(env.rel_p)} posted and "
                      f"{len(env.rel_np)} non-posted releases, {len(env.results)} commands")
    assert env.cpls and env.msi_taken and env.rel_p and env.rel_np, "a path never exercised"


@cocotb.test()
async def random_inb(dut):
    """L1: random inbound traffic of every kind with HOST_* and endpoint
    commands, link stalls, credit outages, Bus Master Enable changes, link
    drops and resets, against the model."""
    await run_random(dut, 700)


@cocotb.test()
async def random_inb_long(dut):
    """L2: as random_inb, longer."""
    await run_random(dut, 12000)
