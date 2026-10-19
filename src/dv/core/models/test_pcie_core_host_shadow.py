"""Unit tests of the host model's passive shadow mode (no simulator).

A recorder plays the root port with the host model in active mode against
the endpoint stub of ``test_pcie_core_host_model``: every request the
active host sends becomes the sequencer command that asks for it, every
finished request becomes its result beats (in command order), every
interrupt the host logs becomes an MSI capture, and local commands
(header, host memory, error record) get the results the root-port HAS
gives them. The recorded stream is then replayed into a fresh shadow,
which must accept it, and with one seeded mismatch, which it must reject
with ``HostCheckError``.
"""

import random
from collections import deque

import pytest

from pcie_core_host_model import HostCheckError, HostModel
from pcie_core_host_shadow import (
    CFG_RD,
    CFG_WR,
    ERR_CLR,
    ERR_RD,
    HDR_RD,
    HDR_WR,
    HOST_RD,
    HOST_WR,
    MEM_RD,
    MEM_WR,
    HostShadow,
)
from pcie_core_host_tlp import CPL_SC, CPL_UR, MSG_ERR_COR, ROUTE_TO_RC, Tlp, completion, message
from test_pcie_core_host_model import EndpointStub

BDF = (1, 0, 0)
MSI_ADDR = 0xFEE0_0000
MSI_OFFSET = 0x10
WINDOW = 0x80F0_8000  # Memory Base 0x800, Limit 0x80F: 0x8000_0000 to 0x80FF_FFFF
HOST_MEM = 4096
_STATUS = {"SC": "OK", "UR": "UR", "CA": "CA", "CRS": "CRS", "poisoned": "BAD",
           "error": "BAD", "timeout": "TIMEOUT", "link_down": "LINK_DOWN"}


class Recorder:
    """The active host model playing the root port; records what a root
    port testbench would show the shadow."""

    def __init__(self, seed=1, timeout=None, **ep_kw):
        self.host = HostModel(timeout=timeout, strict=timeout is None)
        self.host.read_status = lambda addr, n: CPL_SC if addr + n <= HOST_MEM else CPL_UR
        self.host.on_interrupt = self._irq
        self.ep = EndpointStub(**ep_kw)
        self.rng = random.Random(seed)
        self.events = []
        self.order = deque()  # Request, or fixed beats [(status, data)], in command order

    # -- local commands (results from the root-port HAS) ----------------------
    def local(self, op, addr=0, data=0, first_be=0xF, result=("OK", 0)):
        self.events.append(("cmd", op, addr, 1, first_be, 0, data))
        self.order.append([result])
        self.flush()

    def header_setup(self):
        self.local(HDR_WR, 6 * 4, 0x01_01_00)  # primary 0, secondary 1, subordinate 1
        self.local(HDR_WR, 8 * 4, WINDOW)
        self.local(HDR_WR, 1 * 4, 0x6)  # Memory Space and Bus Master Enable

    def host_wr(self, offset, value):
        self.host.memory.write(offset, value.to_bytes(4, "little"))
        self.local(HOST_WR, offset, value)

    def host_rd(self, offset):
        value = int.from_bytes(self.host.memory.read(offset, 4), "little")
        self.local(HOST_RD, offset, result=("OK", value))

    def refused(self, op, addr, length=1, data=0):
        """An endpoint command the root port refuses (LOCAL_UR, nothing sent)."""
        self.events.append(("cmd", op, addr, length, 0xF, 0xF if length > 1 else 0, data))
        self.order.append([("LOCAL_UR", 0)])
        self.flush()

    # -- the pump -----------------------------------------------------------------
    def _irq(self, irq):
        self.events.append(("msi", (irq.address - MSI_ADDR) >> 2, irq.data))

    def _command_of(self, t):
        if t.kind in ("CfgRd0", "CfgWr0"):
            addr = t.cfg_bus << 20 | t.cfg_dev << 15 | t.cfg_fn << 12 | t.cfg_reg
            op = CFG_WR if t.has_data else CFG_RD
            data = int.from_bytes(t.data, "little") if t.has_data else 0
            return ("cmd", op, addr, 1, t.first_be, 0, data)
        if t.kind == "MRd":
            return ("cmd", MEM_RD, t.address, t.length_dw, t.first_be, t.last_be, 0)
        assert t.kind == "MWr" and t.length_dw == 1
        return ("cmd", MEM_WR, t.address, 1, t.first_be, 0, int.from_bytes(t.data, "little"))

    def _beats(self, req):
        st = _STATUS[req.status]
        if req.kind == "cfg_rd" and st == "OK":
            return [("OK", req.value)]
        if req.kind != "mem_rd" or st != "OK":
            return [(st, 0)]
        t = req.tlp
        out = []
        for i in range(t.length_dw):
            dw = 0
            for j in range(4):
                k = t.address + 4 * i + j - req.first_byte
                if 0 <= k < len(req.data):
                    dw |= req.data[k] << 8 * j
            out.append(("OK", dw))
        return out

    def flush(self):
        while self.order:
            head = self.order[0]
            if not isinstance(head, list):
                if not head.done:
                    return
                head = self._beats(head)
            self.order.popleft()
            for i, (st, data) in enumerate(head):
                self.events.append(("res", st, data, i == len(head) - 1))

    def step(self):
        self.events.append(("tick",))
        self.host.tick()
        moves = (["h"] if self.host.tx_pending else []) + (["e"] if self.ep.out else [])
        if not moves:
            self.flush()
            return False
        if self.rng.choice(moves) == "h":
            raw = self.host.pop_tx()
            t = Tlp.decode(raw)
            if not t.is_completion:
                self.events.append(self._command_of(t))
                self.order.append(self.host._outstanding[t.tag] if t.credit_class == "NP"
                                  else [("OK", 0)])
            self.events.append(("tx", raw))
            self.ep.handle(raw)
        else:
            raw = self.ep.out.pop(0)
            self.events.append(("rx", raw))
            self.host.receive(raw)
        self.flush()
        return True

    def run(self, proc=None, idle=0):
        for _ in range(100000):
            if not self.step() and (proc is None or proc.done):
                break
        for _ in range(idle):
            self.step()
        assert proc is None or proc.done
        if proc is not None and proc.error is not None:
            raise proc.error
        return proc.result if proc is not None else None

    def link_down(self):
        self.host.link_down()
        self.events.append(("dl", False))
        self.flush()
        self.ep = EndpointStub()
        self.events.append(("dl", True))


def record_session(seed=1, max_payload=64):
    """Enumeration, MSI, MMIO (several completions), DMA both ways, an
    endpoint Unsupported Request, inbound Unsupported Requests, a refused
    command, the header, host memory and the error record."""
    r = Recorder(seed, max_payload=max_payload)
    r.events.append(("dl", True))
    r.header_setup()
    r.local(HDR_RD, 0, result=("OK", 0))  # Vendor and Device ID parameters 0
    r.local(HDR_RD, 2 * 4, result=("OK", 0x0604_0000))
    r.local(HDR_RD, 8 * 4, result=("OK", WINDOW))
    dev = r.run(r.host.start(r.host.enumerate_device(BDF)))
    r.run(r.host.start(r.host.setup_msi(dev, MSI_ADDR + MSI_OFFSET, 0x42)))
    bar0 = dev.bars[0].base
    for i in range(32):
        r.host.mem_write32(bar0 + 0x40 + 4 * i, 0x1000_0000 * (i & 7) + i)
    r.run()
    data = r.run(r.host.start(r.host.read(bar0 + 0x40, 128, 64)))  # two reads at once
    assert len(data) == 128
    r.run(r.host.start(r.host.read(bar0 + 0x46, 9, 128)))  # partial first and last DW
    r.refused(MEM_RD, 0x1000, 4)  # outside the memory window
    r.refused(CFG_RD, 2 << 20)  # a bus other than Secondary
    r.host_wr(0x100, 0xDEAD_BEEF)
    r.host_wr(0x104, 0x0123_4567)
    r.run(idle=40)  # past the shadow's latency: the endpoint's read must see them
    r.ep.dma_read(0x100, 8, tag=1)
    r.ep.dma_write(0x200, bytes(range(1, 41)))
    r.run()
    r.ep.raise_irq()
    r.run()
    r.host_rd(0x200)
    r.host_rd(0x224)
    r.local(ERR_RD, result=("OK", 0))
    # An endpoint Unsupported Request (in the window, outside every BAR) and
    # an endpoint read outside host memory (inbound Unsupported Request).
    req = r.host.mem_read(0x80F0_0000, 4)
    r.ep.dma_read(0x2000, 4, tag=2)
    r.run(idle=80)
    assert req.status == "UR" and r.ep.dma[2]["status"] == CPL_UR
    r.local(ERR_RD, result=("OK", 1 << 0 | 1 << 11))
    r.local(ERR_CLR, data=1 << 0)
    r.run(idle=80)
    r.local(ERR_RD, result=("OK", 1 << 11))
    r.ep.out.append(message(r.ep.id, MSG_ERR_COR, ROUTE_TO_RC).encode())
    r.run(idle=80)
    r.local(ERR_RD, result=("OK", 1 << 7 | 1 << 11))
    r.run()
    return r.events


def replay(events, **kw):
    sh = HostShadow(**kw)
    for ev in events:
        kind = ev[0]
        if kind == "tick":
            sh.tick()
        elif kind == "cmd":
            sh.observe_command(*ev[1:])
        elif kind == "tx":
            sh.observe_tx(ev[1])
        elif kind == "rx":
            sh.observe_rx(ev[1])
        elif kind == "res":
            sh.observe_result(*ev[1:])
        elif kind == "msi":
            sh.observe_msi(*ev[1:])
        elif kind == "dl":
            sh.observe_dl_up(ev[1])
    sh.finish()
    return sh


_SESSION = {}


def session(seed=1):
    if seed not in _SESSION:
        _SESSION[seed] = record_session(seed)
    return list(_SESSION[seed])


# ----------------------------------------------------------------------------
# Recorded active-mode traffic passes
@pytest.mark.parametrize("seed", [1, 2, 3])
def test_recorded_session_passes(seed):
    sh = replay(session(seed))
    assert not sh.errors
    s = sh.stats
    assert s["tx_requests"] > 50 and s["rx_completions"] > 40 and s["tx_completions"] >= 2
    assert s["msi"] == 1 and s["err_reads"] == 4 and s["results"] > s["commands"]
    assert sh.pending_msi == 0
    assert sh.devices[0x100].bars[0].size == 4096  # BARs learnt passively


def test_recorded_session_with_ur_window_split():
    """The endpoint's reads served in completions of 64 bytes or fewer."""
    sh = replay(record_session(4, max_payload=128))
    assert not sh.errors


def _timeout_events():
    r = Recorder(1, timeout=500)
    r.events.append(("dl", True))
    r.header_setup()
    dev = r.run(r.host.start(r.host.enumerate_device(BDF)))
    r.ep.mutate = lambda t: None  # the endpoint never answers
    req = r.host.mem_read(dev.bars[0].base, 4)
    r.run(idle=600)
    r.ep.mutate = None
    assert req.status == "timeout" and r.host.errors
    r.run(idle=80)
    r.local(ERR_RD, result=("OK", 1 << 3))
    return r.events


def test_recorded_timeout_passes():
    sh = replay(_timeout_events(), timeout_min=500, timeout_max=520)
    assert not sh.errors


def _link_down_events():
    r = Recorder(1)
    r.events.append(("dl", True))
    r.header_setup()
    dev = r.run(r.host.start(r.host.enumerate_device(BDF)))
    req = r.host.mem_read(dev.bars[0].base, 8)
    r.step()  # the read goes out
    r.link_down()
    assert req.status == "link_down"
    r.local(HDR_RD, 6 * 4, result=("OK", 0x01_01_00))  # the header survives
    r.run(r.host.start(r.host.enumerate_device(BDF)))
    return r.events


def test_recorded_link_down_passes():
    sh = replay(_link_down_events())
    assert not sh.errors


def test_command_while_link_down_is_link_down():
    sh = HostShadow()
    sh.observe_command(CFG_RD, 1 << 20)
    sh.observe_result("LINK_DOWN", 0, True)
    sh.observe_command(HDR_WR, 4, data=2)
    sh.observe_result("OK", 0, True)
    sh.finish()
    assert not sh.errors


def test_byte_enables_the_mas_may_refuse():
    """A command with a zero First DW BE may be refused (LOCAL_UR) or sent."""
    events = [("dl", True)] + session()[1:]
    sh = replay(events)
    sh.observe_command(MEM_RD, 0x8000_0000, 1, 0, 0)
    sh.observe_result("LOCAL_UR", 0, True)
    sh.finish()
    assert not sh.errors


# ----------------------------------------------------------------------------
# Seeded mismatches: each class raises HostCheckError
def _find(events, pred, n=0):
    hits = [i for i, e in enumerate(events) if pred(e)]
    return hits[n]


def _tlp(ev):
    return Tlp.decode(ev[1]) if ev[0] in ("tx", "rx") else None


def _edit(events, i, change):
    t = Tlp.decode(events[i][1])
    t = change(t) or t
    events[i] = (events[i][0], t.encode())
    return events


def _is(kind, side):
    return lambda e: e[0] == side and Tlp.decode(e[1]).kind == kind


def m_rc_malformed(ev):
    i = _find(ev, _is("CfgRd0", "tx"), 3)
    ev[i] = ("tx", ev[i][1][:-4])
    return ev


def m_rc_busy_tag(ev):
    i = _find(ev, lambda e: e[0] == "tx" and _tlp(e).kind == "MRd" and _tlp(e).tag == 1)
    return _edit(ev, i, lambda t: setattr(t, "tag", 0))


def m_rc_tag_range(ev):
    i = _find(ev, _is("CfgRd0", "tx"), 2)
    return _edit(ev, i, lambda t: setattr(t, "tag", 40))


def m_rc_wrong_address(ev):
    i = _find(ev, _is("MWr", "tx"), 5)
    return _edit(ev, i, lambda t: setattr(t, "address", t.address + 4))


def m_rc_wrong_data(ev):
    i = _find(ev, _is("CfgWr0", "tx"), 4)
    return _edit(ev, i, lambda t: setattr(t, "data", bytes(b ^ 1 for b in t.data)))


def m_rc_requester_id(ev):
    i = _find(ev, _is("CfgRd0", "tx"), 1)
    return _edit(ev, i, lambda t: setattr(t, "requester_id", 0x0100))


def m_rc_unrouted(ev):
    """A request sent for a command the routing refuses."""
    i = _find(ev, lambda e: e[0] == "cmd" and e[1] == MEM_RD and e[2] == 0x1000)
    t = Tlp(fmt=0, type=0, length=4, first_be=0xF, last_be=0xF, address=0x1000, tag=30)
    ev.insert(i + 1, ("tx", t.encode()))
    return ev


def m_rc_np_passes_posted(ev):
    """A read sent ahead of an earlier write still waiting."""
    i = _find(ev, _is("MWr", "tx"), 31)
    j = _find(ev, _is("MRd", "tx"))
    k = _find(ev, lambda e: e[0] == "cmd" and e[1] == MEM_RD)
    tx, cmd = ev[j], ev[k]
    for x in sorted((j, k), reverse=True):
        del ev[x]
    ev[i:i] = [cmd, tx]
    return ev


def m_rc_cpl_data(ev):
    i = _find(ev, lambda e: e[0] == "tx" and _tlp(e).kind == "CplD")
    return _edit(ev, i, lambda t: setattr(t, "data", bytes([t.data[0] ^ 0x80]) + t.data[1:]))


def m_rc_cpl_status(ev):
    i = _find(ev, lambda e: e[0] == "tx" and _tlp(e).kind == "Cpl")  # the inbound UR
    return _edit(ev, i, lambda t: setattr(t, "status", CPL_SC))


def m_rc_cpl_byte_count(ev):
    i = _find(ev, lambda e: e[0] == "tx" and _tlp(e).kind == "CplD")
    return _edit(ev, i, lambda t: setattr(t, "byte_count", t.byte_count + 4))


def m_rc_cpl_completer(ev):
    i = _find(ev, lambda e: e[0] == "tx" and _tlp(e).kind == "CplD")
    return _edit(ev, i, lambda t: setattr(t, "completer_id", 0x0008))


def m_rc_cpl_unrequested(ev):
    i = _find(ev, lambda e: e[0] == "tx" and _tlp(e).kind == "CplD")
    ev.insert(i + 1, ev[i])
    return ev


def m_rc_cpl_missing(ev):
    del ev[_find(ev, lambda e: e[0] == "tx" and _tlp(e).kind == "Cpl")]
    return ev


def m_ep_byte_count(ev):
    i = _find(ev, lambda e: e[0] == "rx" and _tlp(e).kind == "CplD" and _tlp(e).length_dw == 16)
    return _edit(ev, i, lambda t: setattr(t, "byte_count", t.byte_count - 4))


def m_ep_completer(ev):
    i = _find(ev, lambda e: e[0] == "rx" and _tlp(e).kind == "CplD", 20)
    return _edit(ev, i, lambda t: setattr(t, "completer_id", 0x0200))


def m_ep_bar_readback(ev):
    """The BAR read after its base was written returns another base."""
    j = _find(ev, lambda e: e[0] == "cmd" and e[1] == CFG_WR and e[2] & 0xFFF == 0x10
              and e[6] not in (0, 0xFFFFFFFF))
    i = _find(ev[j:], lambda e: e[0] == "rx" and _tlp(e).kind == "CplD") + j
    return _edit(ev, i, lambda t: setattr(t, "data", (0x9000_0000).to_bytes(4, "little")))


def m_ep_identity(ev):
    """The second read of the IDs disagrees with the first (a later
    enumeration step reads them through the same request kind)."""
    i = _find(ev, lambda e: e[0] == "tx" and _tlp(e).kind == "CfgRd0" and _tlp(e).cfg_reg == 0)
    t = Tlp.decode(ev[i][1])
    t.tag = 31
    # A second ID read, completed with other IDs, after enumeration.
    k = _find(ev, lambda e: e[0] == "cmd" and e[1] == HOST_WR)
    cpl = completion(0x0100, 0, 31, data=(0x1111_2222).to_bytes(4, "little"))
    ev[k:k] = [("cmd", CFG_RD, 1 << 20, 1, 0xF, 0, 0), ("tx", t.encode()), ("rx", cpl.encode()),
               ("res", "OK", 0x1111_2222, True)]
    return ev


def m_ep_mmio_data(ev):
    i = _find(ev, lambda e: e[0] == "rx" and _tlp(e).kind == "CplD" and _tlp(e).length_dw == 16)
    return _edit(ev, i, lambda t: setattr(t, "data", bytes([t.data[0] ^ 1]) + t.data[1:]))


def m_ep_interrupt_data(ev):
    i = _find(ev, lambda e: e[0] == "rx" and _tlp(e).kind == "MWr"
              and _tlp(e).address == MSI_ADDR + MSI_OFFSET)
    return _edit(ev, i, lambda t: setattr(t, "data", (0x43).to_bytes(4, "little")))


def m_ep_requester(ev):
    i = _find(ev, lambda e: e[0] == "rx" and _tlp(e).kind == "MWr")
    return _edit(ev, i, lambda t: setattr(t, "requester_id", 0x0108))


def m_ep_unexpected_cpl(ev):
    i = _find(ev, lambda e: e[0] == "rx" and _tlp(e).kind == "CplD")
    ev.insert(i + 1, ev[i])
    return ev


def m_ep_malformed(ev):
    i = _find(ev, lambda e: e[0] == "rx")
    ev[i] = ("rx", ev[i][1] + bytes(4))
    return ev


def m_res_read_data(ev):
    i = _find(ev, lambda e: e[0] == "res" and e[1] == "OK" and e[2] == 0x1000_0001)
    ev[i] = ("res", "OK", 0x1000_0003, ev[i][3])
    return ev


def m_res_cfg_data(ev):
    i = _find(ev, lambda e: e[0] == "res" and e[2] == 0x5678_1234)
    ev[i] = ("res", "OK", 0x5678_1235, True)
    return ev


def m_res_status(ev):
    i = _find(ev, lambda e: e[0] == "res" and e[1] == "UR")
    ev[i] = ("res", "OK", 0, True)
    return ev


def m_res_local_ur(ev):
    i = _find(ev, lambda e: e[0] == "res" and e[1] == "LOCAL_UR")
    ev[i] = ("res", "OK", 0, True)
    return ev


def m_res_missing(ev):
    del ev[_find(ev, lambda e: e[0] == "res", 10)]
    return ev


def m_res_extra(ev):
    i = _find(ev, lambda e: e[0] == "res", 10)
    ev.insert(i + 1, ev[i])
    return ev


def m_res_beat_count(ev):
    """A 64-byte read reports its last DW on beat 15 with last 0."""
    i = _find(ev, lambda e: e[0] == "res" and e[2] == 0x7000_001F)
    ev[i] = ("res", "OK", ev[i][2], False)
    return ev


def m_res_header(ev):
    i = _find(ev, lambda e: e[0] == "res" and e[2] == 0x0604_0000)
    ev[i] = ("res", "OK", 0x0604_0001, True)
    return ev


def m_res_host_memory(ev):
    i = _find(ev, lambda e: e[0] == "res" and e[2] == 0x0403_0201)
    ev[i] = ("res", "OK", 0x0403_0200, True)
    return ev


def m_res_early(ev):
    """A read's result before its completion arrived."""
    i = _find(ev, lambda e: e[0] == "res" and e[2] == 0x5678_1234)
    res = ev.pop(i)
    j = max(k for k in range(i) if ev[k][0] == "rx")
    ev.insert(j, res)
    return ev


def m_msi_wrong(ev):
    i = _find(ev, lambda e: e[0] == "msi")
    ev[i] = ("msi", ev[i][1] + 1, ev[i][2])
    return ev


def m_msi_unpredicted(ev):
    i = _find(ev, lambda e: e[0] == "msi")
    ev.insert(i + 1, ev[i])
    return ev


def m_err_extra_bit(ev):
    i = _find(ev, lambda e: e[0] == "res" and e[1] == "OK" and e[2] == 1 << 11)
    ev[i] = ("res", "OK", 1 << 11 | 1 << 3, True)
    return ev


def m_err_missing_bit(ev):
    i = _find(ev, lambda e: e[0] == "res" and e[1] == "OK" and e[2] == 1 << 7 | 1 << 11)
    ev[i] = ("res", "OK", 1 << 11, True)
    return ev


def m_err_not_cleared(ev):
    i = _find(ev, lambda e: e[0] == "res" and e[1] == "OK" and e[2] == 1 << 11)
    ev[i] = ("res", "OK", 1 << 11 | 1, True)
    return ev


def m_rc_poisoned(ev):
    i = _find(ev, _is("MWr", "tx"), 2)
    return _edit(ev, i, lambda t: setattr(t, "ep", 1))


def m_rc_zero_last_be(ev):
    """A 16-DW read sent with Last DW BE 0, as its command asked."""
    i = _find(ev, _is("MRd", "tx"))
    k = _find(ev, lambda e: e[0] == "cmd" and e[1] == MEM_RD)
    ev[k] = ev[k][:5] + (0,) + ev[k][6:]
    return _edit(ev, i, lambda t: setattr(t, "last_be", 0))


FAULTS = {
    "rc_poisoned": (m_rc_poisoned, "never poisons"),
    "rc_zero_last_be": (m_rc_zero_last_be, "zero byte-enable field"),
    # The root port's requests.
    "rc_malformed": (m_rc_malformed, "rc: malformed TLP sent"),
    "rc_busy_tag": (m_rc_busy_tag, "still outstanding"),
    "rc_tag_range": (m_rc_tag_range, "tags are 0 to 31"),
    "rc_wrong_address": (m_rc_wrong_address, "no pending command asks for"),
    "rc_wrong_data": (m_rc_wrong_data, "no pending command asks for"),
    "rc_requester_id": (m_rc_requester_id, "no pending command asks for"),
    "rc_unrouted": (m_rc_unrouted, "no pending command asks for"),
    "rc_np_passes_posted": (m_rc_np_passes_posted, "passed an earlier posted write"),
    # The root port's completions to the endpoint's reads.
    "rc_cpl_data": (m_rc_cpl_data, "host memory byte"),
    "rc_cpl_status": (m_rc_cpl_status, "predicted 001"),
    "rc_cpl_byte_count": (m_rc_cpl_byte_count, "rc: mem_rd tag 1: Byte Count"),
    "rc_cpl_completer": (m_rc_cpl_completer, "the root port is 0x0000"),
    "rc_cpl_unrequested": (m_rc_cpl_unrequested, "no endpoint request waits"),
    "rc_cpl_missing": (m_rc_cpl_missing, "never answered"),
    # The endpoint's completions and requests.
    "ep_byte_count": (m_ep_byte_count, "endpoint: mem_rd"),
    "ep_completer": (m_ep_completer, "completer ID 0x0200"),
    "ep_bar_readback": (m_ep_bar_readback, "predicted 0x80000000"),
    "ep_identity": (m_ep_identity, "predicted 0x56781234"),
    "ep_mmio_data": (m_ep_mmio_data, "the root port wrote"),
    "ep_interrupt_data": (m_ep_interrupt_data, "matches no programmed vector"),
    "ep_requester": (m_ep_requester, "unknown requester 0x0108"),
    "ep_unexpected_cpl": (m_ep_unexpected_cpl, "endpoint: unexpected completion"),
    "ep_malformed": (m_ep_malformed, "endpoint: malformed TLP"),
    # The results the root port reports.
    "res_read_data": (m_res_read_data, "the completion carried"),
    "res_cfg_data": (m_res_cfg_data, "the completion carried"),
    "res_status": (m_res_status, "predicted UR"),
    "res_local_ur": (m_res_local_ur, "predicted LOCAL_UR"),
    "res_missing": (m_res_missing, "the completion carried"),
    "res_extra": (m_res_extra, "no command waiting"),
    "res_beat_count": (m_res_beat_count, "last 0 of 16"),
    "res_header": (m_res_header, "predicted OK data 0x06040000"),
    "res_host_memory": (m_res_host_memory, "host memory holds 0x01"),
    "res_early": (m_res_early, "while the request is outstanding"),
    "msi_wrong": (m_msi_wrong, "predicted offset 0x4"),
    "msi_unpredicted": (m_msi_unpredicted, "with none predicted"),
    "err_extra_bit": (m_err_extra_bit, "none outside"),
    "err_missing_bit": (m_err_missing_bit, "bits 0x0880 set"),
    "err_not_cleared": (m_err_not_cleared, "none outside 0x0800"),
}


@pytest.mark.parametrize("name", sorted(FAULTS))
def test_seeded_mismatch_raises(name):
    mutate, message_part = FAULTS[name]
    with pytest.raises(HostCheckError) as exc:
        replay(mutate(session()))
    assert message_part in str(exc.value), str(exc.value)


@pytest.mark.parametrize("name", ["rc_busy_tag", "ep_completer", "res_status", "msi_wrong"])
def test_non_strict_records_and_carries_on(name):
    sh = replay(FAULTS[name][0](session()), strict=False)
    assert sh.errors and any(FAULTS[name][1] in e for e in sh.errors)


def test_timeout_too_early_raises():
    with pytest.raises(HostCheckError, match="before 600"):
        replay(_timeout_events(), timeout_min=600)


def test_timeout_missing_raises():
    ev = [e for e in _timeout_events() if not (e[0] == "res" and e[1] == "TIMEOUT")]
    with pytest.raises(HostCheckError, match="no completion and no TIMEOUT result"):
        replay(ev, timeout_min=400, timeout_max=450)


def test_timeout_bit_missing_raises():
    ev = _timeout_events()
    i = _find(ev, lambda e: e[0] == "res" and e[2] == 1 << 3)
    ev[i] = ("res", "OK", 0, True)
    with pytest.raises(HostCheckError, match="bits 0x0008 set"):
        replay(ev, timeout_min=500)


def test_link_down_result_must_be_link_down():
    ev = _link_down_events()
    i = _find(ev, lambda e: e[0] == "res" and e[1] == "LINK_DOWN")
    ev[i] = ("res", "TIMEOUT", 0, True)
    with pytest.raises(HostCheckError, match="predicted LINK_DOWN"):
        replay(ev)


def test_header_survives_link_down():
    ev = _link_down_events()
    i = _find(ev, lambda e: e[0] == "res" and e[2] == 0x01_01_00)
    ev[i] = ("res", "OK", 0, True)
    with pytest.raises(HostCheckError, match="predicted OK data 0x00010100"):
        replay(ev)


def test_msi_capture_may_drop_when_full():
    """Five MSIs with none taken: the fifth may be dropped, and the record's
    bit 12 may then be set; a capture out of order is still an error."""
    events = session()
    sh = replay(events)
    msi = Tlp.decode(next(e[1] for e in events if e[0] == "rx" and Tlp.decode(e[1]).kind == "MWr"
                          and Tlp.decode(e[1]).address == MSI_ADDR + MSI_OFFSET))
    for _ in range(5):
        sh.observe_rx(msi.encode())
    for _ in range(4):
        sh.observe_msi(MSI_OFFSET >> 2, 0x42)
    sh.observe_command(ERR_RD)
    sh.observe_result("OK", 1 << 7 | 1 << 11 | 1 << 12, True)
    sh.finish()
    assert not sh.errors and sh.pending_msi == 1
    with pytest.raises(HostCheckError, match="with none predicted"):
        sh.observe_msi(0x9, 0x42)


def _bare_shadow():
    """A shadow with the root port set up by hand: bus 0, Secondary Bus 1,
    Memory Space and Bus Master Enable, the memory window left at its
    reset value (1 MiB at address 0, over host memory)."""
    sh = HostShadow()
    sh.observe_dl_up(True)
    for idx, value in ((6, 0x01_01_00), (1, 0x6)):
        sh.observe_command(HDR_WR, 4 * idx, data=value)
        sh.observe_result("OK", 0, True)
    return sh


def test_inbound_request_in_window_is_ur():
    """An endpoint read of host memory that the memory window also claims
    is an Unsupported Request, not served (rc HAS, inbound routing)."""
    sh = _bare_shadow()
    rd = Tlp(fmt=0, type=0, length=1, requester_id=0x0100, tag=0, first_be=0xF, address=0x100)
    sh.observe_rx(rd.encode())
    served = completion(0, 0x0100, 0, CPL_SC, 4, 0, bytes(4))
    with pytest.raises(HostCheckError, match="status 000, predicted 001"):
        sh.observe_tx(served.encode())


def test_inbound_write_in_window_not_stored():
    sh = _bare_shadow()
    sh.observe_command(HOST_WR, 0x100, data=0x1111_1111)
    sh.observe_result("OK", 0, True)
    wr = Tlp(fmt=0b010, type=0, length=1, requester_id=0x0100, first_be=0xF, address=0x100,
             data=bytes(4))
    sh.observe_rx(wr.encode())
    sh.tick(100)
    sh.observe_command(HOST_RD, 0x100)
    with pytest.raises(HostCheckError, match="host memory holds 0x11"):
        sh.observe_result("OK", 0, True)
