"""Self-tests of the pcie_rc architecture model; no simulator needed.

Run: python3 -m pytest src/arch/rc (./build ci unit runs them as arch:<ip>).
Each test states the HAS, TL_SEQ or sub-IP MAS rule it checks.
"""

import random

import pytest

import pcie_rc_arch as m  # puts the sibling arch models on sys.path

import pcie_cfg_arch as cfgm  # noqa: E402
import pcie_dll_arch as dll  # noqa: E402
import pcie_tl_arch as tlm  # noqa: E402

# A header after enumeration: secondary bus 1, a window of 1 MiB at
# 0x8000_0000, both enables on.
HDR = {"secondary": 1, "mem_space_en": 1, "bus_master_en": 1,
       "mem_base": 0x8000, "mem_limit": 0x8000}


# ------------------------------------------------- address map, routing

def test_memory_window():
    """1 MiB granular; empty while base is above limit (HAS, Type 1 header)."""
    assert m.memory_window(0x8000, 0x8000) == (0x8000_0000, 0x800F_FFFF)
    assert m.memory_window(0x8000, 0x8010) == (0x8000_0000, 0x801F_FFFF)
    assert m.memory_window(0x000F, 0x000F) == (0, 0xF_FFFF)   # bits 3:0 read 0
    assert m.memory_window(0x8010, 0x8000) is None


@pytest.mark.parametrize("req,dl_up,want", [
    ({"kind": "cfg", "bus": 1, "dev": 0}, True, "send"),
    ({"kind": "cfg", "bus": 1, "dev": 1}, True, "LOCAL_UR"),
    ({"kind": "cfg", "bus": 2, "dev": 0}, True, "LOCAL_UR"),
    ({"kind": "cfg", "bus": 1, "dev": 0}, False, "LINK_DOWN"),
    ({"kind": "cfg", "bus": 2, "dev": 1}, False, "LINK_DOWN"),             # LINK_DOWN first
    ({"kind": "mem", "addr": 0x8000_0FFC, "len": 2}, False, "LINK_DOWN"),  # over the 4 KiB check
    ({"kind": "mem", "addr": 0x8000_0000, "len": 32}, True, "send"),
    ({"kind": "mem", "addr": 0x800F_FFFC, "len": 1}, True, "send"),
    ({"kind": "mem", "addr": 0x800F_FFFC, "len": 2}, True, "LOCAL_UR"),   # leaves the window
    ({"kind": "mem", "addr": 0x8000_0FFC, "len": 2}, True, "LOCAL_UR"),   # crosses 4 KiB
    ({"kind": "mem", "addr": 0x7FFF_FFFC, "len": 1}, True, "LOCAL_UR"),
    ({"kind": "mem", "addr": 0x8000_0000, "len": 33}, True, "LOCAL_UR"),
    ({"kind": "mem", "write": True, "addr": 0x8000_0000, "len": 1}, True, "send"),
    ({"kind": "mem", "write": True, "addr": 0x8000_0000, "len": 2}, True, "LOCAL_UR"),  # a write is 1 DW
])
def test_outbound(req, dl_up, want):
    """HAS, Outbound routing."""
    assert m.outbound(req, HDR, dl_up) == want


def test_outbound_needs_memory_space_enable():
    hdr = dict(HDR, mem_space_en=0)
    assert m.outbound({"kind": "mem", "addr": 0x8000_0000, "len": 1}, hdr, True) == "LOCAL_UR"


@pytest.mark.parametrize("req,want", [
    ({"write": True, "addr": 0x0000_0100, "len": 32}, "host"),
    ({"write": False, "addr": 0x0000_0FFC, "len": 1}, "host"),
    ({"write": True, "addr": 0x0000_0FFC, "len": 2}, "ur"),             # past host memory
    ({"write": True, "addr": 0xFEE0_0010, "len": 1}, "msi"),
    ({"write": True, "addr": 0xFEE0_0010, "len": 2}, "ur"),             # an MSI is 1 DW
    ({"write": False, "addr": 0xFEE0_0010, "len": 1}, "ur"),
    ({"write": True, "addr": 0x8000_0000, "len": 1}, "ur"),             # the window claims it
    ({"write": True, "addr": 0x1_0000_0000, "len": 1}, "ur"),           # above 4 GiB
])
def test_inbound(req, want):
    """HAS, Inbound routing."""
    assert m.inbound(req, HDR) == want


def test_inbound_needs_bus_master_enable():
    hdr = dict(HDR, bus_master_en=0)
    assert m.inbound({"write": True, "addr": 0xFEE0_0000, "len": 1}, hdr) == "ur"
    assert m.inbound({"write": True, "addr": 0x100, "len": 1}, hdr) == "ur"


def test_window_wins_over_host_memory():
    """A window that covers host memory claims its requests: Unsupported
    Request (rows in order)."""
    hdr = dict(HDR, mem_base=0x0000, mem_limit=0x0000)
    assert m.inbound({"write": True, "addr": 0x100, "len": 1}, hdr) == "ur"


def test_overlap_is_measured():
    d = m.perf({"kind": "address_map", "host_mem_base": 0xFEE0_0000}, 1)
    assert d["map_overlap_bytes"] == 4096


@pytest.mark.parametrize("over", [
    {"host_mem_addr_w": 7}, {"host_mem_addr_w": 17}, {"host_mem_base": 0x800},
    {"msi_addr": 0xFEE0_0004}, {"vendor_id": 0xFFFF}, {"fc_ph": 0}, {"fc_pd": 7},
    {"cpl_timeout_div": 1025}, {"other": 1},
])
def test_bad_parameters_raise(over):
    """HAS, Parameters: ranges."""
    with pytest.raises(ValueError):
        m.params_of(over)


# ---------------------------------------------------------------- header

def test_header_reset_and_access():
    """HAS, Type 1 header: reset values, RW fields take writes by byte,
    RO fields and indices past the header ignore them."""
    h = m.Header(m.params_of({"vendor_id": 0x1234, "device_id": 0x5678, "revision_id": 9}))
    assert [h.read(i) for i in range(16)] == [
        0x5678_1234, 0, 0x0604_0009, 0x0001_0000] + [0] * 12
    for i in range(32):
        h.write(i, 0xFFFF_FFFF, 0xF)
    want = {0: 0x5678_1234, 1: 0x0000_0006, 2: 0x0604_0009, 3: 0x0001_00FF,
            6: 0x00FF_FFFF, 8: 0xFFF0_FFF0}
    assert [h.read(i) for i in range(32)] == [want.get(i, 0) for i in range(32)]
    h.write(8, 0x1000_1000, 0x3)                        # bytes 0 and 1 only
    assert h.read(8) == 0xFFF0_1000
    assert h.state["mem_base"] == 0x1000 and h.state["mem_limit"] == 0xFFF0


# -------------------------------------------------------- command checks

ST = {"mem_space_en": 1, "bus_master_en": 1, "primary": 0, "secondary": 1,
      "mem_base": 0x1000, "mem_limit": 0x1000}


def cmd(op, addr=0, n=1, fbe=0xF, lbe=None, data=0):
    return {"op": op, "addr": addr, "len": n, "fbe": fbe,
            "lbe": (0xF if n > 1 else 0) if lbe is None else lbe, "data": data}


@pytest.mark.parametrize("c,dl_up,want", [
    (cmd(m.CFG_RD, m.cfg_addr(1, 0, 0, 0)), True, None),
    (cmd(m.CFG_RD, m.cfg_addr(1, 0, 3, 0x100)), True, None),          # any function, ext reg
    (cmd(m.CFG_RD, m.cfg_addr(1, 0, 0, 0)), False, m.LINK_DOWN),
    (cmd(12, 0), False, m.LOCAL_UR),                                   # not an endpoint op
    (cmd(m.HDR_RD, 0), False, None),                                   # local: link irrelevant
    (cmd(m.MEM_RD, 0x1000_0000, 33), False, m.LINK_DOWN),              # check 1 first
    (cmd(10), True, m.LOCAL_UR),                                       # 2: op
    (cmd(m.MEM_WR, 0x1000_0000, 2), True, m.LOCAL_UR),                 # 3: len
    (cmd(m.HDR_RD, 0, 0), True, m.LOCAL_UR),
    (cmd(m.CFG_RD, 1 << 28 | m.cfg_addr(1, 0, 0, 0)), True, m.LOCAL_UR),  # 4: addr bits
    (cmd(m.MEM_RD, 0x1000_0002), True, m.LOCAL_UR),
    (cmd(m.HDR_WR, 0x1000), True, m.LOCAL_UR),
    (cmd(m.HOST_RD, 0x1000), True, m.LOCAL_UR),                        # bit 12 at W 12
    (cmd(m.HOST_RD, 0xFFC), True, None),
    (cmd(m.ERR_RD, 0xFFFF_FFFF), True, None),                          # ERR_*: addr ignored
    (cmd(m.MEM_RD, 0x1000_0FF8, 4), True, m.LOCAL_UR),                 # 5: 4 KiB
    (cmd(m.MEM_RD, 0x1000_0FF0, 4), True, None),
    (cmd(m.CFG_WR, m.cfg_addr(1, 0, 0, 0), fbe=0), True, m.LOCAL_UR),  # 6: byte enables
    (cmd(m.MEM_WR, 0x1000_0000, fbe=0), True, m.LOCAL_UR),
    (cmd(m.MEM_RD, 0x1000_0000, fbe=0), True, None),                   # zero-length read
    (cmd(m.MEM_RD, 0x1000_0000, lbe=1), True, m.LOCAL_UR),             # len 1: last_be 0
    (cmd(m.MEM_RD, 0x1000_0000, 3, fbe=0x5), True, m.LOCAL_UR),        # not contiguous
    (cmd(m.MEM_RD, 0x1000_0000, 2, fbe=0x5, lbe=0x2), True, None),     # one aligned QW
    (cmd(m.HOST_WR, 0, fbe=0), True, None),
    (cmd(m.CFG_RD, m.cfg_addr(2, 0, 0, 0)), True, m.LOCAL_UR),         # 7: routing
    (cmd(m.CFG_RD, m.cfg_addr(1, 1, 0, 0)), True, m.LOCAL_UR),
    (cmd(m.MEM_RD, 0x2000_0000), True, m.LOCAL_UR),
])
def test_check_cmd(c, dl_up, want):
    """rc_tl MAS, Commands: checks 1 to 7 in order; REQUESTER, Byte enables."""
    assert m.check_cmd(c, ST, dl_up, 12) == want


def test_check_cmd_needs_memory_space_enable():
    assert m.check_cmd(cmd(m.MEM_RD, 0x1000_0000), dict(ST, mem_space_en=0), True, 12) == m.LOCAL_UR


# --------------------------------------------------- rc_tl, standalone

class FakeLink:
    """An up link with infinite credits: records what rc_tl sends and the
    releases; the test delivers TLPs."""

    def __init__(self):
        self.dl_up, self.delivered, self.sent, self.released = True, [], [], []

    def avail(self, cls, f):
        return dll.MODS[f] - 1

    def offer(self, tlp, now):
        self.sent.append(tlp)
        return True

    def release(self, cls, data):
        self.released.append((cls, data))


def make_tl(**over):
    d = FakeLink()
    t = m.RcTl(m.params_of(over), d)
    t.hdr.write(6, 0x0001_0100, 0x7)
    t.hdr.write(8, 0x1000_1000, 0xF)
    t.hdr.write(1, 0x6, 0x1)
    return t, d


def run(t, cycles, now=0):
    for k in range(now, now + cycles):
        t.step(k)
    return now + cycles


def drain(t, now, limit=100):
    """Every result beat until the last: [(status, data)]."""
    out = []
    for k in range(now, now + limit):
        t.step(k)
        b = t.pop_rsp(k)
        if b is not None:
            out.append(b[:2])
            if b[2]:
                return out, k + 1
    raise AssertionError("no last beat")


def send_req(t, d, c, now=0):
    """Offer an endpoint command and step until its TLP is sent."""
    assert t.offer(c, now)
    for k in range(now + 1, now + 10):
        t.step(k)
        if d.sent:
            return tlm.parse(d.sent[-1]), k + 1
    raise AssertionError("request not sent")


def test_request_formats_and_tag_rolls():
    """REQUESTER, Requests: a Type 0 configuration read with Requester ID
    {Primary, 0, 0}, sent from the third cycle after the take; the 5-bit
    tag steps per non-posted request and wraps at 32."""
    t, d = make_tl()
    t.hdr.write(6, 0x0001_0105, 0x7)                    # primary bus 5
    h, now = send_req(t, d, cmd(m.CFG_RD, m.cfg_addr(1, 0, 2, 0x44)))
    assert (h["type"], h["bus"], h["dev"], h["fn"], h["reg"], h["rid"], h["tag"]) == \
        (tlm.T_CFG0, 1, 0, 2, 0x44 >> 2, 0x0500, 0)
    assert now == m.REQ_TX_DELAY + 1
    tags = [0]
    for _ in range(33):
        t.req = None
        t.busy = False
        d.sent.clear()
        h, now = send_req(t, d, cmd(m.MEM_RD, 0x1000_0000), now)
        tags.append(h["tag"])
    assert tags == [k % 32 for k in range(34)]


def cpl(t, status=tlm.SC, data=b"", bc=4, la=0, tag=None, rid=None, ep=0, locked=False):
    return tlm.cpl_tlp(0x0100, status, bc, t.rid if rid is None else rid,
                       t.req.tag if tag is None else tag, la, data, locked=locked, ep=ep)


@pytest.mark.parametrize("op,status,ep,want,bit", [
    (m.CFG_RD, tlm.CRS, 0, m.CRS, m.E_CRS),
    (m.MEM_RD, tlm.CRS, 0, m.BAD, m.E_MALFORMED),           # only cfg may be retried
    (m.MEM_RD, tlm.UR, 0, m.UR, m.E_UR),
    (m.CFG_WR, tlm.CA, 0, m.CA, m.E_CA),
    (m.MEM_RD, 3, 0, m.BAD, m.E_MALFORMED),                 # reserved status
    (m.CFG_RD, tlm.SC, 1, m.BAD, m.E_POISONED),
])
def test_completion_status(op, status, ep, want, bit):
    """REQUESTER, Completion matching: the status rows and their record
    bits; the request ends with one beat."""
    t, d = make_tl()
    a = m.cfg_addr(1, 0, 0, 0) if op in (m.CFG_RD, m.CFG_WR) else 0x1000_0000
    _, now = send_req(t, d, cmd(op, a))
    data = b"\1\2\3\4" if status == tlm.SC else b""
    d.delivered.append(cpl(t, status, data, ep=ep))
    beats, _ = drain(t, now)
    assert beats == [(want, 0)] and t.err == 1 << bit


def test_unexpected_completions():
    """REQUESTER, Completion matching: wrong tag, wrong Requester ID, a
    locked completion, or no request: dropped, bit 4, request goes on."""
    t, d = make_tl()
    d.delivered.append(tlm.cpl_tlp(0, tlm.SC, 4, 0, 0, 0, b"\0" * 4))   # nothing outstanding
    _, now = send_req(t, d, cmd(m.CFG_RD, m.cfg_addr(1, 0, 0, 0)))
    good = t.req.tag
    d.delivered += [cpl(t, data=b"\0" * 4, tag=good ^ 1), cpl(t, data=b"\0" * 4, rid=0x0100),
                    cpl(t, data=b"\0" * 4, locked=True)]
    now = run(t, 3, now)
    assert t.events[m.E_UNEXPECTED] == 4 and t.req is not None
    d.delivered.append(cpl(t, data=bytes([0xEF, 0xBE, 0xAD, 0xDE])))
    assert drain(t, now)[0] == [(m.OK, 0xDEAD_BEEF)]
    assert t.err == 1 << m.E_UNEXPECTED


def read_cpls(t, tag, parts):
    """Completions for a read with ``tag``: (data DWs, bc, la) each."""
    return [cpl(t, data=bytes(range(4 * n)), bc=bc, la=la, tag=tag) for n, bc, la in parts]


@pytest.mark.parametrize("parts,want", [
    ([(32, 128, 0)], [m.OK] * 32),                                     # one completion
    ([(16, 128, 0), (16, 64, 0x40)], [m.OK] * 32),                     # split on 64 bytes
    ([(8, 128, 0), (24, 96, 0x20)], [m.BAD]),                          # ends off a boundary
    ([(16, 120, 0)], [m.BAD]),                                         # wrong Byte Count
    ([(16, 128, 4)], [m.BAD]),                                         # wrong Lower Address
    ([(16, 128, 0), (17, 64, 0x40)], [m.OK] * 16 + [m.BAD]),           # data past the end
])
def test_read_completions(parts, want):
    """REQUESTER, Completion matching: a 32-DW read's DWs in address order
    over one or several completions; malformed ones end it with BAD (its
    data not delivered); later ones are then unexpected."""
    t, d = make_tl()
    h, now = send_req(t, d, cmd(m.MEM_RD, 0x1000_0000, 32))
    d.delivered += read_cpls(t, h["tag"], parts)
    beats, now = drain(t, now)
    assert [s for s, _ in beats] == want
    if want[-1] == m.BAD:                   # and bit 4 for any completion after it
        assert t.err & ~(1 << m.E_UNEXPECTED) == 1 << m.E_MALFORMED


def test_split_read_failure_leaves_the_rest_unexpected():
    t, d = make_tl()
    h, now = send_req(t, d, cmd(m.MEM_RD, 0x1000_0000, 32))
    d.delivered += read_cpls(t, h["tag"], [(8, 120, 0)])
    beats, now = drain(t, now)
    d.delivered += read_cpls(t, h["tag"], [(16, 96, 0x20)])
    run(t, 3, now)
    assert beats == [(m.BAD, 0)] and t.events[m.E_UNEXPECTED] == 1


def test_mem_write_result_follows_eop():
    """REQUESTER, Requests: MEM_WR finishes OK in the cycle after link
    takes its eop (4 beats: 3 header DWs and the data DW)."""
    t, d = make_tl()
    t.offer(cmd(m.MEM_WR, 0x1000_0004, data=0x1122_3344, fbe=0x6), 0)
    beats, end = drain(t, 1)
    h = tlm.parse(d.sent[0])
    assert (h["fmt"], h["addr"], h["fbe"], h["data"]) == (2, 0x1000_0004, 0x6,
                                                          bytes([0x44, 0x33, 0x22, 0x11]))
    assert beats == [(m.OK, 0)] and end - 1 == m.REQ_TX_DELAY + 4


@pytest.mark.parametrize("div,n", [(1024, 2048), (512, 4096), (3, 699050)])
def test_completion_timeout(div, n):
    """REQUESTER, Completion timeout: N = floor(2^21 / div) cycles after
    the sop is taken, the request ends with TIMEOUT and bit 3, its beat in
    the next cycle; a completion after it is unexpected."""
    t, d = make_tl(cpl_timeout_div=div)
    _, now = send_req(t, d, cmd(m.MEM_RD, 0x1000_0000))
    sent = t.req.sent
    t.step(sent + n - 1)
    assert t.req is not None
    t.step(sent + n)
    assert t.pop_rsp(sent + n + 1) == (m.TIMEOUT, 0, True)
    assert t.err == 1 << m.E_TIMEOUT
    d.delivered.append(tlm.cpl_tlp(0, tlm.SC, 4, t.rid, 0, 0, b"\0" * 4))
    t.step(sent + n + 2)
    assert t.events[m.E_UNEXPECTED] == 1


def test_link_down_ends_the_request_and_refuses_commands():
    """HAS, Link down: an outstanding request ends LINK_DOWN; endpoint
    commands while down end LINK_DOWN at once; local ones still work."""
    t, d = make_tl()
    _, now = send_req(t, d, cmd(m.MEM_RD, 0x1000_0000))
    d.dl_up = False
    beats, now = drain(t, now)
    assert beats == [(m.LINK_DOWN, 0)]
    t.offer(cmd(m.CFG_WR, m.cfg_addr(1, 0, 0, 0)), now)
    assert drain(t, now + 1)[0] == [(m.LINK_DOWN, 0)]
    assert t.offer(cmd(m.HOST_WR, 0x10, data=7), now + 5)
    assert drain(t, now + 6)[0] == [(m.OK, 0)]
    assert t.offer(cmd(m.HOST_RD, 0x10), now + 9)
    assert drain(t, now + 10)[0] == [(m.OK, 7)]


def test_one_command_at_a_time():
    """rc_tl MAS, Commands: cmd_ready again only in the cycle after the
    last beat."""
    t, d = make_tl()
    assert t.offer(cmd(m.ERR_RD), 0)
    assert not t.offer(cmd(m.ERR_RD), 1)
    assert t.pop_rsp(1) == (m.OK, 0, True)
    assert not t.offer(cmd(m.ERR_RD), 1)
    assert t.offer(cmd(m.ERR_RD), 2)


# ------------------------------------------------------ rc_tl inbound

def mwr(addr, dws, fbe=0xF, lbe=None, ep=0, rid=0x0100):
    data = b"".join(x.to_bytes(4, "little") for x in dws)
    lbe = (0xF if len(dws) > 1 else 0) if lbe is None else lbe
    return tlm.mem_tlp(True, addr, len(dws), fbe, lbe, rid, 0, data, ep=ep)


def deliver(t, d, *tlps, now=0, cycles=4):
    d.delivered += tlps
    return run(t, cycles, now)


def test_dma_write_and_read_of_host_memory():
    """HAS, Host memory and Inbound routing: a write stored with its byte
    enables; a read answered from host memory with its byte enables
    applied, Completer ID {Primary, 0, 0}, Byte Count and Lower Address."""
    t, d = make_tl()
    deliver(t, d, mwr(0x100, [0x1111_1111, 0x2222_2222, 0x3333_3333], fbe=0xC, lbe=0x3))
    assert t.mem[0x100:0x10C] == bytes([0, 0, 0x11, 0x11, 0x22, 0x22, 0x22, 0x22, 0x33, 0x33,
                                        0, 0])
    d.delivered.append(tlm.mem_tlp(False, 0x104, 2, 0xE, 0x1, 0x0100, 7))
    run(t, 10, 4)
    h = tlm.parse(d.sent[-1])
    assert (h["type"], h["status"], h["cid"], h["rid"], h["tag"], h["bc"], h["la"]) == \
        (tlm.T_CPL, tlm.SC, 0, 0x0100, 7, 4, 0x05)
    assert h["data"] == bytes([0, 0x22, 0x22, 0x22, 0x33, 0, 0, 0])
    assert d.released == [(dll.P, 1), (dll.NP, 0)]


@pytest.mark.parametrize("tlp,bit", [
    (mwr(0x1000_0000, [1]), m.E_INBOUND_UR),                 # the window claims it
    (mwr(0x2000_0000, [1]), m.E_INBOUND_UR),                 # nowhere
    (mwr(0xFEE0_0000, [1, 2]), m.E_INBOUND_UR),              # an MSI is 1 DW
    (mwr(0x100, [1], ep=1), m.E_POISONED),
    (mwr(0x2000_0000, [1], ep=1), m.E_INBOUND_UR),           # UR outranks poisoned
    (mwr(0xFFC, [1, 2]), m.E_MALFORMED),                     # crosses 4 KiB
    (mwr(0x100, [1], lbe=0xF), m.E_MALFORMED),               # 1 DW with Last BE
    (tlm.msg_tlp(0x30, tlm.R_TO_RC, 0x0100), m.E_COR),
    (tlm.msg_tlp(0x31, tlm.R_TO_RC, 0x0100), m.E_NONFATAL),
    (tlm.msg_tlp(0x33, tlm.R_TO_RC, 0x0100), m.E_FATAL),
    (tlm.msg_tlp(0x20, tlm.R_LOCAL, 0x0100), m.E_MSG),
    (tlm.msg_tlp(0x30, tlm.R_TO_RC, 0x0100, ep=1), m.E_POISONED),
])
def test_posted_inbound_events(tlp, bit):
    """INBOUND, Receive checks and Credits: each posted TLP sets one error
    record bit and releases its posted credits in the next cycle."""
    t, d = make_tl()
    deliver(t, d, tlp)
    assert t.err == 1 << bit
    assert d.released == [(dll.P, tlm.credits(tlp)[2])]
    assert not t.msi and not d.sent


@pytest.mark.parametrize("tlp,bit,status,bc", [
    (tlm.mem_tlp(False, 0x2000_0000, 2, 0xF, 0xF, 0x0100, 3), m.E_INBOUND_UR, tlm.UR, 8),
    (tlm.mem_tlp(False, 0xFEE0_0000, 1, 0xF, 0, 0x0100, 3), m.E_INBOUND_UR, tlm.UR, 4),
    (tlm.mem_tlp(False, 0x100, 1, 0x6, 0, 0x0100, 3, ep=1), m.E_POISONED, tlm.UR, 2),
    (tlm.cfg_tlp(False, 0, 0, 0, 0, 0xF, 0x0100, 3), m.E_INBOUND_UR, tlm.UR, 4),
    (tlm.mem_tlp(False, 0xFF8, 4, 0xF, 0xF, 0x0100, 3), m.E_MALFORMED, None, 0),
])
def test_nonposted_inbound(tlp, bit, status, bc):
    """INBOUND, Completer: a refused read becomes one Unsupported Request
    completion (Byte Count T for a memory read); a poisoned one that would
    be served too, bit 5 only; a malformed one is dropped unanswered; each
    releases its non-posted credits when the completer takes it."""
    t, d = make_tl()
    deliver(t, d, tlp, cycles=6)
    assert t.err == 1 << bit
    assert d.released == [(dll.NP, tlm.credits(tlp)[2])]
    if status is None:
        assert not d.sent
    else:
        h = tlm.parse(d.sent[0])
        assert (h["status"], h["bc"], h["tag"], len(h["data"])) == (status, bc, 3, 0)


def test_msi_capture_and_overflow():
    """HAS, MSI window; INBOUND, MSI capture: 4 captures in arrival order,
    the 5th dropped with bit 12; none while Bus Master Enable is 0."""
    t, d = make_tl()
    deliver(t, d, *(mwr(0xFEE0_0000 + 4 * k, [0x100 + k]) for k in range(5)))
    assert t.err == 1 << m.E_MSI_DROP
    assert [t.take_msi() for _ in range(5)] == [(k, 0x100 + k) for k in range(4)] + [None]
    t.hdr.write(1, 0x2, 0x1)
    deliver(t, d, mwr(0xFEE0_0000, [1]), now=10)
    assert not t.msi and t.err >> m.E_INBOUND_UR & 1


# ---------------------------------------------------------------- rc_seq

def test_step_encoding():
    """rc_seq MAS, Step format: field positions; FAIL is word 0."""
    w = m.encode(op=0x12, status=5, chk=1, cond=3, idx=31, len=32, fbe=0xA, lbe=0x5,
                 addr=0x1234_5678, data=0x9ABC_DEF0, mask=0x0F0F_0F0F)
    assert w >> 122 == 0x12 and w >> 119 & 7 == 5 and w >> 118 & 1 and w >> 110 & 31 == 31
    assert w >> 104 & 63 == 32 and w >> 96 & 0xFF == 0xA5 and w >> 64 & m.FULL == 0x1234_5678
    assert m.decode(w)["data"] == 0x9ABC_DEF0 and w & m.FULL == 0x0F0F_0F0F
    assert w < 1 << 127 and m.fail() == 0
    with pytest.raises(ValueError):
        m.encode(op=32)


class StubTl:
    """A stand-in for rc_tl (like the rc_seq testbench's): answers each
    command with the beats ``answer(cmd)`` gives, one per cycle."""

    def __init__(self, answer):
        self.answer, self.cmds, self.msi, self.beats = answer, [], [], []

    def offer(self, c, now):
        if self.beats:
            return False
        self.cmds.append(c)
        out = self.answer(c)
        self.beats = [(s, dt, i == len(out) - 1) for i, (s, dt) in enumerate(out)]
        return True

    def pop_rsp(self, now):
        return self.beats.pop(0) if self.beats else None

    def take_msi(self):
        return self.msi.pop(0)


def run_seq(rom, answer=lambda c: [(m.OK, 0)], dl_up=True, limit=10_000, tl=None):
    s, tl = m.RcSeq(rom), tl or StubTl(answer)
    s.start()
    for now in range(limit):
        s.step(now, tl, dl_up, True)
        if s.done:
            return s, tl
    raise AssertionError("run did not end")


def test_expect_and_registers():
    """rc_seq MAS, Result registers and EXPECT: a MEM_RD's beats fill R[0]
    to R[len-1]; a masked mismatch fails at that step."""
    rom = [m.req(m.MEM_RD, 0x1000_0000, 3), m.expect(10, idx=0), m.expect(12, idx=2),
           m.expect(0x10, 0x10, idx=1), m.done()]
    s, _ = run_seq(rom, lambda c: [(m.OK, 10), (m.OK, 0x31), (m.OK, 12)])
    assert s.passed and s.R[:3] == [10, 0x31, 12]
    rom[3] = m.expect(0x20, 0x30, idx=1)
    s, _ = run_seq(rom, lambda c: [(m.OK, 10), (m.OK, 0x31), (m.OK, 12)])
    assert (s.passed, s.fail_step) == (False, 3)


def test_req_status_check():
    """REQ with chk fails unless the last beat's status matches; chk 0
    never fails and sets S."""
    s, _ = run_seq([m.req(m.ERR_RD, 0), m.done()], lambda c: [(m.LOCAL_UR, 0)])
    assert (s.passed, s.fail_step) == (False, 0)
    s, _ = run_seq([m.req(m.ERR_RD, 0, chk=False), m.jump(m.STATUS_EQ, 3, status=m.LOCAL_UR),
                    m.fail(), m.done()], lambda c: [(m.LOCAL_UR, 0)])
    assert s.passed


def test_padding_and_reserved_ops_fail():
    """FAIL pads the ROM; reserved ops fail; a program running off its end
    fails at the first padded index."""
    s, _ = run_seq([m.req(m.ERR_RD, 0)])
    assert (s.passed, s.fail_step) == (False, 1)
    s, _ = run_seq([m.encode(op=7)])
    assert (s.passed, s.fail_step) == (False, 0)
    s, _ = run_seq([m.jump(5, 0)])                      # cond 5 is reserved
    assert (s.passed, s.fail_step) == (False, 0)


def test_polling_loop_limit():
    """JUMP: a backward conditional take increments L; with L at the limit
    the step fails. A forward take or a not-taken jump sets L to 0."""
    n = [0]

    def count(c):
        n[0] += 1
        return [(m.OK, n[0])]

    rom = [m.req(m.ERR_RD, 0), m.jump(m.DATA_NE, 0, limit=5, value=4), m.done()]
    s, _ = run_seq(rom, count)
    assert s.passed and n[0] == 4                       # three backward takes
    n[0] = 0
    rom[1] = m.jump(m.DATA_NE, 0, limit=2, value=4)
    s, _ = run_seq(rom, count)
    assert (s.passed, s.fail_step, n[0]) == (False, 1, 3)
    # A forward skip inside the loop body resets L each pass: the loop is
    # then unbounded by its limit (the MAS forbids such a loop); here it
    # ends by data.
    n[0] = 0
    rom = [m.req(m.ERR_RD, 0), m.jump(m.DATA_EQ, 2, value=99), m.jump(m.DATA_NE, 0, limit=1,
                                                                        value=6), m.done()]
    s, _ = run_seq(rom, count)
    assert s.passed and n[0] == 6


def test_waits_time_out():
    """WAIT_MSI and WAIT_LINK fail after T + 1 cycles; HOT_RESET fails when
    link_up does not fall."""
    for word in (m.wait_msi(20), m.wait_link(20)):
        s, _ = run_seq([word, m.done()], dl_up=False)
        assert (s.passed, s.fail_step) == (False, 0)
        assert 20 < s.trace[-1][3] - s.trace[-1][2] < 30
    s, _ = run_seq([m.hot_reset(20), m.done()])
    assert (s.passed, s.fail_step, s.hot_reset_req) == (False, 0, False)


def test_wait_msi_takes_the_capture():
    tl = StubTl(lambda c: [(m.OK, 0)])
    tl.msi = [(5, 0xAB)]
    s, _ = run_seq([m.wait_msi(10), m.expect(0xAB), m.expect(5, idx=1), m.done()], tl=tl)
    assert s.passed and not tl.msi


def test_unwritten_register_is_a_program_error():
    with pytest.raises(RuntimeError):
        run_seq([m.expect(0), m.done()])


def test_programs_fit_the_rom():
    """rc_seq MAS, Programs: every M4 program fits 256 steps; ERRORS'
    jump targets resolve to its sections."""
    for name in m.PROGRAMS:
        a = m.program(name)
        assert 60 < len(a.rom()) <= m.ROM_STEPS
    a = m.program("ERRORS")
    rom = a.rom()
    r2 = a.sections["R2"][0]
    assert m.decode(rom[r2 + 1])["addr"] & 0xFFFF == a.labels["R11"]
    assert m.decode(rom[r2 + 2])["addr"] & 0xFFFF == a.labels["R14"]


# ------------------------------------------------ e2e: rc, link, core

def e2e(name, ep=None, patch=None, **over):
    rc = m.params_of(over)
    a = m.program(name, rc, ep)
    b = m.Bench(random.Random(1), a.rom(), rc)
    if patch:
        patch(b)
    b.run(200_000)
    return b, a


def test_enumeration_fails_on_a_wrong_id():
    """Program ENUM against core: an expected Vendor ID other than core's
    fails at the first E4 EXPECT."""
    b, a = e2e("ENUM", ep=cfgm.Params(vendor_id=0x1AB4))
    assert (b.seq.passed, b.seq.fail_step) == (False, a.sections["E4"][0] + 1)


def test_mmio_fails_on_corrupted_bar0():
    """Program MMIO: a BAR0 DW changed behind the program fails M2 at the
    EXPECT of that DW."""
    def patch(b):
        rd = b.app._rd
        b.app._rd = lambda off: rd(off) ^ (1 if off == 0x14 else 0)
    b, a = e2e("MMIO", patch=patch)
    assert (b.seq.passed, b.seq.fail_step) == (False, a.sections["M2"][0] + 1 + 5)


def test_dma_fails_on_a_lost_write():
    """Program DMA_MSI: a DMA write that never reaches host memory fails
    D4 at its first EXPECT."""
    def patch(b):
        b.tl._host_write = lambda h: None
    b, a = e2e("DMA_MSI", patch=patch)
    assert (b.seq.passed, b.seq.fail_step) == (False, a.sections["D4"][0] + 1)


def test_bus_master_disabled_blocks_dma():
    """Program DMA_MSI with the root port's Bus Master Enable cleared:
    core's read is an Unsupported Request, so no MSI comes and the run
    fails at WAIT_MSI (short timeout)."""
    rc = m.params_of()
    a = m.program("DMA_MSI", rc)
    rom = a.rom()
    e1 = a.sections["E1"][0]
    rom[e1 + 2] = m.req(m.HDR_WR, 4, data=0x2, fbe=0x1)            # MSE only
    d3 = a.sections["D3"][0]
    rom[d3] = m.wait_msi(3000)
    rom[a.sections["E2"][0] + 3] = m.expect(0x2)                   # the Command readback
    b = m.Bench(random.Random(1), rom, rc)
    b.run(200_000)
    assert (b.seq.passed, b.seq.fail_step) == (False, d3)
    assert b.tl.err >> m.E_INBOUND_UR & 1 and b.app.copies["failed"] == 1


def test_credit_releases_balance():
    """HAS, Credits: after DMA_MSI every posted and non-posted credit the
    endpoint used has come back to rc's dll."""
    b, _ = e2e("DMA_MSI")
    for _ in range(10):
        b.step()
    assert b.seq.passed
    assert b.a.held == [[0, 0], [0, 0], [0, 0]]


# ------------------------------------------------------------- targets

@pytest.mark.parametrize("name", ["ENUM", "MMIO", "DMA_MSI"])
def test_program_targets(name):
    """HAS, Targets: each program passes end to end against core."""
    d = m.perf({"kind": "program", "program": name}, 1)
    assert d["run_passed"] == 1 and d["fail_step"] == 0 and d["unexpected_completions"] == 0
    assert d["enumeration_cycles"] > 0


def test_errors_target():
    """HAS, Targets, errors and recovery: ERRORS passes with one timeout of
    2048 cycles at CPL_TIMEOUT_DIV 1024, three enumerations, a Hot Reset
    and an injected link down."""
    d = m.perf({"kind": "program", "program": "ERRORS", "cpl_timeout_div": 1024}, 1)
    assert d["run_passed"] == 1
    assert (d["timeouts"], d["timeout_cycles"], d["enumerations"], d["hot_resets"],
            d["link_downs"], d["msi_dropped"], d["inbound_ur"]) == (1, 2048, 3, 1, 2, 1, 1)


def test_errors_needs_the_divider():
    """At CPL_TIMEOUT_DIV 1 the R9 read would wait 16.8 ms: the run is cut
    by the cycle limit, so a scenario cannot pass by luck."""
    with pytest.raises(RuntimeError):
        m.perf({"kind": "program", "program": "ERRORS", "max_cycles": 50_000}, 1)


def test_completion_timeout_target():
    """HAS, Targets: at CPL_TIMEOUT_DIV 1 the timeout is 2^21 cycles,
    16.8 ms at 125 MHz and 41.9 ms at 50 MHz, inside 50 us to 50 ms."""
    d = m.perf({"kind": "completion_timeout", "reads": 2}, 1)
    assert d["timeout_cycles_min"] == d["timeout_cycles_max"] == 1 << 21
    assert 50 <= d["timeout_us_min"] <= d["timeout_us_max"] <= 50_000
    assert d["tags_used"] == 2 and d["run_passed"] == 1
    d = m.perf({"kind": "completion_timeout", "clk_mhz": 50}, 1)
    assert 41_900 < d["timeout_us_max"] < 42_000


def test_targets_address_map():
    """HAS, Targets: the address_map scenario."""
    d = m.perf({"kind": "address_map"}, 1)
    assert d == {"host_mem_bytes": 4096, "map_overlap_bytes": 0, "map_top_byte": 0xFEE0_0FFF}


def test_deterministic():
    w = {"kind": "program", "program": "MMIO"}
    assert m.perf(w, 3) == m.perf(w, 3)


@pytest.mark.parametrize("w", [
    {"kind": "traffic"},
    {"kind": "address_map", "host_mem_addr_w": 7},
    {"kind": "address_map", "other": 1},
    {"kind": "program", "program": "BOOT"},
    {"kind": "program", "program": "ENUM", "other": 1},
    {"kind": "program", "program": "DMA_MSI", "dma_bytes": 6},
    {"kind": "program", "program": "ENUM", "fc_nph": 0},
    {"kind": "completion_timeout", "cpl_timeout_div": 0},
    {"kind": "completion_timeout", "clk_mhz": 0},
])
def test_bad_workloads_raise(w):
    with pytest.raises(ValueError):
        m.perf(w, 1)
