"""pcie_rc_tl front end: the take, checks 1 to 7, byte enables, outbound
routing, ERR_*, the interim rule for HOST_* and the result beats
(wiki/src/rtl/rc_tl/MAS.md#commands, REQUESTER.md#byte-enables).

Check 1 refuses every endpoint command with LINK_DOWN while dl_up_i is 0,
so the tests offer endpoint commands with dl_up_i 1 and tell a pass from a
refusal by the result: an endpoint command that passes goes to the
requester, is sent, and the endpoint (tb/rc_tl_ep.py) completes it
Successfully, so it finishes OK; a refusal finishes LOCAL_UR at once and
sends nothing. HDR_* and ERR_* pass with OK; HOST_* finishes LOCAL_UR
either way until #375 serves it (MAS, Leaf blocks).

Each test states its expected statuses from the specs itself, besides the
environment's per-cycle check against the reference model.
"""

import cocotb
from cocotb.triggers import FallingEdge, Timer

from tb.rc_tl_env import Env
from pcie_rc_tl_model import (CFG_RD, CFG_WR, ERR_CLR, ERR_RD, HDR_RD, HDR_WR, HOST_RD,
                              HOST_WR, LINK_DOWN, LOCAL_UR, MEM_RD, MEM_WR, OK, Cmd,
                              Rsp, be_legal)

PASS = OK                # an endpoint command past the checks, completed
REFUSED = LOCAL_UR

BUS, BASE, LIMIT = 0x05, 0x100, 0x1FF    # Secondary Bus Number, window [31:20]
IN_WIN = BASE << 20


def seed() -> int:
    return int(cocotb.RANDOM_SEED or 0)


async def open_routes(env: Env) -> None:
    """Memory Space Enable 1, Secondary Bus Number BUS, window BASE to LIMIT."""
    await env.hdr_wr(1, 0x0000_0002)
    await env.hdr_wr(6, (0x07 << 16) | (BUS << 8) | 0x00)
    await env.hdr_wr(8, (LIMIT << 20) | (BASE << 4))


def cfg_addr(bus: int = BUS, dev: int = 0, fn: int = 0, reg: int = 0) -> int:
    return (bus << 20) | (dev << 15) | (fn << 12) | (reg << 2)


@cocotb.test()
async def cmd_checks(dut):
    """Checks 2 to 5, each with refusals and the passing neighbours."""
    env = Env(dut, seed())
    await env.start()
    await open_routes(env)
    # Check 2: op 10 to 15 refused, with the link down and up.
    for op in range(10, 16):
        await env.expect_cmd(Cmd(op), REFUSED, dl_up=0)
        await env.expect_cmd(Cmd(op), REFUSED, dl_up=1)
    sent = len(env.sent)
    # Check 3: len.
    for n in (0, 33, 34, 48, 63):
        await env.expect_cmd(Cmd(MEM_RD, IN_WIN, n, 0xF, 0xF), REFUSED)
    for n in (1, 2, 31, 32):
        await env.expect_cmd(Cmd(MEM_RD, IN_WIN, n, 0xF, 0xF if n > 1 else 0), PASS)
    for op, a in ((CFG_RD, cfg_addr()), (CFG_WR, cfg_addr()), (MEM_WR, IN_WIN)):
        for n in (0, 2, 32, 63):
            await env.expect_cmd(Cmd(op, a, n, 0xF, 0xF), REFUSED)
        await env.expect_cmd(Cmd(op, a, 1, 0xF), PASS)
    for op in (HDR_RD, HDR_WR, ERR_RD, ERR_CLR):
        for n in (0, 2, 63):
            await env.expect_cmd(Cmd(op, 0x30, n, 0xF, 0xF), REFUSED)
    await env.expect_cmd(Cmd(HDR_RD, 0x30, 1), OK)
    # Every refusal so far sent nothing; each pass sent one TLP.
    assert len(env.sent) == sent + 4 + 3, env.last_sent()
    # Check 4: every address bit outside the op's fields, one at a time
    # (flipped, so a field bit changes its field).
    for bit in range(32):
        a = cfg_addr() ^ (1 << bit)
        outside = bit >= 28 or bit < 2
        # Bits 27:15 select bus and device: check 7 refuses those.
        want = REFUSED if outside or 15 <= bit <= 27 else PASS
        await env.expect_cmd(Cmd(CFG_RD, a), want)
        await env.expect_cmd(Cmd(CFG_WR, a), want)
        a = IN_WIN ^ (1 << bit)
        want = REFUSED if bit < 2 or not BASE <= a >> 20 <= LIMIT else PASS
        await env.expect_cmd(Cmd(MEM_WR, a), want)
        await env.expect_cmd(Cmd(MEM_RD, a), want)
        a = 1 << bit
        want = REFUSED if bit < 2 or bit >= 12 else OK
        await env.expect_cmd(Cmd(HDR_RD, a), want, hdr_data(env, a, want))
    # Check 5: a MEM_RD inside one aligned 4 KiB block.
    for n in (1, 2, 7, 31, 32):
        last = 0x1000 - 4 * n
        await env.expect_cmd(Cmd(MEM_RD, IN_WIN | last, n, 0xF, 0xF if n > 1 else 0), PASS)
        if n > 1:
            # One DW further crosses into the next block.
            await env.expect_cmd(Cmd(MEM_RD, IN_WIN | (last + 4), n, 0xF, 0xF), REFUSED)
    await env.expect_cmd(Cmd(MEM_RD, IN_WIN | 0xFFC, 32, 0xF, 0xF), REFUSED)
    # Check 4: ERR_* has no address field to check.
    for a in (0, 3, 0x8000_0001, 0xFFFF_FFFF):
        await env.expect_cmd(Cmd(ERR_RD, a), OK, 0)
        await env.expect_cmd(Cmd(ERR_CLR, a), OK, 0)


def hdr_data(env: Env, addr: int, status: int) -> int:
    """The data an HDR_RD of addr returns: the header DW when it passes."""
    return env.fe.hdr.read((addr >> 2) & 0x3FF) if status == OK else 0


@cocotb.test()
async def byte_enables(dut):
    """Check 6, every first_be and last_be of every op with len 1 (ERR_*
    included), and of MEM_RD with len 2 (both QW alignments), 3 and 32."""
    env = Env(dut, seed())
    await env.start()
    await open_routes(env)
    addr = {CFG_RD: cfg_addr(), CFG_WR: cfg_addr(), MEM_RD: IN_WIN, MEM_WR: IN_WIN,
            HDR_RD: 0x2C, HDR_WR: 0x2C, HOST_RD: 0, HOST_WR: 0, ERR_RD: 0, ERR_CLR: 0}
    pass_status = {CFG_RD: PASS, CFG_WR: PASS, MEM_RD: PASS, MEM_WR: PASS,
                   HDR_RD: OK, HDR_WR: OK, HOST_RD: REFUSED, HOST_WR: REFUSED,
                   ERR_RD: OK, ERR_CLR: OK}
    # The REQUESTER.md table, len 1: last_be 0; first_be not 0 for CFG_*
    # and MEM_WR; any first_be otherwise.
    for op, a in addr.items():
        for fbe in range(16):
            for lbe in range(16):
                legal = lbe == 0 and (fbe != 0 or op not in (CFG_RD, CFG_WR, MEM_WR))
                assert legal == be_legal(Cmd(op, a, 1, fbe, lbe))
                await env.expect_cmd(Cmd(op, a, 1, fbe, lbe),
                                     pass_status[op] if legal else REFUSED)
    # MEM_RD, len 2 or more: both enables non-zero; contiguous unless len 2
    # and addr[2] 0.
    contig_f, contig_l = (0xF, 0xE, 0xC, 0x8), (0x1, 0x3, 0x7, 0xF)
    for n, a in ((2, IN_WIN), (2, IN_WIN | 4), (3, IN_WIN), (32, IN_WIN)):
        for fbe in range(16):
            for lbe in range(16):
                nonzero = fbe != 0 and lbe != 0
                legal = nonzero and ((n == 2 and not a & 4)
                                     or (fbe in contig_f and lbe in contig_l))
                assert legal == be_legal(Cmd(MEM_RD, a, n, fbe, lbe))
                await env.expect_cmd(Cmd(MEM_RD, a, n, fbe, lbe),
                                     PASS if legal else REFUSED)


@cocotb.test()
async def routing(dut):
    """Check 7 by the header state: Secondary Bus Number and device 0 for
    CFG_*; Memory Space Enable and the window edges for MEM_*; an HDR_WR
    routes the very next command."""
    env = Env(dut, seed())
    await env.start()
    # Out of reset: Secondary Bus Number 0, Memory Space Enable 0, window
    # 0 to 1 MiB.
    await env.expect_cmd(Cmd(CFG_RD, cfg_addr(bus=0)), PASS)
    await env.expect_cmd(Cmd(CFG_RD, cfg_addr(bus=1)), REFUSED)
    await env.expect_cmd(Cmd(MEM_WR, 0x0000_0000), REFUSED)      # MSE 0
    await env.hdr_wr(1, 0x2)
    await env.expect_cmd(Cmd(MEM_WR, 0x0000_0000), PASS)    # the very next command
    await env.expect_cmd(Cmd(MEM_WR, 0x000F_FFFC), PASS)
    await env.expect_cmd(Cmd(MEM_WR, 0x0010_0000), REFUSED)
    await env.hdr_wr(1, 0x4)                                     # BME only: MSE back to 0
    await env.expect_cmd(Cmd(MEM_RD, 0x0000_0000), REFUSED)
    await env.hdr_wr(1, 0x2)
    # Configuration: every Secondary Bus Number bit, device and function.
    for bus in (0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80, 0xFF, 0x5A):
        await env.hdr_wr(6, (bus << 8) | 0x11, be=0x2)
        for b in (bus, bus ^ 0x01, bus ^ 0x80, (bus + 1) & 0xFF):
            await env.expect_cmd(Cmd(CFG_WR, cfg_addr(bus=b)),
                                 PASS if b == bus else REFUSED)
        for dev in (1, 2, 4, 8, 16, 31):
            await env.expect_cmd(Cmd(CFG_RD, cfg_addr(bus=bus, dev=dev)), REFUSED)
        for fn in range(8):
            await env.expect_cmd(Cmd(CFG_RD, cfg_addr(bus=bus, fn=fn, reg=0x3FF)), PASS)
    # Primary and Subordinate Bus Numbers do not route.
    await env.hdr_wr(6, 0x00FF_10EE)
    await env.expect_cmd(Cmd(CFG_RD, cfg_addr(bus=0xEE)), REFUSED)
    await env.expect_cmd(Cmd(CFG_RD, cfg_addr(bus=0xFF)), REFUSED)
    await env.expect_cmd(Cmd(CFG_RD, cfg_addr(bus=0x10)), PASS)
    # Memory window edges, for several windows, an empty one included.
    for base, limit in ((0x100, 0x1FF), (0x123, 0x123), (0x000, 0xFFF), (0xFFF, 0xFFF),
                        (0x800, 0x7FF), (0x456, 0x455), (0x001, 0xFFE)):
        await env.hdr_wr(8, (limit << 20) | (base << 4))
        for mib in {base - 1, base, base + 1, limit - 1, limit, limit + 1}:
            if not 0 <= mib <= 0xFFF:
                continue
            inside = base <= mib <= limit
            for low in (0x00000, 0xFFFFC):
                a = (mib << 20) | low
                await env.expect_cmd(Cmd(MEM_WR, a), PASS if inside else REFUSED)
                n = 1 if low else 32
                await env.expect_cmd(Cmd(MEM_RD, a, n, 0xF, 0xF if n > 1 else 0),
                                     PASS if inside else REFUSED)


@cocotb.test()
async def check1(dut):
    """Check 1: an endpoint command taken while dl_up_i is 0 finishes
    LINK_DOWN at once, whichever of checks 2 to 7 it would fail, and sends
    nothing; with the link up the same commands pass or finish LOCAL_UR.
    Local commands are unaffected by the link; HOST_* finishes LOCAL_UR
    (the interim rule until #375). dl_up_i is sampled at the take."""
    env = Env(dut, seed())
    await env.start()
    await open_routes(env)
    env.toggle_idle = True
    good = (Cmd(CFG_RD, cfg_addr()), Cmd(CFG_WR, cfg_addr(), data=0x1234),
            Cmd(MEM_RD, IN_WIN, 32, 0xF, 0xF), Cmd(MEM_WR, IN_WIN, data=0xFFFF_FFFF))
    # One endpoint command failing each of checks 3 to 7.
    bad = (Cmd(MEM_WR, IN_WIN, 2, 0xF, 0xF),                    # 3
           Cmd(CFG_RD, cfg_addr() | (1 << 28)),                 # 4
           Cmd(MEM_RD, IN_WIN | 0xF84, 32, 0xF, 0xF),           # 5
           Cmd(CFG_WR, cfg_addr(), first_be=0),                 # 6
           Cmd(MEM_WR, (LIMIT + 1) << 20),                      # 7
           Cmd(CFG_RD, cfg_addr(bus=BUS + 1)))                  # 7
    local = ((Cmd(HDR_RD, 0x8), OK), (Cmd(HDR_WR, 0x0C, data=0x40), OK), (Cmd(ERR_RD), OK),
             (Cmd(ERR_CLR, data=0xFFFF_FFFF), OK), (Cmd(HOST_RD, 0x10), LOCAL_UR),
             (Cmd(HOST_WR, 0x10, data=0xA5), LOCAL_UR))
    for dl_up in (1, 0, 1, 0):
        sent = len(env.sent)
        for c in good:
            await env.expect_cmd(c, OK if dl_up else LINK_DOWN, dl_up=dl_up)
        assert len(env.sent) == sent + (4 if dl_up else 0), env.last_sent()
        for c in bad:
            await env.expect_cmd(c, LOCAL_UR if dl_up else LINK_DOWN, dl_up=dl_up)
        for c, status in local:
            await env.expect_cmd(c, status, dl_up=dl_up)
        assert len(env.sent) == sent + (4 if dl_up else 0), env.last_sent()
    # dl_up_i is sampled at the take: changed every cycle while offered
    # (takes land on both values).
    seen = set()
    for i in range(18):
        env.drv["cmd_valid_i"] = 1
        env.drv["cmd_i"] = bad[i % len(bad)].pack()
        up = int(i % 3 != 0)
        env.drv["dl_up_i"] = up
        take, beat = await env.tick()
        if take:
            assert beat == Rsp(LOCAL_UR if up else LINK_DOWN)
            seen.add(up)
    assert seen == {0, 1}
    env.drv["cmd_valid_i"] = 0
    env.drv["dl_up_i"] = 1
    await env.idle(4)


@cocotb.test()
async def result_beats(dut):
    """One beat per command, in the cycle after the take, last 1; ready 0
    from the take to the beat and 1 in the cycle after; rsp_o 0 between
    beats; rst_i holds cmd_ready_o, rsp_valid_o and rsp_o at 0 at once."""
    env = Env(dut, seed())
    d = dut
    await env.start()
    # Ready in the first cycle out of reset.
    await env.apply()
    assert int(d.cmd_ready_o.value) == 1
    # A command held valid: taken every second cycle.
    env.drv["cmd_valid_i"] = 1
    env.drv["cmd_i"] = Cmd(HDR_RD, 0x8).pack()
    trace = []
    for _ in range(12):
        take, beat = await env.tick()
        trace.append((take, beat is not None, int(d.cmd_ready_o.value)))
    # Each take edge puts out the beat; the next edge is the beat's (ready
    # 0); ready is 1 again after it.
    assert trace == [(True, True, 0), (False, False, 1)] * 6, trace
    want = Rsp(OK, 0x0604_0000)
    assert all(b == want for _, _, b in env.takes[-6:])
    env.drv["cmd_valid_i"] = 0
    await env.idle(3)
    # Back-to-back different commands keep the order and pairing.
    for c, status in ((Cmd(HDR_WR, 0x18, data=0x0003_0201), OK), (Cmd(HDR_RD, 0x18), OK),
                      (Cmd(15), LOCAL_UR), (Cmd(HDR_RD, 0x0C), OK)):
        beats = await env.command(c)
        assert len(beats) == 1 and beats[0].status == status, f"{c}: {beats}"
    assert env.takes[-3][2].data == 0x0003_0201
    assert env.takes[-1][2].data == 0x0001_0000
    # rst_i rising in the beat cycle silences the beat at once.
    await env.idle(1)
    env.drv["cmd_valid_i"] = 1
    env.drv["cmd_i"] = Cmd(HDR_RD, 0x8).pack()
    take, beat = await env.tick()
    assert take and beat is not None
    env.drv["cmd_valid_i"] = 0
    d.rst_i.value = 1
    d.cmd_valid_i.value = 0
    await Timer(1, unit="ns")
    for name in ("cmd_ready_o", "rsp_valid_o", "rsp_o"):
        assert int(getattr(d, name).value) == 0, f"{name} not 0 with rst_i 1 in the beat cycle"
    await env.reset(3)
    await env.apply()
    assert int(d.cmd_ready_o.value) == 1
    # A command offered in reset is not taken; its first take is after it.
    env.drv["rst_i"] = 1
    env.drv["cmd_valid_i"] = 1
    for _ in range(3):
        take, _ = await env.tick()
        assert not take
    env.drv["rst_i"] = 0
    take, beat = await env.tick()
    assert take and beat == Rsp(OK, 0x0604_0000)
    env.drv["cmd_valid_i"] = 0
    await env.idle(2)
    await FallingEdge(d.clk_i)


# --------------------------------------------------------------- random

def random_cmd(env: Env) -> Cmd:
    """A command biased to the check edges: mostly legal fields, one field
    disturbed at a time, addresses near the routing edges."""
    r = env.rng
    h = env.fe.hdr
    op = r.choice((CFG_RD, CFG_WR, MEM_RD, MEM_WR, HDR_RD, HDR_WR, HDR_WR, HOST_RD, HOST_WR,
                   ERR_RD, ERR_CLR, r.randrange(10, 16)))
    n = r.choice((1, 1, 1, 2, 3, 32, r.randrange(1, 33))) if op == MEM_RD else 1
    if op in (CFG_RD, CFG_WR):
        bus = h.sec_bus if r.random() < 0.8 else r.getrandbits(8)
        dev = 0 if r.random() < 0.8 else r.getrandbits(5)
        addr = cfg_addr(bus, dev, r.getrandbits(3), r.getrandbits(10))
    elif op in (MEM_RD, MEM_WR):
        mib = r.choice((h.mem_base, h.mem_limit, h.mem_base - 1, h.mem_limit + 1,
                        r.getrandbits(12))) & 0xFFF
        dw = r.choice((0, 1023 - n + 1, 1023 - n + 2, r.getrandbits(10))) & 0x3FF
        addr = (mib << 20) | r.getrandbits(8) << 12 | dw << 2
    elif op in (HDR_RD, HDR_WR):
        addr = r.choice((0, 1, 2, 3, 6, 8, r.getrandbits(4), r.getrandbits(10))) << 2
    else:
        addr = r.getrandbits(env.p.host_mem_addr_w) & ~3
    fbe, lbe = r.getrandbits(4), 0
    if n > 1:
        fbe = r.choice((0xF, 0xE, 0xC, 0x8, r.getrandbits(4)))
        lbe = r.choice((0x1, 0x3, 0x7, 0xF, r.getrandbits(4)))
    c = Cmd(op, addr, n, fbe, lbe, r.getrandbits(32))
    # Disturb one field now and then.
    k = r.randrange(12)
    if k == 0:
        c = Cmd(op, c.addr ^ (1 << r.randrange(32)), n, fbe, lbe, c.data)
    elif k == 1:
        c = Cmd(op, addr, r.getrandbits(6), fbe, lbe, c.data)
    elif k == 2:
        c = Cmd(op, addr, n, fbe, r.getrandbits(4), c.data)
    return c


async def run_random(dut, cmds: int) -> None:
    env = Env(dut, seed())
    await env.start()
    r = env.rng
    env.toggle_idle = True
    await open_routes(env)
    done = 0
    while done < cmds:
        if r.random() < 0.004:
            await env.reset(r.randrange(1, 4))
            await open_routes(env)
        env.drv["dl_up_i"] = int(r.random() < 0.8)
        for _ in range(r.choice((0, 0, 0, 1, 3))):
            await env.tick()
        await env.command(random_cmd(env))
        done += 1
    env.drv["cmd_valid_i"] = 0
    await env.idle(4)
    statuses = {beats[-1].status for _, beats in env.results}
    assert {OK, LOCAL_UR, LINK_DOWN} <= statuses, statuses


@cocotb.test()
async def random_cmds(dut):
    """L1: 3000 random commands against the model, with resets."""
    await run_random(dut, 3000)


@cocotb.test()
async def random_long(dut):
    """L2: 30000 random commands against the model, with resets."""
    await run_random(dut, 30000)
