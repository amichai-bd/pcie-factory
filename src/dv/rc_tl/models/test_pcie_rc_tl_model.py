"""Unit tests of the rc_tl front-end model against hand-worked spec cases
(wiki/src/rtl/rc_tl/MAS.md#commands, REQUESTER.md#byte-enables,
wiki/src/arch/rc/HAS.md#type-1-header). No simulator."""

from pcie_rc_tl_model import (CFG_RD, CFG_WR, ERR_CLR, ERR_RD, HDR_RD, HDR_WR, HOST_RD,
                              HOST_WR, LINK_DOWN, LOCAL_UR, MEM_RD, MEM_WR, OK, Cmd,
                              FrontEnd, Params, Rsp, failed_check)


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


def test_interim_results():
    fe = fe_open()
    assert fe.execute(Cmd(MEM_WR, 0x1000_0000), 1) == Rsp(LOCAL_UR)
    assert fe.execute(Cmd(MEM_WR, 0x1000_0000), 0) == Rsp(LINK_DOWN)
    assert fe.execute(Cmd(MEM_WR, 0x2000_0000), 0) == Rsp(LOCAL_UR)
    assert fe.execute(Cmd(HOST_RD, 0), 1) == Rsp(LOCAL_UR)
    assert fe.execute(Cmd(ERR_CLR, 0), 0) == Rsp(LOCAL_UR)


def test_cycle_model():
    fe = FrontEnd()
    rd = Cmd(HDR_RD, 0x8)
    assert fe.step(1, 1, rd, 1) == (0, None, False)
    ready, beat, take = fe.step(0, 1, rd, 1)
    assert take and ready == 0 and beat == Rsp(OK, 0x0604_0000)
    assert fe.step(0, 1, rd, 1) == (1, None, False)     # beat cycle: not taken
    ready, beat, take = fe.step(0, 1, rd, 1)
    assert take and beat is not None
    assert fe.step(1, 0, rd, 1) == (0, None, False)     # reset mid-command
    assert fe.step(0, 0, rd, 1) == (1, None, False)
