"""Unit tests of the rc_tl model against hand-worked spec cases
(wiki/src/rtl/rc_tl/MAS.md#commands, REQUESTER.md,
wiki/src/arch/rc/HAS.md#type-1-header, wiki/src/arch/tl/TLP.md). No
simulator."""

from pcie_rc_tl_model import (BAD, CA, CFG_RD, CFG_WR, CRS, CS_CA, CS_CRS, CS_SC, CS_UR,
                              E_MAL, E_POIS, E_TMO, E_UNEXP, ERR_CLR, ERR_RD, HDR_RD, HDR_WR,
                              HOST, HOST_RD, HOST_WR, LINK_DOWN, LOCAL_UR, MEM_RD, MEM_WR, OK,
                              TIMEOUT, UR, Cmd, FrontEnd, Inputs, Params, RcTl, Rsp,
                              cpl_tlp, failed_check, read_bytes, read_completions,
                              request_tlp, timeout_cycles)
from pcie_rc_tl_model import (CAPTURE, CLS_CPL, CLS_NP, CLS_P, DROP, E_COR, E_IN_UR,
                              E_MSG, NP_DROP, READ, UR_CPL, URLK_CPL, WRITE, Header, RxHdr,
                              completions_of, credit_class, judge, kind_known, malformed)


def fe_open(p: Params = Params()) -> FrontEnd:
    """A front end with Memory Space Enable, window 0x100..0x1FF MiB, bus 1."""
    fe = FrontEnd(p)
    fe.execute(Cmd(HDR_WR, 0x04, data=0x2), 1)
    fe.execute(Cmd(HDR_WR, 0x18, data=0x0001_0100), 1)
    fe.execute(Cmd(HDR_WR, 0x20, data=0x1FF0_1000), 1)
    return fe


def test_pack_round_trip():
    c = Cmd(MEM_RD, 0x1234_5678, 32, 0xE, 0x7, 0xDEAD_BEEF)
    assert Cmd.unpack(c.pack()) == c
    assert c.pack() >> 78 == MEM_RD
    r = Rsp(LINK_DOWN, 0x8000_0001, 1)
    assert Rsp.unpack(r.pack()) == r


def test_header_reset_values():
    fe = FrontEnd(Params(vendor_id=0x1234, device_id=0xABCD, revision_id=0x5A))
    expect = {0: 0xABCD_1234, 1: 0, 2: 0x0604_005A, 3: 0x0001_0000, 6: 0, 8: 0}
    for idx in range(1024):
        assert fe.execute(Cmd(HDR_RD, idx << 2), 1) == Rsp(OK, expect.get(idx, 0))


def test_header_writes():
    fe = FrontEnd()
    for idx in range(16):
        fe.execute(Cmd(HDR_WR, idx << 2, data=0xFFFF_FFFF), 1)
    got = {idx: fe.execute(Cmd(HDR_RD, idx << 2), 1).data for idx in range(16)}
    assert got[1] == 0x6 and got[3] == 0x0001_00FF and got[6] == 0x00FF_FFFF
    assert got[8] == 0xFFF0_FFF0 and got[0] == 0 and got[2] == 0x0604_0000
    assert all(got[i] == 0 for i in (4, 5, 7, 9, 10, 11, 12, 13, 14, 15))
    fe.execute(Cmd(HDR_WR, 0x20, first_be=0b0101, data=0x0000_0000), 1)
    assert fe.execute(Cmd(HDR_RD, 0x20), 1).data == 0xFF00_FF00
    fe.execute(Cmd(HDR_WR, 0x18, first_be=0, data=0), 1)
    assert fe.execute(Cmd(HDR_RD, 0x18), 1).data == 0x00FF_FFFF


def test_checks_in_order():
    fe = fe_open()
    h, p = fe.hdr, fe.p
    assert failed_check(Cmd(10), h, p) == 2
    assert failed_check(Cmd(MEM_RD, 0x1000_0000, 33), h, p) == 3
    assert failed_check(Cmd(MEM_RD, 0x1000_0000, 0), h, p) == 3
    assert failed_check(Cmd(MEM_WR, 0x1000_0000, 2, 0xF, 0xF), h, p) == 3
    assert failed_check(Cmd(CFG_RD, 0x1010_0000), h, p) == 4
    assert failed_check(Cmd(HDR_RD, 0x1000), h, p) == 4
    assert failed_check(Cmd(HOST_RD, 0x1000), h, p) == 4
    assert failed_check(Cmd(HOST_RD, 0xFFC), h, p) == 0
    assert failed_check(Cmd(ERR_RD, 0xFFFF_FFFF), h, p) == 0
    assert failed_check(Cmd(MEM_RD, 0x1000_0F84, 32, 0xF, 0xF), h, p) == 5
    assert failed_check(Cmd(MEM_RD, 0x1000_0F80, 32, 0xF, 0xF), h, p) == 0
    assert failed_check(Cmd(MEM_WR, 0x1000_0000, 1, 0x0), h, p) == 6
    assert failed_check(Cmd(CFG_RD, 0x0010_0000), h, p) == 0
    assert failed_check(Cmd(CFG_RD, 0x0020_0000), h, p) == 7
    assert failed_check(Cmd(CFG_RD, 0x0010_8000), h, p) == 7
    assert failed_check(Cmd(MEM_WR, 0x0FFF_FFFC), h, p) == 7
    assert failed_check(Cmd(MEM_WR, 0x2000_0000), h, p) == 7
    assert failed_check(Cmd(MEM_WR, 0x1FFF_FFFC), h, p) == 0


def test_byte_enables():
    fe = fe_open()
    h, p = fe.hdr, fe.p
    a = 0x1000_0000
    assert failed_check(Cmd(MEM_RD, a, 1, 0x0), h, p) == 0          # zero-length read
    assert failed_check(Cmd(MEM_RD, a, 1, 0x5), h, p) == 0
    assert failed_check(Cmd(MEM_RD, a, 1, 0xF, 0x1), h, p) == 6
    assert failed_check(Cmd(MEM_RD, a, 2, 0x5, 0x9), h, p) == 0     # QW aligned, len 2
    assert failed_check(Cmd(MEM_RD, a + 4, 2, 0x5, 0x9), h, p) == 6
    assert failed_check(Cmd(MEM_RD, a, 3, 0xE, 0x7), h, p) == 0
    assert failed_check(Cmd(MEM_RD, a, 3, 0x6, 0x7), h, p) == 6
    assert failed_check(Cmd(MEM_RD, a, 2, 0x0, 0x1), h, p) == 6
    assert failed_check(Cmd(HDR_WR, 0x4, 1, 0x0), h, p) == 0
    assert failed_check(Cmd(HOST_WR, 0x4, 1, 0x0), h, p) == 0
    assert failed_check(Cmd(ERR_CLR, 0, 1, 0x0, 0x1), h, p) == 6
    assert failed_check(Cmd(CFG_WR, 0x0010_0000, 1, 0x0), h, p) == 6


def test_local_results():
    fe = fe_open()
    assert fe.execute(Cmd(MEM_WR, 0x1000_0000), 1) is None          # to the requester
    assert fe.execute(Cmd(MEM_WR, 0x1000_0000), 0) == Rsp(LINK_DOWN)
    # Check 1 outranks checks 2 to 7.
    assert fe.execute(Cmd(MEM_WR, 0x2000_0000), 0) == Rsp(LINK_DOWN)
    assert fe.execute(Cmd(CFG_RD, 0x0010_0000, 2), 0) == Rsp(LINK_DOWN)
    assert failed_check(Cmd(MEM_RD, 0x1000_0F84, 32, 0xF, 0xF), fe.hdr, fe.p, 0) == 1
    assert failed_check(Cmd(15), fe.hdr, fe.p, 0) == 2
    assert fe.execute(Cmd(HOST_RD, 0), 1) == HOST              # waits for host memory
    assert fe.execute(Cmd(HDR_RD, 0), 0) == Rsp(OK, 0)
    # ERR_*: checks 3 and 6 refuse; ERR_CLR clears the bits set in data.
    fe.err = 0x1FFF
    assert fe.execute(Cmd(ERR_RD, len=2), 1) == Rsp(LOCAL_UR)
    assert fe.execute(Cmd(ERR_CLR, len=1, last_be=1, data=0xFF), 1) == Rsp(LOCAL_UR)
    assert fe.err == 0x1FFF
    assert fe.execute(Cmd(ERR_CLR, 0xFFFF_FFFF, data=0x0000_0F0F, first_be=0), 1) == Rsp(OK)
    assert fe.execute(Cmd(ERR_RD), 0) == Rsp(OK, 0x10F0)


def test_request_tlps():
    """REQUESTER.md, Requests, against hand-worked header bytes."""
    c = Cmd(CFG_RD, (5 << 20) | 0x40)
    assert request_tlp(c, 0, 3) == [0x0100_0004, 0x0F03_0000, 0x4000_0005]
    c = Cmd(CFG_WR, (0xA5 << 20) | (0 << 15) | (6 << 12) | (0xC << 8) | 0xFC, first_be=0x3,
            data=0x1122_3344)
    assert request_tlp(c, 0x7E, 0x1F) == [0x0100_0044, 0x031F_007E, 0xFC0C_06A5, 0x1122_3344]
    c = Cmd(MEM_WR, 0x1000_0010, data=0xDEAD_BEEF)
    assert request_tlp(c, 1, 9) == [0x0100_0040, 0x0F00_0001, 0x1000_0010, 0xDEAD_BEEF]
    c = Cmd(MEM_RD, 0x1000_0F80, 32, 0xF, 0xF)
    assert request_tlp(c, 0, 0x1F) == [0x2000_0000, 0xFF1F_0000, 0x800F_0010]


def test_read_bytes():
    assert read_bytes(Cmd(MEM_RD, 0x100, 1, 0x0)) == (0x100, 0x101)    # zero-length read
    assert read_bytes(Cmd(MEM_RD, 0x100, 1, 0x6)) == (0x101, 0x103)
    assert read_bytes(Cmd(MEM_RD, 0x104, 3, 0xC, 0x1)) == (0x106, 0x10D)
    assert read_bytes(Cmd(MEM_RD, 0x100, 32, 0xF, 0xF)) == (0x100, 0x180)


def test_read_completions():
    """Byte Count and Lower Address of a split read
    [cite: PCIe Base Spec, rev 3.0, §2.3.1.1]."""
    c = Cmd(MEM_RD, 0x1000_0F84, 31, 0xE, 0x7)       # bytes 0xF85 .. 0xFFE
    cpls = read_completions(c, 0x0100, 4, list(range(31)), cuts=[15])
    # The first: 15 DWs to 0xFC0, Byte Count 0x7A (0xFFF - 0xF85), Lower Address 0x05.
    assert cpls[0][1] >> 24 == 0x7A and (cpls[0][2] >> 24) == 0x05
    assert len(cpls[0]) == 3 + 15 and len(cpls[1]) == 3 + 16
    # The second: Byte Count 0x3F (0xFFF - 0xFC0), Lower Address 0x40.
    assert cpls[1][1] >> 24 == 0x3F and (cpls[1][2] >> 24) == 0x40


def link(m: RcTl, steps: int, i: Inputs | None = None, rx: list | None = None) -> list[dict]:
    """Step the model, feeding rx beats one per cycle."""
    out = []
    rx = list(rx or [])
    for _ in range(steps):
        x = Inputs(**(i.__dict__ if i else {}))
        if rx:
            x.rx_valid, x.rx_data, x.rx_sop, x.rx_eop = rx.pop(0)
        out.append(m.step(x))
    return out


def beats_of(tlp: list[int]) -> list[tuple[int, int, int, int]]:
    return [(1, dw, int(k == 0), int(k == len(tlp) - 1)) for k, dw in enumerate(tlp)]


def open_model(div: int = 1024) -> RcTl:
    m = RcTl(Params(cpl_timeout_div=div))
    m.step(Inputs(rst=1))
    for c in (Cmd(HDR_WR, 0x04, data=0x2), Cmd(HDR_WR, 0x18, data=0x0001_0100),
              Cmd(HDR_WR, 0x20, data=0x1FF0_1000)):
        m.step(Inputs(cmd_valid=1, cmd=c))
        m.step(Inputs())
    return m


def test_cycle_model_local():
    m = RcTl()
    rd = Cmd(HDR_RD, 0x8)
    o = m.step(Inputs(rst=1, cmd_valid=1, cmd=rd))
    assert (o["cmd_ready"], o["beat"], o["take"]) == (0, None, False)
    o = m.step(Inputs(cmd_valid=1, cmd=rd))
    assert o["take"] and o["cmd_ready"] == 0 and o["beat"] == Rsp(OK, 0x0604_0000)
    o = m.step(Inputs(cmd_valid=1, cmd=rd))                    # beat cycle: not taken
    assert (o["cmd_ready"], o["beat"], o["take"]) == (1, None, False)


def test_cycle_model_cfg_read():
    """MAS, Timing: the request on tlp_tx_* from the third cycle after the
    take; the completion's data beat in the cycle after its DW."""
    m = open_model()
    c = Cmd(CFG_RD, (1 << 20) | 0x10)
    o = m.step(Inputs(cmd_valid=1, cmd=c))                     # take: edge E
    assert o["take"] and o["beat"] is None and o["tx"].valid == 0
    assert m.step(Inputs())["tx"].valid == 0                   # E+1: the grant
    tlp = request_tlp(c, 0, 0)
    got = [m.step(Inputs())["tx"] for _ in range(3)]           # E+2 to E+4
    assert [(b.valid, b.data, b.sop, b.eop) for b in got] == beats_of(tlp)
    assert m.step(Inputs())["tx"].valid == 0
    cpl = cpl_tlp(CS_SC, 0x0000, 0, [0xCAFE_F00D])
    outs = link(m, 4, rx=beats_of(cpl))
    assert [o["beat"] for o in outs] == [None, None, None, Rsp(OK, 0xCAFE_F00D, 1)]
    assert m.tag == 1 and m.req is None and m.err == 0


def test_cycle_model_failures():
    for status, ep, want, bit in ((CS_UR, 0, UR, 0), (CS_CA, 0, CA, 1), (CS_CRS, 0, CRS, 2),
                                  (3, 0, BAD, E_MAL), (CS_SC, 1, BAD, E_POIS)):
        m = open_model()
        c = Cmd(CFG_RD, 1 << 20)
        m.step(Inputs(cmd_valid=1, cmd=c))
        link(m, 6)
        data = [5] if status == CS_SC else []
        outs = link(m, len(data) + 4, rx=beats_of(cpl_tlp(status, 0x0000, 0, data, ep=ep)))
        beats = [o["beat"] for o in outs if o["beat"] is not None]
        assert beats == [Rsp(want)], (status, beats)
        assert m.err == 1 << bit
    # CRS on a memory read is BAD and malformed.
    m = open_model()
    c = Cmd(MEM_RD, 0x1000_0000)
    m.step(Inputs(cmd_valid=1, cmd=c))
    link(m, 6)
    outs = link(m, 4, rx=beats_of(cpl_tlp(CS_CRS, 0x0000, 0)))
    assert [o["beat"] for o in outs if o["beat"]] == [Rsp(BAD)] and m.err == 1 << E_MAL


def test_cycle_model_split_and_unexpected():
    m = open_model()
    c = Cmd(MEM_RD, 0x1000_0F84, 31, 0xE, 0x7)
    m.step(Inputs(cmd_valid=1, cmd=c))
    link(m, 6)
    data = list(range(100, 131))
    first, second = read_completions(c, 0x0000, 0, data, cuts=[15])
    outs = link(m, len(first) + len(second) + 2, rx=beats_of(first) + beats_of(second))
    beats = [o["beat"] for o in outs if o["beat"] is not None]
    assert [b.data for b in beats] == data and [b.last for b in beats] == [0] * 30 + [1]
    # The same again is now unexpected: dropped, bit 4.
    outs = link(m, len(second) + 2, rx=beats_of(second))
    assert all(o["beat"] is None for o in outs) and m.err == 1 << E_UNEXP


def test_cycle_model_timeout():
    """N cycles after the sop's edge; a completion after it is unexpected."""
    m = open_model(1024)
    n = timeout_cycles(1024)
    assert n == 2048 and timeout_cycles(1) == 2097152 and timeout_cycles(1000) == 2097
    c = Cmd(MEM_WR, 0x1000_0000)
    m.step(Inputs(cmd_valid=1, cmd=c))
    beats = [o["beat"] for o in link(m, 8) if o["beat"] is not None]
    assert beats == [Rsp(OK)]                                  # a posted write: no timeout
    c = Cmd(CFG_WR, 1 << 20)
    m.step(Inputs(cmd_valid=1, cmd=c))
    outs = link(m, 3)                                          # sop taken at the third edge
    assert outs[2]["tx_taken"].sop == 1
    outs = link(m, n)
    # Edge S + k is outs[k - 1]: the beat follows edge S + N.
    assert [k for k, o in enumerate(outs) if o["beat"] is not None] == [n - 1]
    assert outs[n - 1]["beat"] == Rsp(TIMEOUT) and m.err == 1 << E_TMO


# --------------------------------------------------------------- inbound

def rx(typ=0x00, fmt=0b000, addr=0x40, lfield=1, fbe=0xF, lbe=0, tc=0, attr=0, ep=0,
       code=0) -> RxHdr:
    return RxHdr(fmt=fmt, typ=typ, tc=tc, td=0, ep=ep, attr=attr, lfield=lfield, rid=0x0100,
                 tag=7, fbe=fbe, lbe=lbe, code=code, addr=addr)


def open_hdr(bme: int = 1) -> Header:
    """Bus Master Enable, window 0x100..0x1FF MiB; host memory 4 KiB at 0."""
    h = Header(Params())
    h.write(1, 0xF, 0x2 | (bme << 2))
    h.write(8, 0xF, 0x1FF0_1000)
    return h


def test_kinds_and_classes():
    """TLP.md, Kinds; FLOW_CONTROL.md, classes."""
    assert kind_known(0b000, 0x00) and kind_known(0b011, 0x00) and not kind_known(0b100, 0x00)
    assert kind_known(0b001, 0x01) and not kind_known(0b010, 0x01)
    assert kind_known(0b010, 0x04) and not kind_known(0b001, 0x04)
    assert kind_known(0b001, 0x10) and kind_known(0b011, 0x17) and not kind_known(0b000, 0x10)
    assert kind_known(0b011, 0x0E) and not kind_known(0b000, 0x0C) and not kind_known(0b000, 0x03)
    assert credit_class(0b010, 0x00) == CLS_P and credit_class(0b001, 0x13) == CLS_P
    assert credit_class(0b000, 0x00) == CLS_NP and credit_class(0b010, 0x0C) == CLS_NP
    assert credit_class(0b001, 0x0A) == CLS_CPL and credit_class(0b110, 0x00) == CLS_P


def test_malformed_steps():
    """INBOUND.md, Receive checks, steps 3 to 6."""
    assert not malformed(rx(lfield=1, fbe=0))                        # zero-length read
    assert malformed(rx(lfield=1, lbe=1))
    assert not malformed(rx(lfield=2, fbe=0x5, lbe=0xA, addr=0x40))  # one aligned QW
    assert malformed(rx(lfield=2, fbe=0x5, lbe=0xA, addr=0x44))
    assert malformed(rx(lfield=3, fbe=0xF, lbe=0x2)) and malformed(rx(lfield=2, fbe=0, lbe=1))
    assert malformed(rx(addr=0xFFC, lfield=2, lbe=0xF)) and not malformed(rx(addr=0xFF8, lfield=2, lbe=0xF))
    assert malformed(rx(addr=0x40, lfield=0, lbe=0xF))               # 1024 DWs from 0x40
    assert not malformed(rx(addr=0, lfield=0, lbe=0xF))
    assert malformed(rx(typ=0x04, lfield=2)) and malformed(rx(typ=0x02, tc=1))
    assert malformed(rx(typ=0x05, attr=1)) and not malformed(rx(typ=0x04))
    assert malformed(rx(typ=0x10, fmt=0b001, tc=1, code=0x30))
    assert not malformed(rx(typ=0x10, fmt=0b001, tc=1, code=0x7E))


def test_inbound_routing():
    """rc HAS, Inbound routing, first match; poisoned after; UR outranks it."""
    h, p = open_hdr(), Params()
    w = rx(fmt=0b010, addr=0x40)
    assert judge(w, h, p) == (WRITE, 0)
    assert judge(rx(addr=0x40, lfield=4, lbe=0xF), h, p) == (READ, 0)
    assert judge(rx(fmt=0b010, addr=0xFEE0_0010), h, p) == (CAPTURE, 0)
    assert judge(rx(fmt=0b010, addr=0xFEE0_0010, lfield=2, lbe=0xF), h, p) == (DROP, 1 << E_IN_UR)
    assert judge(rx(addr=0xFEE0_0010), h, p) == (UR_CPL, 1 << E_IN_UR)
    assert judge(rx(fmt=0b010, addr=0x1000_0040), h, p) == (DROP, 1 << E_IN_UR)   # window
    assert judge(rx(addr=0x1000), h, p) == (UR_CPL, 1 << E_IN_UR)                 # past host
    assert judge(rx(fmt=0b001, addr=(1 << 32) | 0x40), h, p) == (UR_CPL, 1 << E_IN_UR)
    assert judge(rx(typ=0x01, addr=0x40), h, p) == (URLK_CPL, 1 << E_IN_UR)
    assert judge(rx(typ=0x04), h, p) == (UR_CPL, 1 << E_IN_UR)
    assert judge(rx(addr=0x40, ep=1), h, p) == (UR_CPL, 1 << E_POIS)
    assert judge(rx(fmt=0b010, addr=0x40, ep=1), h, p) == (DROP, 1 << E_POIS)
    assert judge(rx(addr=0x1000_0040, ep=1), h, p) == (UR_CPL, 1 << E_IN_UR)
    assert judge(rx(addr=0x40, lfield=1, lbe=1, ep=1), h, p) == (NP_DROP, 1 << E_MAL)
    off = open_hdr(bme=0)
    assert judge(w, off, p) == (DROP, 1 << E_IN_UR)
    assert judge(rx(typ=0x10, fmt=0b001, code=0x30), off, p) == (DROP, 1 << E_COR)
    assert judge(rx(typ=0x12, fmt=0b011, code=0x99, lfield=1), h, p) == (DROP, 1 << E_MSG)


def test_completer_split():
    """INBOUND.md, Completer: 64 DWs at 0x24, F 1110, L 0111: T 254 from B
    0x25, end 0x123; 23 DWs to the 64-byte boundary 0x80, then 32 to 0x100,
    then 9; Byte Counts 254, 0x123 - 0x80, 0x123 - 0x100."""
    assert completions_of(rx(addr=0x24, lfield=64, fbe=0xE, lbe=0x7)) == [
        (0x24, 23, 254, 0x25), (0x80, 32, 0x123 - 0x80, 0), (0x100, 9, 0x123 - 0x100, 0)]
    # 1024 DWs: 32 completions of 32; the first's Byte Count 4096 is sent as 0.
    c = completions_of(rx(addr=0, lfield=0, lbe=0xF))
    assert len(c) == 32 and c[0] == (0, 32, 0, 0) and c[1] == (0x80, 32, 4096 - 128, 0)
    assert completions_of(rx(addr=0x40, lfield=1, fbe=0)) == [(0x40, 1, 1, 0x40)]
    # 33 DWs at 0x7C: the highest boundary within 128 bytes is 0xC0.
    assert completions_of(rx(addr=0x7C, lfield=33, lbe=0xF)) == [(0x7C, 17, 132, 0x7C),
                                                                 (0xC0, 16, 64, 0x40)]


def test_host_memory_has_no_initial_contents():
    """INBOUND.md, Host memory: a byte never written is unknown, so no check
    compares it; written bytes are known, lane by lane."""
    t = RcTl(Params())
    t.step(Inputs(rst=1))
    t.step(Inputs(cmd_valid=1, cmd=Cmd(HOST_WR, 0x10, first_be=0b0011, data=0x1234_5678)))
    beats = [t.step(Inputs())["beat"] for _ in range(3)]
    assert [b for b in beats if b is not None] == [Rsp(OK)] and t.known[4] == 0x0000_FFFF
    t.step(Inputs(cmd_valid=1, cmd=Cmd(HOST_RD, 0x10)))
    beat = [t.step(Inputs())["beat"] for _ in range(3)]
    got = [b for b in beat if b is not None][0]
    assert got == Rsp(OK, 0x5678) and got.known == 0x0000_FFFF
