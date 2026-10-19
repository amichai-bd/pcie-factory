"""pcie_rc_tl requester: requests, tags, completion matching and checks,
split completions, the completion timeout, transmit and credits, link down
and the error record (wiki/src/rtl/rc_tl/REQUESTER.md, MAS.md#commands,
MAS.md#reset-and-link-down, MAS.md#error-record-and-q2-cases).

The endpoint (tb/rc_tl_ep.py) answers each request; a test replaces its
answer with a plan to inject any completion. Each test states its expected
results, request fields and error record bits from the specs itself,
besides the environment's per-cycle check against the reference model.
"""

import cocotb

from tb.rc_tl_env import FC_INPUTS, Env
from tb.rc_tl_ep import CFG_RD_T, CFG_WR_T, MEM_RD_T, MEM_WR_T, Req
from pcie_rc_tl_model import (BAD, CA, CFG_RD, CFG_WR, CRS, CS_CA, CS_CRS, CS_SC, CS_UR,
                              E_CA, E_CRS, E_MAL, E_POIS, E_TMO, E_UNEXP, E_UR, ERR_CLR,
                              ERR_RD, LINK_DOWN, LOCAL_UR, MEM_RD, MEM_WR, OK, TIMEOUT, UR,
                              Cmd, Rsp, cpl_tlp, read_bytes, read_completions, request_tlp)

BUS, PRI, BASE, LIMIT = 0x05, 0x2A, 0x100, 0x1FF   # Secondary, Primary Bus Numbers, window
RID = PRI << 8                                     # the Requester ID {PRI, 0, 0}
IN_WIN = BASE << 20


def seed() -> int:
    return int(cocotb.RANDOM_SEED or 0)


def cfg_addr(bus: int = BUS, dev: int = 0, fn: int = 0, reg: int = 0) -> int:
    return (bus << 20) | (dev << 15) | (fn << 12) | (reg << 2)


async def setup(dut, **kw) -> Env:
    """Out of reset, Memory Space Enable 1, the bus numbers and the window."""
    env = Env(dut, seed())
    for k, v in kw.items():
        setattr(env, k, v)
    await env.start()
    await env.hdr_wr(1, 0x0000_0006)
    await env.hdr_wr(6, (0x07 << 16) | (BUS << 8) | PRI)
    await env.hdr_wr(8, (LIMIT << 20) | (BASE << 4))
    return env


def answer(*tlps):
    """A plan: answer the next request with these TLPs, each a function of
    the decoded request."""
    return lambda r: [t(r) for t in tlps]


def bit(*bits: int) -> int:
    return sum(1 << b for b in bits)


async def expect_err(env: Env, want: int, where: str) -> None:
    """The error record holds exactly `want`; then clear it."""
    got = await env.err_rd()
    assert got == want, f"{where}: error record expected 0x{want:04x} got 0x{got:04x}"
    await env.err_clr()


def mem_data(env: Env, c: Cmd) -> list[int]:
    return [env.ep.mem_dw((c.addr >> 2) + i) for i in range(c.len)]


# --------------------------------------------------------------- success

@cocotb.test()
async def req_success(dut):
    """Every endpoint command completes OK: the TLP fields of REQUESTER.md
    (Fmt and Type, Length, byte enables, Requester ID, rolling tag,
    address), the data in address order, the request on tlp_tx_* from the
    third cycle after the take, MEM_WR's beat in the cycle the link takes
    its eop, and a data beat in the cycle its DW arrives."""
    env = await setup(dut)
    tag = 0

    def check_req(r: Req, kind: int, length: int, fbe: int, lbe: int, addr: int,
                  t: int | None) -> None:
        assert (r.kind, r.rid, r.length, r.fbe, r.lbe, r.addr) == (kind, RID, length, fbe,
                                                                    lbe, addr), r
        assert t is None or r.tag == t, f"tag expected {t} got {r.tag}"
        assert r.dws[0] >> 8 & 0xFFFF == 0, f"TC, TD, EP, Attr or AT not 0: {r}"

    # Timing: the take at edge E, the sop offered after edge E+2.
    c = Cmd(CFG_RD, cfg_addr(reg=0x3F))
    await env.offer(c)
    for k in range(2):
        await env.tick()
        assert int(dut.tlp_tx_valid_o.value) == (1 if k == 1 else 0), f"edge E+{k + 1}"
    while not env.results or env.results[-1][0] != c:
        await env.tick()
    assert env.results[-1][1] == [Rsp(OK, env.ep.cfg_dw(c.addr))]
    assert env.sent[-1] == request_tlp(c, PRI, 0)
    check_req(env.ep.reqs[-1], CFG_RD_T, 1, 0xF, 0, c.addr, 0)
    tag = 1
    # Configuration reads and writes: each function, register, byte enables.
    for fn, reg, be in ((0, 0, 0xF), (7, 0x3FF, 0x1), (3, 0x155, 0x6), (5, 0x2AA, 0x8)):
        a = cfg_addr(fn=fn, reg=reg)
        env.ep.cfg[a >> 2] = 0xA000_0000 | reg
        await env.expect_cmd(Cmd(CFG_RD, a, first_be=be), OK, 0xA000_0000 | reg)
        check_req(env.ep.reqs[-1], CFG_RD_T, 1, be, 0, a, tag)
        tag += 1
        await env.expect_cmd(Cmd(CFG_WR, a, first_be=be, data=0x5A5A_0000 | reg), OK, 0)
        r = env.ep.reqs[-1]
        check_req(r, CFG_WR_T, 1, be, 0, a, tag)
        assert r.data == 0x5A5A_0000 | reg and len(r.dws) == 4
        tag += 1
    # Memory writes: Tag 0, the tag does not step; OK as the link takes the eop.
    for a, be, d in ((IN_WIN, 0xF, 0x1234_5678), ((LIMIT << 20) | 0xFFFFC, 0x9, 0xCAFE_BABE)):
        beats = await env.expect_cmd(Cmd(MEM_WR, a, first_be=be, data=d), OK, 0)
        r = env.ep.reqs[-1]
        check_req(r, MEM_WR_T, 1, be, 0, a, 0)
        assert r.data == d and len(r.dws) == 4
        assert env.beat_cycles[-1] == env.tx_eop_cycles[-1], "MEM_WR beat not at the eop"
        assert len(beats) == 1
    # Memory reads: len 1 to 32, alignments and legal byte enables.
    for n, off, fbe, lbe in ((1, 0, 0xF, 0), (1, 4, 0x0, 0), (1, 8, 0x6, 0), (2, 0, 0x1, 0x8),
                             (2, 4, 0xE, 0x3), (3, 0xC, 0x8, 0x1), (7, 0x24, 0xC, 0x7),
                             (16, 0x40, 0xF, 0xF), (31, 0x84, 0xE, 0x7), (32, 0, 0xF, 0xF),
                             (32, 0xF80, 0xF, 0x1)):
        c = Cmd(MEM_RD, IN_WIN | off, n, fbe, lbe)
        beats = await env.expect_cmd(c, OK)
        assert [b.data for b in beats] == mem_data(env, c), f"{c}: data"
        check_req(env.ep.reqs[-1], MEM_RD_T, n, fbe, lbe, c.addr, tag)
        tag += 1
        # Each beat in the cycle its DW arrived: the last at the eop.
        assert env.beat_cycles[-1] == env.rx_hdr_cycles[-1] + n
    # The tag rolls over after 32 non-posted requests (5 bits).
    for _ in range(36):
        await env.expect_cmd(Cmd(CFG_RD, cfg_addr()), OK)
        assert env.ep.reqs[-1].tag == tag % 32
        tag += 1
    # The Requester ID follows the Primary Bus Number.
    await env.hdr_wr(6, (0x07 << 16) | (BUS << 8) | 0xC3)
    await env.expect_cmd(Cmd(MEM_RD, IN_WIN), OK)
    assert env.ep.reqs[-1].rid == 0xC300
    # With the link stalling and the receive stream gapped.
    env.tx_ready_p, env.rx_gap_p, env.answer_delay = 0.4, 0.3, (0, 9)
    for k in range(24):
        op = (CFG_RD, CFG_WR, MEM_RD, MEM_WR)[k % 4]
        c = Cmd(op, cfg_addr(reg=k) if op < MEM_RD else IN_WIN | (k * 0x84 & 0xF80),
                32 if op == MEM_RD else 1, 0xF, 0xF if op == MEM_RD else 0, k * 0x1111)
        beats = await env.expect_cmd(c, OK)
        if op == MEM_RD:
            assert [b.data for b in beats] == mem_data(env, c)
    await env.drain()
    assert await env.err_rd() == 0


# --------------------------------------------------------------- statuses

@cocotb.test()
async def req_status(dut):
    """The completion status rows: UR, CA, CRS (configuration) and CRS on a
    memory read (BAD), reserved statuses (BAD), poisoned (BAD); each ends
    the request with one beat in the cycle after the completion's eop, data
    0, and sets its error record bit. Status rows come before poisoned and
    malformed."""
    env = await setup(dut, rx_gap_p=0.2, answer_delay=(0, 5))
    rd4 = Cmd(MEM_RD, IN_WIN | 0x10, 4, 0xF, 0xF)
    cases = []
    for c in (Cmd(CFG_RD, cfg_addr()), Cmd(CFG_WR, cfg_addr(), data=7), rd4):
        cfg = c.op != MEM_RD
        cases += [
            (c, CS_UR, {}, UR, bit(E_UR)),
            (c, CS_CA, {}, CA, bit(E_CA)),
            (c, CS_CRS, {}, CRS if cfg else BAD, bit(E_CRS if cfg else E_MAL)),
            (c, CS_UR, {"ep": 1}, UR, bit(E_UR)),
            (c, CS_CA, {"td": 1}, CA, bit(E_CA)),
            (c, CS_CRS, {"td": 1}, CRS if cfg else BAD, bit(E_CRS if cfg else E_MAL)),
        ]
        cases += [(c, st, {}, BAD, bit(E_MAL)) for st in (3, 5, 6, 7)]
    for c, st, kw, want, bits in cases:
        env.ep.plan(answer(lambda r, st=st, kw=kw: cpl_tlp(st, r.rid, r.tag, **kw)))
        beats = await env.expect_cmd(c, want, 0)
        assert len(beats) == 1 and env.beat_cycles[-1] == env.rx_eop_cycles[-1]
        await expect_err(env, bits, f"{c} status {st} {kw}")
    # A configuration write's Successful completion with a digest: OK.
    env.ep.plan(answer(lambda r: cpl_tlp(CS_SC, r.rid, r.tag, td=1)))
    await env.expect_cmd(Cmd(CFG_WR, cfg_addr(), data=9), OK, 0)
    assert env.beat_cycles[-1] == env.rx_eop_cycles[-1]
    # A data-carrying UR completion is still UR.
    env.ep.plan(answer(lambda r: cpl_tlp(CS_UR, r.rid, r.tag, [1, 2, 3, 4], bc=16)))
    await env.expect_cmd(rd4, UR, 0)
    await expect_err(env, bit(E_UR), "UR with data")
    # Poisoned Successful completions: BAD, bit 5, no data delivered.
    for c in (Cmd(CFG_RD, cfg_addr()), rd4):
        env.ep.plan(lambda r: read_completions(r.as_cmd(), r.rid, r.tag, [9] * r.length, ep=1)
                    if r.kind == MEM_RD_T else [cpl_tlp(CS_SC, r.rid, r.tag, [9], ep=1)])
        beats = await env.expect_cmd(c, BAD, 0)
        assert len(beats) == 1
        await expect_err(env, bit(E_POIS), f"{c} poisoned")
    env.ep.plan(answer(lambda r: cpl_tlp(CS_SC, r.rid, r.tag, ep=1)))
    await env.expect_cmd(Cmd(CFG_WR, cfg_addr()), BAD, 0)
    await expect_err(env, bit(E_POIS), "CFG_WR poisoned")
    await env.drain()


# --------------------------------------------------------------- malformed

@cocotb.test()
async def req_malformed(dut):
    """Every malformed row of the completion checks ends the request BAD
    with bit 6 (malformed outranks poisoned), and the legal neighbours
    pass."""
    env = await setup(dut)
    cfg_rd, cfg_wr = Cmd(CFG_RD, cfg_addr()), Cmd(CFG_WR, cfg_addr(), data=3)
    rd4 = Cmd(MEM_RD, IN_WIN | 0x10, 4, 0xF, 0xF)          # bytes 0x10 .. 0x1F
    rd_be = Cmd(MEM_RD, IN_WIN | 0x14, 3, 0xC, 0x3)        # bytes 0x16 .. 0x1D
    mal = [
        (cfg_rd, lambda r: cpl_tlp(CS_SC, r.rid, r.tag)),                      # no data
        (cfg_rd, lambda r: cpl_tlp(CS_SC, r.rid, r.tag, [1, 2])),              # Length 2
        (cfg_wr, lambda r: cpl_tlp(CS_SC, r.rid, r.tag, [1])),                 # with data
        (rd4, lambda r: cpl_tlp(CS_SC, r.rid, r.tag, bc=16, la=0x10)),         # no data
        (rd4, lambda r: cpl_tlp(CS_SC, r.rid, r.tag, [0] * 4, bc=15, la=0x10)),
        (rd4, lambda r: cpl_tlp(CS_SC, r.rid, r.tag, [0] * 4, bc=17, la=0x10)),
        (rd4, lambda r: cpl_tlp(CS_SC, r.rid, r.tag, [0] * 4, bc=0, la=0x10)),  # 4096
        (rd4, lambda r: cpl_tlp(CS_SC, r.rid, r.tag, [0] * 4, bc=0x110, la=0x10)),
        (rd4, lambda r: cpl_tlp(CS_SC, r.rid, r.tag, [0] * 4, bc=0x810, la=0x10)),
        (rd4, lambda r: cpl_tlp(CS_SC, r.rid, r.tag, [0] * 4, bc=16, la=0x11)),
        (rd4, lambda r: cpl_tlp(CS_SC, r.rid, r.tag, [0] * 4, bc=16, la=0x50)),
        (rd4, lambda r: cpl_tlp(CS_SC, r.rid, r.tag, [0] * 5, bc=16, la=0x10)),  # past
        (rd4, lambda r: cpl_tlp(CS_SC, r.rid, r.tag, [0] * 3, bc=16, la=0x10)),  # short
        (rd4, lambda r: cpl_tlp(CS_SC, r.rid, r.tag, [0] * 2, bc=16, la=0x10)),
        (rd_be, lambda r: cpl_tlp(CS_SC, r.rid, r.tag, [0] * 3, bc=8, la=0x14)),
        (rd_be, lambda r: cpl_tlp(CS_SC, r.rid, r.tag, [0] * 3, bc=9, la=0x16)),
        (rd_be, lambda r: cpl_tlp(CS_SC, r.rid, r.tag, [0] * 4, bc=8, la=0x16)),
    ]
    for k, (c, cpl) in enumerate(mal):
        for ep in (0, 1):
            def plan(r, cpl=cpl, ep=ep):
                t = cpl(r)
                t[0] |= ep << 22                     # EP: malformed still decides
                return [t]
            env.ep.plan(plan)
            beats = await env.expect_cmd(c, BAD, 0)
            assert len(beats) == 1
            await expect_err(env, bit(E_MAL), f"malformed case {k} ep {ep}")
    # The legal neighbours: the right Byte Count and Lower Address.
    env.ep.plan(answer(lambda r: cpl_tlp(CS_SC, r.rid, r.tag, [1, 2, 3], bc=8, la=0x16)))
    beats = await env.expect_cmd(rd_be, OK)
    assert [b.data for b in beats] == [1, 2, 3]
    # A short completion that ends on a 64-byte boundary is a split, not
    # malformed: the read waits for the rest.
    c = Cmd(MEM_RD, IN_WIN | 0x30, 8, 0xF, 0xF)            # bytes 0x30 .. 0x4F
    env.ep.plan(answer(lambda r: cpl_tlp(CS_SC, r.rid, r.tag, [1, 2, 3, 4], bc=32, la=0x30),
                       lambda r: cpl_tlp(CS_SC, r.rid, r.tag, [5, 6, 7, 8], bc=16, la=0x40)))
    beats = await env.expect_cmd(c, OK)
    assert [b.data for b in beats] == [1, 2, 3, 4, 5, 6, 7, 8]
    # A zero-length read: Byte Count 1, one DW.
    env.ep.plan(answer(lambda r: cpl_tlp(CS_SC, r.rid, r.tag, [0xABCD], bc=1, la=0x08)))
    await env.expect_cmd(Cmd(MEM_RD, IN_WIN | 0x8, 1, 0x0), OK, 0xABCD)
    assert await env.err_rd() == 0
    await env.drain()


# --------------------------------------------------------------- splits

def boundaries(c: Cmd) -> list[int]:
    """The DW counts of the completions but the last when a read is split
    at every 64-byte boundary inside it (INBOUND.md, Completer)."""
    s = c.addr >> 2
    points = [k for k in range(1, c.len) if (s + k) % 16 == 0]
    return [b - a for a, b in zip([0] + points, points)]


@cocotb.test()
async def req_split(dut):
    """Injected split completions: the data in address order however many
    completions carry it, Byte Count and Lower Address checked on each, a
    digest DW never data; a failing later completion ends the read at its
    eop (D7) with its status on the last beat, after the data already
    delivered, and the read's remaining completions are unexpected."""
    env = await setup(dut)
    reads = (Cmd(MEM_RD, IN_WIN, 32, 0xF, 0xF), Cmd(MEM_RD, IN_WIN | 0xF84, 31, 0xE, 0x7),
             Cmd(MEM_RD, IN_WIN | 0x20, 32, 0xC, 0x3), Cmd(MEM_RD, IN_WIN | 0x7C, 2, 0xF, 0xF))
    for c in reads:
        cuts_all = boundaries(c)
        for cuts in ([], cuts_all[:1], cuts_all):
            for td in (0, 1):
                data = [0x1000 * len(cuts) + i for i in range(c.len)]
                env.ep.plan(lambda r, cuts=cuts, data=data, td=td:
                            read_completions(r.as_cmd(), r.rid, r.tag, data, cuts, td=td))
                beats = await env.expect_cmd(c, OK)
                assert [b.data for b in beats] == data, f"{c} cuts {cuts}"
    assert await env.err_rd() == 0
    # With gaps on the receive stream and a stalling link.
    env.rx_gap_p, env.tx_ready_p = 0.4, 0.5
    c = reads[2]
    env.ep.plan(lambda r: read_completions(r.as_cmd(), r.rid, r.tag, list(range(32)),
                                           boundaries(c)))
    beats = await env.expect_cmd(c, OK)
    assert [b.data for b in beats] == list(range(32))
    env.rx_gap_p, env.tx_ready_p = 0.0, 1.0
    # D7: the second of three completions fails; the third is unexpected.
    c = reads[2]                                  # 8, 16 and 8 DWs
    first, end = read_bytes(c)
    for how, want, bits in (("bc", BAD, bit(E_MAL)), ("la", BAD, bit(E_MAL)),
                            ("ep", BAD, bit(E_POIS)), ("ur", UR, bit(E_UR)),
                            ("ca", CA, bit(E_CA)), ("rsvd", BAD, bit(E_MAL)),
                            ("short", BAD, bit(E_MAL))):
        def plan(r, how=how):
            good = read_completions(r.as_cmd(), r.rid, r.tag, list(range(32)), [8, 16])
            mid = good[1]
            if how == "bc":
                mid[1] += 1 << 24
            elif how == "la":
                mid[2] ^= 0x04 << 24
            elif how == "ep":
                mid[0] |= 1 << 22
            elif how == "ur":
                mid = cpl_tlp(CS_UR, r.rid, r.tag)
            elif how == "ca":
                mid = cpl_tlp(CS_CA, r.rid, r.tag)
            elif how == "rsvd":
                mid = cpl_tlp(6, r.rid, r.tag)
            else:
                mid = cpl_tlp(CS_SC, r.rid, r.tag, [0] * 8, bc=end - (c.addr + 32),
                              la=(c.addr + 32) & 0x7F)       # ends at 0x60: not 64-byte
            return [good[0], mid, good[2]]
        env.ep.plan(plan)
        beats = await env.expect_cmd(c, want, 0)
        assert [b.data for b in beats[:-1]] == list(range(8)) and len(beats) == 9, (
            f"{how}: {[str(b) for b in beats]}")
        await env.drain(8)
        await expect_err(env, bits | bit(E_UNEXP), f"D7 {how}")


# --------------------------------------------------------------- unexpected

@cocotb.test()
async def req_unexpected(dut):
    """A completion matching no request is dropped and sets bit 4 only:
    with nothing outstanding, a wrong tag (any of its 8 bits), a wrong
    Requester ID, a locked completion, one before its request is sent, a
    duplicate after its request ended, one while a posted write waits.
    The outstanding request is unaffected."""
    env = await setup(dut)
    good = (lambda r: cpl_tlp(CS_SC, r.rid, r.tag, [0x600D]))
    # Nothing outstanding, with every status.
    for k, st in enumerate((CS_SC, CS_UR, CS_CRS, CS_CA, 3, 5, 6, 7)):
        env.queue_rx(cpl_tlp(st, RID, k, [k] if st == CS_SC else []), idle=k)
    await env.drain(4)
    await expect_err(env, bit(E_UNEXP), "nothing outstanding")
    # Wrong tag, wrong Requester ID, locked: then the real one.
    bad = [lambda r, x=x: cpl_tlp(CS_SC, r.rid, r.tag ^ x, [1]) for x in (1, 2, 4, 8, 16, 32,
                                                                        64, 128)]
    bad += [lambda r, x=x: cpl_tlp(CS_SC, r.rid ^ x, r.tag, [1]) for x in (0x0100, 0x8000,
                                                                          0x0001, 0x0080)]
    bad += [lambda r: cpl_tlp(CS_SC, r.rid, r.tag, [1], locked=True),
            lambda r: cpl_tlp(CS_UR, r.rid, r.tag, locked=True)]
    for b in bad:
        env.ep.plan(answer(b, good))
        await env.expect_cmd(Cmd(CFG_RD, cfg_addr()), OK, 0x600D)
        await expect_err(env, bit(E_UNEXP), "mismatch")
    # A completion Type with a Fmt that is no completion kind (a 4-DW
    # header, with or without data, or a prefix) is not a completion: it
    # never matches, and the receive checks drop it as malformed (step 1,
    # bit 6), with no release (its class is a completion's).
    def fmt(f, extra):
        def build(r):
            t = cpl_tlp(CS_SC, r.rid, r.tag, [0xBAD])
            t[0] = (t[0] & ~0xE0) | (f << 5)
            return t[:3] + extra
        return build
    for f, extra in ((0b001, [0]), (0b011, [0, 0xBAD]), (0b100, [0xBAD]), (0b110, [0xBAD])):
        env.ep.plan(answer(fmt(f, extra), good))
        rels = len(env.rel_p) + len(env.rel_np)
        await env.expect_cmd(Cmd(CFG_RD, cfg_addr()), OK, 0x600D)
        assert len(env.rel_p) + len(env.rel_np) == rels, f"Fmt {f:03b}: released"
        await expect_err(env, bit(E_MAL), f"Fmt {f:03b}")
    # Before its request is sent: no non-posted header credit, so it waits.
    env.drv["fc_nph_avail_i"] = 0
    tag = env.next_tag()
    k = await env.offer(Cmd(CFG_RD, cfg_addr(reg=4)))
    env.queue_rx(cpl_tlp(CS_SC, RID, tag, [5]))
    await env.drain(4)
    assert len(env.results) == k, "a completion before its request was sent matched"
    env.drv["fc_nph_avail_i"] = 0xFF
    beats = await env.command_beats(k)
    assert beats == [Rsp(OK, env.ep.cfg_dw(cfg_addr(reg=4)))] and env.ep.reqs[-1].tag == tag
    await expect_err(env, bit(E_UNEXP), "before sent")
    # A duplicate after its request ended.
    env.ep.plan(lambda r: [cpl_tlp(CS_SC, r.rid, r.tag, [7]), cpl_tlp(CS_SC, r.rid, r.tag, [8])])
    await env.expect_cmd(Cmd(CFG_RD, cfg_addr()), OK, 7)
    await env.drain(4)
    await expect_err(env, bit(E_UNEXP), "duplicate")
    # While a posted write waits for the link: no non-posted request.
    env.tx_ready_p = 0.0
    k = await env.offer(Cmd(MEM_WR, IN_WIN))
    env.queue_rx(cpl_tlp(CS_SC, RID, env.next_tag(), bc=4))
    env.queue_rx(cpl_tlp(CS_SC, RID, 0, bc=4))
    await env.drain(4)
    env.tx_ready_p = 1.0
    assert await env.command_beats(k) == [Rsp(OK)]
    await expect_err(env, bit(E_UNEXP), "posted")


# --------------------------------------------------------------- timeout

async def until(env: Env, cycle: int) -> None:
    while env.cycle < cycle:
        await env.tick()


@cocotb.test()
async def req_timeout(dut):
    """The completion timeout at the MAS bound: TIMEOUT and bit 3 with its
    beat in the cycle after edge S + N, S the edge the link took the sop and
    N = floor(2**21 / CPL_TIMEOUT_DIV); a completion after it is unexpected;
    no expiry while a matched completion is received (third header DW to
    eop), then at the edge after its eop; a posted write never times out;
    a read that times out after some data ends with TIMEOUT on its last
    beat; a request whose TLP the link stalls mid-way still times out, and
    the TLP still goes out whole before the next."""
    env = await setup(dut)
    n = env.n_tmo
    assert n == (1 << 21) // env.p.cpl_timeout_div
    env.answer = False
    # Unanswered: CFG_RD, CFG_WR, MEM_RD; then the late completion.
    for c in (Cmd(CFG_RD, cfg_addr()), Cmd(CFG_WR, cfg_addr(), data=1),
              Cmd(MEM_RD, IN_WIN, 32, 0xF, 0xF)):
        beats = await env.expect_cmd(c, TIMEOUT, 0)
        assert len(beats) == 1
        assert env.beat_cycles[-1] == env.tx_sop_cycles[-1] + n, (
            f"{c}: TIMEOUT at cycle {env.beat_cycles[-1]}, sop at {env.tx_sop_cycles[-1]}")
        late = Req.decode(env.sent[-1])
        env.queue_rx(cpl_tlp(CS_SC, RID, late.tag, [1] if c.op == CFG_RD else [],
                             bc=4))
        await env.drain(2)
        await expect_err(env, bit(E_TMO, E_UNEXP), f"{c} timeout")
    env.answer = True
    # A read with its first one or two completions only: TIMEOUT after
    # their data beats.
    for sent in (1, 2):
        c = Cmd(MEM_RD, IN_WIN | 0x20, 32, 0xF, 0xF)          # 8, 16 and 8 DWs
        env.ep.plan(lambda r, sent=sent:
                    read_completions(r.as_cmd(), r.rid, r.tag, list(range(32)), [8, 16])[:sent])
        beats = await env.expect_cmd(c, TIMEOUT, 0)
        got = (8, 24)[sent - 1]
        assert [b.data for b in beats[:-1]] == list(range(got)) and len(beats) == got + 1
        await expect_err(env, bit(E_TMO), f"partial {sent}")
    c = Cmd(MEM_RD, IN_WIN, 32, 0xF, 0xF)
    # Completions around the deadline (sop at edge S); the third header DW:
    #   at S + N: matches, the read completes OK;
    #   at S + N + 1: too late, unexpected, TIMEOUT at S + N;
    #   at S + N - 4, a non-last split: its data, TIMEOUT after its eop.
    env.answer = False
    for case in ("at", "after", "across"):
        nsent = len(env.sent)
        k = await env.offer(c)
        while len(env.sent) == nsent:
            await env.tick()
        s = env.tx_sop_cycles[-1]
        tag = Req.decode(env.sent[-1]).tag
        whole = read_completions(c, RID, tag, list(range(32)))
        split = read_completions(c, RID, tag, list(range(32)), [16])
        if case == "at":
            await until(env, s + n - 2)
            env.queue_rx(whole[0])
            beats = await env.command_beats(k)
            assert [b.data for b in beats] == list(range(32)) and beats[-1] == Rsp(OK, 31, 1)
            assert env.rx_hdr_cycles[-1] == s + n
            assert await env.err_rd() == 0
        elif case == "after":
            await until(env, s + n - 1)
            env.queue_rx(whole[0])
            beats = await env.command_beats(k)
            assert beats == [Rsp(TIMEOUT)] and env.beat_cycles[-1] == s + n
            await env.drain(2)
            assert env.rx_hdr_cycles[-1] == s + n + 1
            await expect_err(env, bit(E_TMO, E_UNEXP), "after")
        else:
            await until(env, s + n - 6)
            env.queue_rx(split[0])
            beats = await env.command_beats(k)
            assert [b.data for b in beats[:-1]] == list(range(16)) and beats[-1] == Rsp(TIMEOUT)
            assert env.rx_hdr_cycles[-1] == s + n - 4
            assert env.beat_cycles[-1] == env.rx_eop_cycles[-1] + 1, "TIMEOUT not after the eop"
            await expect_err(env, bit(E_TMO), "across")
    env.answer = True
    # A posted write never times out, however long the link stalls.
    env.tx_ready_p = 0.0
    k = await env.offer(Cmd(MEM_WR, IN_WIN, data=5))
    await env.idle(n + 16)
    assert len(env.results) == k
    env.tx_ready_p = 1.0
    assert await env.command_beats(k) == [Rsp(OK)]
    # The link takes the sop of a CFG_WR, then stalls past the timeout.
    c = Cmd(CFG_WR, cfg_addr(reg=9), data=0xD00D)
    nsop = len(env.tx_sop_cycles)
    k = await env.offer(c)
    while len(env.tx_sop_cycles) == nsop:
        await env.tick()
    env.tx_ready_p = 0.0
    beats = await env.command_beats(k)
    assert beats == [Rsp(TIMEOUT)] and env.beat_cycles[-1] == env.tx_sop_cycles[-1] + n
    # A new request taken while the old TLP is still held: the old TLP
    # goes out whole first, then the new one.
    c2 = Cmd(CFG_RD, cfg_addr(reg=10))
    await env.idle(2)
    k = await env.offer(c2)
    await env.idle(20)
    env.tx_ready_p = 1.0
    beats = await env.command_beats(k)
    assert beats == [Rsp(OK, env.ep.cfg_dw(c2.addr))]
    old, new = Req.decode(env.sent[-2]), Req.decode(env.sent[-1])
    assert old.kind == CFG_WR_T and old.data == 0xD00D and len(env.sent[-2]) == 4
    assert new.addr == c2.addr and new.tag == (old.tag + 1) % 32
    await env.drain(4)
    # The stalled CFG_WR's completion came after its timeout: unexpected.
    await expect_err(env, bit(E_TMO, E_UNEXP), "stalled")


# --------------------------------------------------------------- link down

async def down_for(env: Env, cycles: int) -> None:
    env.drv["dl_up_i"] = 0
    await env.idle(cycles)
    env.drv["dl_up_i"] = 1


@cocotb.test()
async def req_link_down(dut):
    """Link down with a request outstanding ends it with LINK_DOWN, its beat
    in the cycle after the first edge with dl_up_i 0: before the grant,
    after the sop, mid-TLP, mid-completion (after the data delivered),
    a posted write before its eop. The link's TLPs in progress are
    abandoned; afterwards requests work and the tag continues; a late
    completion is unexpected; the error record and local commands are
    unaffected."""
    env = await setup(dut)
    # Before the grant: no non-posted header credit.
    env.drv["fc_nph_avail_i"] = 0
    nsent = len(env.sent)
    k = await env.offer(Cmd(CFG_RD, cfg_addr()))
    await env.idle(5)
    await down_for(env, 1)
    assert env.results[k][1] == [Rsp(LINK_DOWN)] and env.beat_cycles[-1] == env.cycle - 1
    env.drv["fc_nph_avail_i"] = 0xFF
    await env.idle(6)
    assert len(env.sent) == nsent, "a request sent after it ended LINK_DOWN"
    # After the sop: no answer; a late completion after the link is back.
    env.answer = False
    for c in (Cmd(CFG_RD, cfg_addr()), Cmd(MEM_RD, IN_WIN, 32, 0xF, 0xF)):
        k = await env.offer(c)
        while len(env.sent) == nsent:
            await env.tick()
        nsent = len(env.sent)
        await env.idle(10)
        await down_for(env, 3)
        assert env.results[k][1] == [Rsp(LINK_DOWN)]
        late = Req.decode(env.sent[-1])
        env.queue_rx(read_completions(c, RID, late.tag, [0] * c.len)[0] if c.op == MEM_RD
                     else cpl_tlp(CS_SC, RID, late.tag, [0]))
        await env.drain(2)
        await expect_err(env, bit(E_UNEXP), f"late after link down {c}")
    env.answer = True
    # Mid-TLP: the link took the sop of a CFG_WR, then the link goes down.
    for c in (Cmd(CFG_WR, cfg_addr(), data=3), Cmd(MEM_WR, IN_WIN, data=4)):
        nsop = len(env.tx_sop_cycles)
        k = await env.offer(c)
        while len(env.tx_sop_cycles) == nsop:
            await env.tick()
        env.tx_ready_p = 0.0
        await env.idle(4)
        await down_for(env, 2)
        env.tx_ready_p = 1.0
        assert env.results[k][1] == [Rsp(LINK_DOWN)]
        await env.idle(8)
        assert len(env.sent) == nsent, "an abandoned TLP finished"
    # Mid-completion: 5 data beats delivered, then LINK_DOWN on the last.
    for delivered in (1, 5, 31):
        c = Cmd(MEM_RD, IN_WIN | 0x80, 32, 0xF, 0xF)
        k = await env.offer(c)
        first = len(env.beats)
        while len(env.beats) - first < delivered:
            await env.tick()
        await down_for(env, 1)
        beats = env.results[k][1]
        assert [b.status for b in beats] == [OK] * delivered + [LINK_DOWN], beats
        assert [b.data for b in beats[:-1]] == mem_data(env, c)[:delivered]
        await env.idle(4)
    # In the second completion of a split read.
    c = Cmd(MEM_RD, IN_WIN | 0x20, 32, 0xF, 0xF)
    env.ep.plan(lambda r: read_completions(r.as_cmd(), r.rid, r.tag, list(range(32)), [8, 16]))
    k = await env.offer(c)
    first = len(env.beats)
    while len(env.beats) - first < 12:
        await env.tick()
    await down_for(env, 2)
    beats = env.results[k][1]
    assert [b.data for b in beats[:-1]] == list(range(12)) and beats[-1] == Rsp(LINK_DOWN)
    await env.idle(4)
    # A completion cut by link down holds nothing: the next request,
    # unanswered, still times out at N.
    env.answer = False
    await env.expect_cmd(Cmd(CFG_RD, cfg_addr(reg=2)), TIMEOUT, 0)
    assert env.beat_cycles[-1] == env.tx_sop_cycles[-1] + env.n_tmo
    await env.err_clr()
    env.answer = True
    # Afterwards: requests work, the tag counts every request sent.
    tag = env.next_tag()
    await env.expect_cmd(Cmd(CFG_RD, cfg_addr(reg=1)), OK)
    assert env.ep.reqs[-1].tag == tag
    # The error record survives the link going down; local commands work.
    env.queue_rx(cpl_tlp(CS_SC, RID, 31, [1]))
    await env.drain(2)
    env.drv["dl_up_i"] = 0
    await env.expect_cmd(Cmd(ERR_RD), OK, bit(E_UNEXP))
    await env.expect_cmd(Cmd(ERR_CLR, data=0xFFFF_FFFF), OK, 0)
    await env.expect_cmd(Cmd(ERR_RD), OK, 0)
    await env.expect_cmd(Cmd(MEM_WR, IN_WIN), LINK_DOWN)
    env.drv["dl_up_i"] = 1
    await env.expect_cmd(Cmd(MEM_WR, IN_WIN), OK)


# --------------------------------------------------------------- credits

@cocotb.test()
async def req_credits(dut):
    """Transmit credit gating (REQUESTER.md, Transmit): a request goes only
    when the link shows 1 header credit and its data credits for its class
    (posted MEM_WR 1 data credit, CFG_WR 1, reads 0); all ones passes; the
    completion credits and the other class do not gate it; credits falling
    after the grant do not stop a TLP."""
    env = await setup(dut)
    ops = {"CFG_RD": Cmd(CFG_RD, cfg_addr()), "CFG_WR": Cmd(CFG_WR, cfg_addr(), data=1),
           "MEM_RD": Cmd(MEM_RD, IN_WIN, 4, 0xF, 0xF), "MEM_WR": Cmd(MEM_WR, IN_WIN, data=2)}
    cases = (
        ("fc_nph_avail_i", 0, ("CFG_RD", "CFG_WR", "MEM_RD")),
        ("fc_npd_avail_i", 0, ("CFG_WR",)),
        ("fc_ph_avail_i", 0, ("MEM_WR",)),
        ("fc_pd_avail_i", 0, ("MEM_WR",)),
        ("fc_cplh_avail_i", 0, ()),
        ("fc_cpld_avail_i", 0, ()),
        ("fc_nph_avail_i", 1, ()),
        ("fc_npd_avail_i", 1, ()),
        ("fc_ph_avail_i", 1, ()),
        ("fc_pd_avail_i", 1, ()),
        ("fc_npd_avail_i", 0x7FF, ()),
    )
    for name, value, blocked in cases:
        for op, c in ops.items():
            full = env.drv[name]
            env.drv[name] = value
            nsop = len(env.tx_sop_cycles)
            k = await env.offer(c)
            await env.idle(40)
            held = len(env.tx_sop_cycles) == nsop
            assert held == (op in blocked), f"{name}={value}: {op} held {held}"
            env.drv[name] = full
            beats = await env.command_beats(k)
            assert beats[-1].status == OK, f"{name}={value} {op}: {beats}"
    # Credits falling after the grant: the TLP still goes out whole.
    for op in ("CFG_WR", "MEM_WR"):
        env.tx_ready_p = 0.0
        k = await env.offer(ops[op])
        await env.idle(4)
        assert int(dut.tlp_tx_valid_o.value) == 1
        for name in ("fc_ph_avail_i", "fc_pd_avail_i", "fc_nph_avail_i", "fc_npd_avail_i"):
            env.drv[name] = 0
        env.tx_ready_p = 1.0
        await env.idle(8)
        assert len(env.sent[-1]) == 4
        for name in ("fc_ph_avail_i", "fc_nph_avail_i"):
            env.drv[name] = 0xFF
        for name in ("fc_pd_avail_i", "fc_npd_avail_i"):
            env.drv[name] = 0xFFF
        assert (await env.command_beats(k))[-1].status == OK


# --------------------------------------------------------------- error record

@cocotb.test()
async def err_record(dut):
    """The error record (MAS, Error record): each requester event sets its
    bit; ERR_RD returns the record as before its take's edge, bits 31:13
    0; ERR_CLR clears the bits set in data and an event at the same edge
    wins; ERR_* refused by checks 3 and 6 change nothing; the record is
    sticky until ERR_CLR or rst_i."""
    env = await setup(dut)
    rd = Cmd(MEM_RD, IN_WIN, 4, 0xF, 0xF)
    events = ((rd, lambda r: cpl_tlp(CS_UR, r.rid, r.tag), E_UR),
              (rd, lambda r: cpl_tlp(CS_CA, r.rid, r.tag), E_CA),
              (Cmd(CFG_RD, cfg_addr()), lambda r: cpl_tlp(CS_CRS, r.rid, r.tag), E_CRS),
              (rd, lambda r: cpl_tlp(CS_SC, r.rid, r.tag, [0] * 4, bc=16, ep=1), E_POIS),
              (rd, lambda r: cpl_tlp(CS_SC, r.rid, r.tag, [0] * 4, bc=15), E_MAL),
              (rd, lambda r: cpl_tlp(CS_SC, r.rid ^ 1, r.tag, [0] * 4, bc=16), E_UNEXP))
    want = 0
    for c, plan, b in events:
        if b == E_UNEXP:
            env.ep.plan(lambda r, plan=plan: [plan(r)] + env.ep.default(r))
        else:
            env.ep.plan(answer(plan))
        await env.command(c)
        want |= 1 << b
        assert await env.err_rd() == want, f"after bit {b}"
    # Refused ERR_* change nothing (checks 3 and 6).
    for c in (Cmd(ERR_CLR, len=0, data=0xFFFF_FFFF), Cmd(ERR_CLR, len=2, data=0xFFFF_FFFF),
              Cmd(ERR_CLR, len=1, last_be=0x1, data=0xFFFF_FFFF),
              Cmd(ERR_CLR, len=63, last_be=0xF, data=0xFFFF_FFFF),
              Cmd(ERR_RD, len=2), Cmd(ERR_RD, len=1, last_be=0x8)):
        await env.expect_cmd(c, LOCAL_UR, 0)
    assert await env.err_rd() == want
    # ERR_CLR clears the bits set in data, any first_be; 0 clears nothing.
    await env.expect_cmd(Cmd(ERR_CLR, data=0, first_be=0xF), OK, 0)
    assert await env.err_rd() == want
    await env.expect_cmd(Cmd(ERR_CLR, data=bit(E_UR, E_UNEXP), first_be=0x0), OK, 0)
    want &= ~bit(E_UR, E_UNEXP)
    assert await env.err_rd() == want
    await env.err_clr(0xFFFF_FFFF)
    assert await env.err_rd() == 0
    # Same edge: an unexpected completion's eop at the ERR_CLR's take: the
    # event wins; at an ERR_RD's take: the read shows the record before it.
    for op in (ERR_CLR, ERR_RD):
        env.queue_rx(cpl_tlp(CS_SC, RID, 30, bc=4))      # 3 DWs: eop at the third edge
        await env.idle(2)
        k = await env.offer(Cmd(op, data=0xFFFF_FFFF))
        assert env.rx_eop_cycles[-1] == env.cycle - 1
        assert env.results[k][1] == [Rsp(OK, 0)]
        assert await env.err_rd() == bit(E_UNEXP)
        await env.err_clr()
    # Sticky until rst_i.
    env.queue_rx(cpl_tlp(CS_SC, RID, 30, bc=4))
    await env.drain(2)
    await env.reset(2)
    assert await env.err_rd() == 0


# --------------------------------------------------------------- random

def random_plan(env: Env, r):
    """A random answer to the next request (DV plan, random_req)."""
    k = env.rng.random()

    def plan(q: Req):
        rid, tag = q.rid, q.tag
        if q.kind == MEM_RD_T:
            c = q.as_cmd()
            data = [env.rng.getrandbits(32) for _ in range(c.len)]
            # Split at a random subset of the 64-byte boundaries.
            sizes, acc, start = [], 0, c.addr >> 2
            for b in range(1, c.len):
                if (start + b) % 16 == 0 and env.rng.random() < 0.5:
                    sizes.append(b - acc)
                    acc = b
            good = read_completions(c, rid, tag, data, sizes, td=int(env.rng.random() < 0.2))
        elif q.kind == CFG_RD_T:
            good = [cpl_tlp(CS_SC, rid, tag, [env.rng.getrandbits(32)],
                            td=int(env.rng.random() < 0.2))]
        else:
            good = [cpl_tlp(CS_SC, rid, tag, bc=4)]
        stray = cpl_tlp(CS_SC, rid, (tag + 1 + env.rng.randrange(255)) & 0xFF, [1])
        if k < 0.50:
            return good
        if k < 0.58:
            return [cpl_tlp(env.rng.choice((CS_UR, CS_CA, CS_CRS, 3, 5, 6, 7)), rid, tag,
                            ep=int(env.rng.random() < 0.3))]
        if k < 0.64:
            t = list(env.rng.choice(good))
            t[0] |= 1 << 22                       # poisoned
            return [t] + good
        if k < 0.72:
            t = list(good[0])
            f = env.rng.randrange(4)
            if f == 0:
                t[1] ^= 1 << (24 + env.rng.randrange(8))     # Byte Count
            elif f == 1:
                t[2] ^= 1 << (24 + env.rng.randrange(7))     # Lower Address
            elif f == 2:
                # One DW more or less of data, Length to match (at most 32).
                n = t[0] >> 24 if t[0] & 0x40 else 0
                if 0 < n < 32:
                    t, t[0] = t + [0], t[0] + (1 << 24)
                elif n > 1:
                    t, t[0] = t[:-1], t[0] - (1 << 24)
            else:
                # Fmt with data flipped: Length 1 when it now has data.
                t[0] = (t[0] ^ (1 << 6)) & ~(1 << 23) & ~(0xFF << 24) & ~(0x3 << 16)
                t = t[:3] + [0] if t[0] & 0x40 else t[:3]
                if t[0] & 0x40:
                    t[0] |= 1 << 24
            return [t] + good[1:]
        if k < 0.80:
            return [stray] + good
        if k < 0.86:
            return good + [good[-1]]                          # a duplicate: unexpected
        if k < 0.92 and len(good) > 1:
            t = list(good[1])
            t[1] = (t[1] & ~(0x7 << 21)) | (CS_UR << 21) if env.rng.random() < 0.5 else t[1] ^ (1 << 24)
            return [good[0], t] + good[2:]                   # D7
        if k < 0.93:
            return []                                         # no answer: timeout
        return good

    return plan


def random_cmd(env: Env, r) -> Cmd:
    k = r.random()
    if k < 0.08:
        return Cmd(r.choice((ERR_RD, ERR_CLR, 4)), 0, 1, r.getrandbits(4), 0,
                   r.getrandbits(32) if r.random() < 0.3 else 0)
    if k < 0.12:
        return Cmd(r.choice((CFG_RD, MEM_RD, MEM_WR)), (LIMIT + 1) << 20)      # refused
    op = r.choice((CFG_RD, CFG_WR, MEM_RD, MEM_RD, MEM_WR))
    if op in (CFG_RD, CFG_WR):
        return Cmd(op, cfg_addr(fn=r.randrange(8), reg=r.randrange(1024)),
                   first_be=r.randrange(1, 16), data=r.getrandbits(32))
    if op == MEM_WR:
        return Cmd(op, IN_WIN | r.getrandbits(20) & ~3, first_be=r.randrange(1, 16),
                   data=r.getrandbits(32))
    n = r.choice((1, 2, 3, 8, 16, 31, 32, r.randrange(1, 33)))
    dw = r.randrange(0, 1024 - n + 1)
    a = IN_WIN | (r.getrandbits(8) << 12) | (dw << 2)
    if n == 1:
        return Cmd(op, a, 1, r.getrandbits(4))
    return Cmd(op, a, n, r.choice((0xF, 0xE, 0xC, 0x8)), r.choice((0x1, 0x3, 0x7, 0xF)))


def credit_outage(env: Env, r) -> None:
    name = r.choice(("fc_ph_avail_i", "fc_pd_avail_i", "fc_nph_avail_i", "fc_npd_avail_i"))
    full = FC_INPUTS[name]
    env.drv[name] = 0
    left = [r.randint(1, 40)]

    def hook(e: Env) -> bool:
        left[0] -= 1
        if left[0] <= 0:
            e.drv[name] = full
            return False
        return True
    env.hooks.append(hook)


def link_blip(env: Env, r) -> None:
    wait, length = [r.randint(0, 30)], [r.randint(1, 4)]

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


async def run_random_req(dut, cmds: int) -> None:
    env = await setup(dut, answer_delay=(0, 12))
    r = env.rng
    done = 0
    while done < cmds:
        if r.random() < 0.003:
            env.hooks = []
            env.drv |= FC_INPUTS | {"dl_up_i": 1}
            await env.reset(r.randrange(1, 4))
            await env.hdr_wr(1, 0x0000_0006)
            await env.hdr_wr(6, (0x07 << 16) | (BUS << 8) | PRI)
            await env.hdr_wr(8, (LIMIT << 20) | (BASE << 4))
        if r.random() < 0.05:
            env.tx_ready_p = r.choice((1.0, 1.0, 0.8, 0.4))
            env.rx_gap_p = r.choice((0.0, 0.0, 0.2, 0.5))
        if r.random() < 0.04:
            credit_outage(env, r)
        if r.random() < 0.02:
            link_blip(env, r)
        env.ep.plan(random_plan(env, r))
        await env.command(random_cmd(env, r))
        env.ep.plans.clear()
        done += 1
        if r.random() < 0.1:
            await env.idle(r.randrange(1, 8))
    env.hooks = []
    env.drv |= FC_INPUTS | {"dl_up_i": 1}
    await env.drain(16)
    statuses = {beats[-1].status for c, beats in env.results if c.op < 4}
    assert {OK, UR, CA, CRS, BAD, LINK_DOWN, LOCAL_UR} <= statuses, statuses


@cocotb.test()
async def random_req(dut):
    """L1: 1500 random endpoint commands with random answers, link stalls,
    receive gaps, credit outages, link drops and resets, against the model."""
    await run_random_req(dut, 1500)


@cocotb.test()
async def random_req_long(dut):
    """L2: 20000 random endpoint commands, as random_req."""
    await run_random_req(dut, 20000)


@cocotb.test()
async def timeout_once(dut):
    """One unanswered configuration read: TIMEOUT in the cycle after edge
    S + N, at the entry's CPL_TIMEOUT_DIV (the FPGA value 1: N = 2**21)."""
    env = await setup(dut)
    env.answer = False
    beats = await env.expect_cmd(Cmd(CFG_RD, cfg_addr()), TIMEOUT, 0)
    assert len(beats) == 1 and env.beat_cycles[-1] == env.tx_sop_cycles[-1] + env.n_tmo
    assert env.n_tmo == (1 << 21) // env.p.cpl_timeout_div
    assert await env.err_rd() == bit(E_TMO)
