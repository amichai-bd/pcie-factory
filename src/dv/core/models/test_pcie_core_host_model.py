"""Unit tests of the independent host model (no simulator).

The far end is ``EndpointStub``, a small endpoint written here from the
cfg HAS register and capability pages and the tl completion page (wiki
pages only): a Type 0 configuration space, BAR0 memory, the MSI-X table
and pending bits in BAR2, completions split on a 64-byte boundary, and
interrupt and bus-master requests. A ``pump`` loop moves TLPs both ways
in order, with a seeded choice of which side moves next.
"""

import ast
import random
import re
import sys
from pathlib import Path

import pytest

from pcie_core_host_model import (
    CPL_STATUS,
    DeviceInfo,
    HostCheckError,
    HostModel,
)
from pcie_core_host_tlp import (
    CPL_CA,
    CPL_SC,
    CPL_UR,
    MSG_ERR_COR,
    MSG_SET_SLOT_POWER_LIMIT,
    ROUTE_TO_RC,
    Tlp,
    bdf_id,
    byte_enables,
    completion,
    mem_read,
    mem_write,
    message,
)

HERE = Path(__file__).resolve().parent
BDF = (1, 0, 0)


# ----------------------------------------------------------------------------
# Endpoint stub
class EndpointStub:
    """A minimal endpoint for exercising the host model."""

    def __init__(self, bar0_addr_w=12, msix_size=8, bar64=False, cap_loop=False,
                 split_rcb=64, max_payload=128, io_bar=0, rom_size=0, ext_caps=(),
                 ext_next=None):
        # io_bar: an I/O BAR1 of that many bytes; rom_size: a sizable
        # Expansion ROM; ext_caps: [(id, version)] from 0x100, each 0x40
        # apart, the last one's Next set to ext_next (default 0, the end).
        self.io_bar, self.rom_size = io_bar, rom_size
        self.ext_caps, self.ext_next = list(ext_caps), ext_next
        self.bar0_size = 1 << bar0_addr_w
        self.msix_size = msix_size
        self.bar64 = bar64
        self.split_rcb = split_rcb
        self.max_payload = max_payload
        self.bus = self.dev = 0
        self.cfg = {}
        self.rw = {}  # DW offset -> writable mask
        self.w1c = {}
        self._build(cap_loop)
        self.bar0 = bytearray(self.bar0_size)
        self.table = [[0, 0, 0, 1] for _ in range(msix_size)]  # addr lo, hi, data, ctrl
        self.pending = 0
        self.msi_pending = False
        self.out = []  # TLPs to the host, in order
        self.held = []  # completions held back to interleave reads (reorder mode)
        self.reorder = None  # a random.Random to reorder completions of different reads
        self.mutate = None  # f(Tlp) -> Tlp | None, applied to completions it sends
        self.dma = {}  # tag -> dict(data, remaining, status)

    # -- configuration space -------------------------------------------------
    def _reg(self, off, value, rw=0, w1c=0):
        self.cfg[off], self.rw[off], self.w1c[off] = value, rw, w1c

    def _build(self, cap_loop):
        self._reg(0x00, 0x5678_1234)
        self._reg(0x04, 1 << 20, rw=(1 << 1) | (1 << 2) | (1 << 6) | (1 << 8) | (1 << 10))
        self._reg(0x08, 0xFF00_0001)
        self._reg(0x0C, 0, rw=0xFF)
        if self.bar64:
            mask = ((1 << 64) - self.bar0_size)
            self._reg(0x10, 0b0100 | 0b1000, rw=mask & 0xFFFFFFFF)
            self._reg(0x14, 0, rw=mask >> 32)
        else:
            self._reg(0x10, 0, rw=(0xFFFFFFFF - self.bar0_size + 1) & 0xFFFFFFFF)
            if self.io_bar:  # I/O space indicator bit 0 set, size from bit 2 up
                self._reg(0x14, 0x1, rw=(0xFFFFFFFF - self.io_bar + 1) & 0xFFFFFFFC)
            else:
                self._reg(0x14, 0)
        self._reg(0x18, 0, rw=0xFFFFF000)
        for off in (0x1C, 0x20, 0x24, 0x28, 0x38):
            self._reg(off, 0)
        rom_rw = ((0xFFFFFFFF - self.rom_size + 1) & 0xFFFFF800) | 1 if self.rom_size else 0
        self._reg(0x30, 0, rw=rom_rw)
        for i, (cid, ver) in enumerate(self.ext_caps):
            off = 0x100 + 0x40 * i
            last = i == len(self.ext_caps) - 1
            nxt = (self.ext_next or 0) if last else off + 0x40
            self._reg(off, (nxt << 20) | (ver << 16) | cid)
        self._reg(0x2C, 0xBEEF_CAFE)
        self._reg(0x34, 0x40)
        self._reg(0x3C, 0, rw=0xFF)
        self._reg(0x40, 0x0003_5001)  # PM, next 0x50, version 3
        self._reg(0x44, 0x8, rw=0x3)
        self._reg(0x50, (1 << 23) | 0x6005, rw=(1 << 16) | (0x7 << 20))  # MSI, 64-bit
        self._reg(0x54, 0, rw=0xFFFFFFFC)
        self._reg(0x58, 0, rw=0xFFFFFFFF)
        self._reg(0x5C, 0, rw=0xFFFF)
        self._reg(0x60, ((self.msix_size - 1) << 16) | (0x50 if cap_loop else 0x70) << 8 | 0x11,
                  rw=0xC000_0000)
        self._reg(0x64, 0x2)
        self._reg(0x68, 0x800 | 0x2)
        self._reg(0x70, 0x0002_0010)  # PCIe cap, version 2, Endpoint, last
        self._reg(0x74, 0x0000_8000)  # MPS supported 128, role-based error reporting
        self._reg(0x78, (2 << 12) | (1 << 11) | (1 << 4), rw=0x7EFF, w1c=0xF << 16)

    def cfg_value(self, off):
        return self.cfg.get(off, 0)

    @property
    def command(self):
        return self.cfg[0x04] & 0xFFFF

    @property
    def bar0_base(self):
        return self.cfg[0x10] & ~0xF | (self.cfg[0x14] << 32 if self.bar64 else 0)

    @property
    def bar2_base(self):
        return self.cfg[0x18] & ~0xFFF

    @property
    def id(self):
        return bdf_id(self.bus, self.dev, 0)

    def _cfg_write(self, off, value, be):
        mask = 0
        for i in range(4):
            if be >> i & 1:
                mask |= 0xFF << 8 * i
        if off not in self.cfg:
            return
        rw, w1c = self.rw[off] & mask, self.w1c[off] & mask
        old = self.cfg[off]
        new = (old & ~rw) | (value & rw)
        new &= ~(value & w1c)
        self.cfg[off] = new
        if off == 0x60 or off == 0x04:
            self._flush_pending()

    # -- receive -------------------------------------------------------------
    def handle(self, raw):
        t = Tlp.decode(raw)
        k = t.kind
        if k in ("CfgRd0", "CfgWr0"):
            if k == "CfgWr0":
                self.bus, self.dev = t.cfg_bus, t.cfg_dev  # capture on every write
                self._cfg_write(t.cfg_reg, int.from_bytes(t.data, "little"), t.first_be)
                self._send(completion(self.id, t.requester_id, t.tag))
            else:
                v = self.cfg_value(t.cfg_reg)
                self._send(completion(self.id, t.requester_id, t.tag,
                                      data=v.to_bytes(4, "little")))
        elif k in ("MRd", "MWr"):
            mem_on = self.command & 2
            base, region = None, None
            if mem_on and self.bar0_base <= t.address < self.bar0_base + self.bar0_size:
                base, region = self.bar0_base, "bar0"
            elif mem_on and self.bar2_base <= t.address < self.bar2_base + 4096:
                base, region = self.bar2_base, "bar2"
            if region is None:
                if k == "MRd":
                    dw, n, fbe, _ = t.address, t.length_dw, t.first_be, t.last_be
                    low = (fbe & -fbe).bit_length() - 1 if fbe else 0
                    total = 4 * n - low if n > 1 else 4  # enough for these tests
                    self._send(completion(self.id, t.requester_id, t.tag, CPL_UR,
                                          byte_count=total, lower_addr=(dw + low) & 0x7F))
                return
            off = t.address - base
            if k == "MWr":
                mask = [True] * len(t.data)
                for i in range(4):
                    mask[i] = bool(t.first_be >> i & 1)
                    if t.length_dw > 1:
                        mask[len(t.data) - 4 + i] = bool(t.last_be >> i & 1)
                for i, b in enumerate(t.data):
                    if mask[i]:
                        self._mem_write(region, off + i, b)
            else:
                self._answer_read(t, region, off)
        elif k in ("Cpl", "CplD"):
            self._dma_completion(t)
        elif k in ("Msg", "MsgD"):
            self.last_msg = t

    def _mem_write(self, region, off, b):
        if region == "bar0":
            self.bar0[off] = b
            return
        if off < 16 * self.msix_size:
            e, f, byte = off // 16, (off % 16) // 4, off % 4
            v = self.table[e][f]
            v = (v & ~(0xFF << 8 * byte)) | (b << 8 * byte)
            self.table[e][f] = v & (1 if f == 3 else 0xFFFFFFFF)
            if f == 3:
                self._flush_pending()

    def _mem_read_byte(self, region, off):
        if region == "bar0":
            return self.bar0[off]
        if off < 16 * self.msix_size:
            e, f, byte = off // 16, (off % 16) // 4, off % 4
            return (self.table[e][f] >> 8 * byte) & 0xFF
        if 0x800 <= off < 0x810:
            return (self.pending >> 8 * (off - 0x800)) & 0xFF
        return 0

    def _answer_read(self, t, region, off):
        n, fbe, lbe = t.length_dw, t.first_be, t.last_be
        if n == 1:
            if fbe == 0:
                low, total = 0, 1
            else:
                low = (fbe & -fbe).bit_length() - 1
                total = fbe.bit_length() - low
        else:
            low = (fbe & -fbe).bit_length() - 1
            total = 4 * n - low - (4 - lbe.bit_length())
        first = t.address + low
        start, end = t.address, t.address + 4 * n
        cpls = []
        while start < end:
            if end - start <= self.max_payload:
                stop = end
            else:
                stop = (start + self.max_payload) // self.split_rcb * self.split_rcb
            data = bytes(self._mem_read_byte(region, off + a - t.address)
                         for a in range(start, stop))
            nxt = max(first, start)
            cpls.append(completion(self.id, t.requester_id, t.tag, CPL_SC,
                                   (first + total - nxt) & 0xFFF, nxt & 0x7F, data,
                                   tc=t.tc, attr=t.attr))
            start = stop
        if self.reorder is not None:
            self.held.append(cpls)
        else:
            for c in cpls:
                self._send(c)

    def release_held(self):
        """Send held completions, reads interleaved at random, each read's in
        order (completions of different tags may pass each other)."""
        while self.held:
            i = self.reorder.randrange(len(self.held))
            self._send(self.held[i].pop(0))
            if not self.held[i]:
                self.held.pop(i)

    def _send(self, t):
        if self.mutate is not None and t.is_completion:
            t = self.mutate(t)
            if t is None:
                return
        self.out.append(t.encode())

    # -- requests the endpoint makes -----------------------------------------
    def raise_irq(self, vector=0):
        if self.cfg[0x60] >> 31 & 1:
            self.pending |= 1 << vector
            self._flush_pending()
        elif self.cfg[0x50] >> 16 & 1:
            self.msi_pending = True
            self._flush_pending()

    def _allowed(self):
        return self.command & 4 and (self.cfg[0x44] & 3) == 0

    def _flush_pending(self):
        if not self._allowed():
            return
        if self.cfg[0x60] >> 31 & 1:
            if self.cfg[0x60] >> 30 & 1:
                return
            for v in range(self.msix_size):
                if self.pending >> v & 1 and not self.table[v][3] & 1:
                    self.pending &= ~(1 << v)
                    lo, hi, data, _ = self.table[v]
                    self._send(mem_write(self.id, (hi << 32) | lo,
                                         data.to_bytes(4, "little"), 0xF, 0))
        elif self.msi_pending and self.cfg[0x50] >> 16 & 1:
            self.msi_pending = False
            addr = self.cfg[0x54] | (self.cfg[0x58] << 32)
            data = self.cfg[0x5C] & 0xFFFF
            self._send(mem_write(self.id, addr, data.to_bytes(4, "little"), 0xF, 0))

    def dma_write(self, addr, data):
        pos = 0
        while pos < len(data):  # at most 128 bytes of DWs per write
            a = addr + pos
            n = min(len(data) - pos, (a & ~3) + 128 - a)
            dw, ndw, fbe, lbe = byte_enables(a, n)
            buf = bytearray(4 * ndw)
            buf[a - dw:a - dw + n] = data[pos:pos + n]
            self._send(mem_write(self.id, dw, bytes(buf), fbe, lbe))
            pos += n

    def dma_read(self, addr, nbytes, tag):
        dw, n, fbe, lbe = byte_enables(addr, nbytes)
        self.dma[tag] = {"data": bytearray(), "remaining": nbytes, "next": addr,
                         "status": None, "sizes": []}
        self._send(mem_read(self.id, tag, dw, n, fbe, lbe))

    def _dma_completion(self, c):
        d = self.dma[c.tag]
        if c.status != CPL_SC:
            d["status"] = c.status
            return
        assert c.byte_count_value == d["remaining"], (c.byte_count_value, d["remaining"])
        assert c.lower_addr == d["next"] & 0x7F
        start = d["next"] & ~3
        covers = min(d["remaining"], start + len(c.data) - d["next"])
        off = d["next"] - start
        d["data"] += c.data[off:off + covers]
        d["sizes"].append(len(c.data))
        d["remaining"] -= covers
        d["next"] += covers
        if d["remaining"] == 0:
            d["status"] = CPL_SC


def pump(host, ep, proc=None, seed=1, avail=None, max_steps=200000):
    """Move TLPs both ways until nothing moves (or ``proc`` finishes).
    Order holds per direction; a seeded choice picks the side that moves."""
    rng = random.Random(seed)
    for _ in range(max_steps):
        if proc is not None and proc.done:
            break
        if ep.reorder is not None and not ep.out:
            ep.release_held()
        moves = []
        if host.tx_pending:
            moves.append("h")
        if ep.out:
            moves.append("e")
        if not moves:
            break
        side = rng.choice(moves)
        if side == "h":
            raw = host.pop_tx(avail)
            if raw is None:
                if not ep.out:
                    break
                side = "e"
            else:
                ep.handle(raw)
                continue
        host.receive(ep.out.pop(0))
    if proc is not None:
        assert proc.done, "procedure did not finish"
        if proc.error is not None:
            raise proc.error
        return proc.result
    return None


def enumerated(seed=1, **ep_kw):
    host = HostModel()
    ep = EndpointStub(**ep_kw)
    dev = pump(host, ep, host.start(host.enumerate_device(BDF)), seed=seed)
    return host, ep, dev


# ----------------------------------------------------------------------------
# Configuration reads and writes
def test_cfg_read_write_and_capture():
    host, ep = HostModel(), EndpointStub()
    w = host.cfg_write(BDF, 0x3C, 0xA5, 0b0001)
    r = host.cfg_read(BDF, 0x3C)
    pump(host, ep)
    assert w.ok and r.ok and r.value & 0xFF == 0xA5
    assert (ep.bus, ep.dev) == (1, 0)
    assert w.tag == 0 and r.tag == 1  # two outstanding at once
    assert host.idle and not host.errors


def test_cfg_completer_id_checked():
    host, ep = HostModel(strict=False), EndpointStub()
    r = host.cfg_read(BDF, 0x00)  # a read first: the stub has captured nothing yet
    pump(host, ep)
    assert r.done and any("completer ID" in e for e in host.errors)


def test_cfg_completion_byte_count_checked():
    host, ep = HostModel(strict=False), EndpointStub()
    host.cfg_write(BDF, 0x04, 0, 0b0011)
    pump(host, ep)

    def bad_bc(t):
        t.byte_count = 8
        return t
    ep.mutate = bad_bc
    host.cfg_read(BDF, 0x00)
    pump(host, ep)
    assert any("Byte Count 8" in e for e in host.errors)


# ----------------------------------------------------------------------------
# Enumeration: capability walk, BAR sizing and assignment
@pytest.mark.parametrize("seed", [1, 2, 3])
def test_enumeration_default(seed):
    host, ep, dev = enumerated(seed)
    assert isinstance(dev, DeviceInfo)
    assert (dev.vendor_id, dev.device_id) == (0x1234, 0x5678)
    assert dev.class_code == 0xFF0000 and dev.revision == 1
    assert (dev.subsys_vendor_id, dev.subsys_id) == (0xCAFE, 0xBEEF)
    assert dev.caps == [(0x01, 0x40), (0x05, 0x50), (0x11, 0x60), (0x10, 0x70)]
    assert dev.ext_caps == []
    assert sorted(dev.bars) == [0, 2]
    assert dev.bars[0].size == 4096 and dev.bars[2].size == 4096
    assert dev.bars[0].base % 4096 == 0 and dev.bars[2].base % 4096 == 0
    assert dev.bars[0].base != dev.bars[2].base
    assert ep.bar0_base == dev.bars[0].base and ep.bar2_base == dev.bars[2].base
    assert dev.rom_size == 0
    assert dev.msi.is_64 and not dev.msi.per_vector_mask
    assert (dev.msix.table_size, dev.msix.table_bir, dev.msix.table_offset) == (8, 2, 0)
    assert (dev.msix.pba_bir, dev.msix.pba_offset) == (2, 0x800)
    assert dev.pcie_port_type == 0 and dev.mps_supported == 128
    assert ep.command & 0x6 == 0x6  # memory space and bus master on
    assert host.idle and not host.errors


def test_enumeration_large_bar0_aligned():
    host, ep, dev = enumerated(bar0_addr_w=20)
    assert dev.bars[0].size == 1 << 20 and dev.bars[0].base % (1 << 20) == 0
    assert dev.bars[2].base + 4096 <= dev.bars[0].base or dev.bars[0].base + (1 << 20) <= dev.bars[2].base


def test_enumeration_64bit_prefetchable_bar():
    host, ep, dev = enumerated(bar64=True, bar0_addr_w=16)
    bar = dev.bars[0]
    assert bar.is_64 and bar.prefetchable and bar.size == 1 << 16
    assert 1 not in dev.bars  # the upper half is BAR1
    assert ep.bar0_base == bar.base


def test_capability_loop_detected():
    host, ep = HostModel(), EndpointStub(cap_loop=True)
    with pytest.raises(HostCheckError, match="looping"):
        pump(host, ep, host.start(host.enumerate_device(BDF)))


def test_bar_assignment_read_back_checked():
    host, ep = HostModel(), EndpointStub()
    plain = ep._cfg_write

    def drop_bar2_base(off, value, be):  # sizes as 4 KiB, keeps no base
        if not (off == 0x18 and value != 0xFFFFFFFF):
            plain(off, value, be)
    ep._cfg_write = drop_bar2_base
    with pytest.raises(HostCheckError, match="read back"):
        pump(host, ep, host.start(host.enumerate_device(BDF)))


# ----------------------------------------------------------------------------
# MSI and MSI-X
def test_msi_setup_and_interrupt():
    host, ep, dev = enumerated()
    pump(host, ep, host.start(host.setup_msi(dev, 0x1_FEE0_0000, 0x4321)))
    assert ep.cfg[0x50] >> 16 & 1 and ep.cfg[0x54] == 0xFEE0_0000 and ep.cfg[0x58] == 1
    assert ep.cfg[0x5C] == 0x4321 and not ep.cfg[0x60] >> 31
    ep.raise_irq()
    pump(host, ep)
    assert [(i.kind, i.vector, i.data) for i in host.interrupts] == [("msi", 0, 0x4321)]
    assert host.interrupts[0].bdf == dev.id and not host.errors


def test_msi_wrong_data_flagged():
    host, ep, dev = enumerated()
    pump(host, ep, host.start(host.setup_msi(dev, 0xFEE0_0000, 0x10)))
    host.strict = False
    ep.cfg[0x5C] = 0x11  # the endpoint sends other data
    ep.raise_irq()
    pump(host, ep)
    assert not host.interrupts and any("matches no programmed vector" in e for e in host.errors)


def test_msix_setup_mask_pending_unmask():
    host, ep, dev = enumerated()
    vectors = {v: (0xFEE0_1000 + 4 * v, 0x100 + v) for v in range(8)}
    pump(host, ep, host.start(host.setup_msix(dev, vectors)))
    assert ep.cfg[0x60] >> 30 == 0b10  # enabled, function mask clear
    assert not ep.cfg[0x50] >> 16 & 1  # MSI off
    assert ep.table[3] == [0xFEE0_100C, 0, 0x103, 0]
    ep.raise_irq(3)
    pump(host, ep)
    assert [(i.kind, i.vector, i.data) for i in host.interrupts] == [("msix", 3, 0x103)]
    host.msix_mask(dev, 5, True)
    pump(host, ep)
    ep.raise_irq(5)
    pump(host, ep)
    assert len(host.interrupts) == 1
    pba = pump(host, ep, host.start(host.read_msix_pba(dev)))
    assert pba == 1 << 5
    host.msix_mask(dev, 5, False)
    pump(host, ep)
    assert host.interrupts[-1].vector == 5
    assert pump(host, ep, host.start(host.read_msix_pba(dev))) == 0
    assert not host.errors


def test_msix_function_mask():
    host, ep, dev = enumerated()
    pump(host, ep, host.start(host.setup_msix(dev, {0: (0xFEE0_0000, 7)})))
    host.msix_function_mask(dev, True)
    pump(host, ep)
    ep.raise_irq(0)
    pump(host, ep)
    assert not host.interrupts
    host.msix_function_mask(dev, False)
    pump(host, ep)
    assert [i.data for i in host.interrupts] == [7]


def test_interrupt_without_bus_master_flagged():
    host, ep, dev = enumerated()
    pump(host, ep, host.start(host.setup_msi(dev, 0xFEE0_0000, 1)))
    host.cfg_write(dev.bdf, 0x04, 0x2, 0b0011)  # bus master off in the host's view only
    pump(host, ep)
    host.strict = False
    ep.cfg[0x04] |= 0x4  # a broken endpoint that still sends
    ep.raise_irq()
    pump(host, ep)
    assert any("Bus Master Enable 0" in e for e in host.errors)


# ----------------------------------------------------------------------------
# Memory reads and writes, completion checking
def test_write_then_read_back():
    host, ep, dev = enumerated()
    base = dev.bars[0].base
    payload = bytes((7 * i) & 0xFF for i in range(300))
    reqs = host.mem_write(base + 6, payload)
    assert [len(r.tlp.data) for r in reqs] == [128, 128, 48]  # 128-byte TLPs from DW 4
    got = pump(host, ep, host.start(host.read(base + 6, 300)))
    assert got == payload and ep.bar0[6:306] == payload
    assert not host.errors


@pytest.mark.parametrize("offset, nbytes, sizes", [
    (0, 128, [128]),
    (4, 128, [128]),  # across a 64-byte boundary, one completion
    (4, 512, [124, 128, 128, 128, 4]),
    (0, 4096, [128] * 32),
    (4, 256, [124, 128, 4]),
    (0x7C, 8, [8]),
])
def test_split_completions(offset, nbytes, sizes):
    host, ep, dev = enumerated(bar0_addr_w=13)
    base = dev.bars[0].base
    ep.bar0[:] = bytes((i * 13 + 1) & 0xFF for i in range(len(ep.bar0)))
    r = host.mem_read(base + offset, nbytes)
    pump(host, ep)
    assert r.ok, r.errors
    assert r.completion_sizes == sizes
    assert bytes(r.data) == bytes(ep.bar0[offset:offset + nbytes])


def test_unaligned_partial_dw_read():
    host, ep, dev = enumerated()
    base = dev.bars[0].base
    ep.bar0[:16] = bytes(range(16))
    r = host.mem_read(base + 3, 6)
    pump(host, ep)
    assert r.ok and bytes(r.data) == bytes(range(3, 9))
    assert (r.tlp.first_be, r.tlp.last_be) == (0x8, 0x1)


def test_zero_length_read():
    host, ep, dev = enumerated()
    r = host.mem_read(dev.bars[0].base + 0x40, 0)
    pump(host, ep)
    assert r.ok and r.data == bytearray() and r.tlp.first_be == 0


def test_split_on_rcb_accepted():
    host, ep, dev = enumerated()
    ep.max_payload = 64  # a completer splitting on every 64-byte boundary
    r = host.mem_read(dev.bars[0].base + 8, 200)
    pump(host, ep)
    assert r.ok and r.completion_sizes == [56, 64, 64, 16]


def test_io_bar_and_rom_sized():
    host, ep, dev = enumerated(io_bar=256, rom_size=2048)
    io = dev.bars[1]
    assert io.io and io.size == 256 and io.base == 0  # recorded, not assigned
    assert dev.rom_size == 2048
    assert ep.cfg[0x14] == 0x1 and ep.cfg[0x30] == 0  # left unassigned, ROM disabled
    assert dev.bars[0].size == 4096 and not host.errors


def test_extended_capabilities_walked():
    host, ep, dev = enumerated(ext_caps=[(0x0001, 2), (0x000B, 1)])
    assert dev.ext_caps == [(0x0001, 2, 0x100), (0x000B, 1, 0x140)]
    assert not host.errors


@pytest.mark.parametrize("nxt", [0x100, 0x40])
def test_extended_capability_bad_pointer_fails(nxt):
    host, ep = HostModel(), EndpointStub(ext_caps=[(0x0001, 1), (0x0003, 1)], ext_next=nxt)
    with pytest.raises(HostCheckError, match="extended capability list"):
        pump(host, ep, host.start(host.enumerate_device(BDF)))


def test_zero_byte_read_procedure():
    host, ep, dev = enumerated()
    assert pump(host, ep, host.start(host.read(dev.bars[0].base, 0))) == b""


def test_unsupported_request_status():
    host, ep, dev = enumerated()
    hole = max(b.base + b.size for b in dev.bars.values())
    r = host.mem_read(hole, 4)  # past every BAR
    pump(host, ep)
    assert r.done and r.status == "UR" and not r.errors and host.idle


def _mutated(mutate, offset=4, nbytes=512):
    host, ep, dev = enumerated()
    host.strict = False
    ep.mutate = mutate
    r = host.mem_read(dev.bars[0].base + offset, nbytes)
    pump(host, ep)
    return host, r


def test_wrong_byte_count_flagged():
    def m(t):
        t.byte_count += 4
        return t
    host, r = _mutated(m)
    assert any("Byte Count" in e for e in r.errors)


def test_wrong_lower_address_flagged():
    def m(t):
        t.lower_addr ^= 0x4
        return t
    host, r = _mutated(m)
    assert any("Lower Address" in e for e in r.errors)


def test_split_off_boundary_flagged():
    host, ep, dev = enumerated()
    host.strict = False
    ep.split_rcb = 32  # splits on 32-byte boundaries: finer than the 64-byte RCB
    r = host.mem_read(dev.bars[0].base + 0x24, 256)
    pump(host, ep)
    assert any("not on a 64-byte boundary" in e for e in r.errors)


def test_data_past_request_flagged():
    def m(t):
        t.data = t.data + bytes(4)
        t.length += 1
        return t
    host, r = _mutated(m, offset=0, nbytes=8)
    assert any("past the request's last DW" in e for e in r.errors)


def test_poisoned_completion_flagged():
    def m(t):
        t.ep = 1
        return t
    host, r = _mutated(m, offset=0, nbytes=8)
    assert r.status == "poisoned" and any("poisoned" in e for e in r.errors)


def test_success_without_data_flagged():
    def m(t):
        t.fmt, t.data, t.length = 0, b"", 0
        return t
    host, r = _mutated(m, offset=0, nbytes=8)
    assert r.status == "error" and any("without data" in e for e in r.errors)


def test_completer_abort_ends_read():
    def m(t):
        return completion(t.completer_id, t.requester_id, t.tag, CPL_CA,
                          t.byte_count, t.lower_addr)
    host, r = _mutated(m, offset=0, nbytes=8)
    assert r.status == "CA" and not r.errors


def test_payload_above_max_flagged():
    host, ep, dev = enumerated()
    host.strict = False
    ep.max_payload, ep.split_rcb = 256, 64
    r = host.mem_read(dev.bars[0].base, 256)
    pump(host, ep)
    assert any("maximum payload" in e for e in r.errors)


def test_crs_on_memory_read_flagged():
    def m(t):
        return completion(t.completer_id, t.requester_id, t.tag, 0b010,
                          t.byte_count, t.lower_addr)
    host, r = _mutated(m, offset=0, nbytes=8)
    assert r.status == "CRS" and any("CRS completion to a memory read" in e for e in r.errors)


def test_reserved_status_read_as_ur_and_flagged():
    def m(t):
        return completion(t.completer_id, t.requester_id, t.tag, 0b011,
                          t.byte_count, t.lower_addr)
    host, r = _mutated(m, offset=0, nbytes=8)
    assert r.status == "UR" and any("reserved Completion Status 011" in e for e in r.errors)


def test_strict_mode_raises():
    host, ep, dev = enumerated()

    def m(t):
        t.lower_addr ^= 0x4
        return t
    ep.mutate = m
    host.mem_read(dev.bars[0].base + 4, 64)
    with pytest.raises(HostCheckError, match="Lower Address"):
        pump(host, ep)


# ----------------------------------------------------------------------------
# Tag tracking and timeouts
def test_tags_limited_reused_and_out_of_order():
    host, ep = HostModel(num_tags=2), EndpointStub()
    dev = pump(host, ep, host.start(host.enumerate_device(BDF)))
    ep.bar0[:] = bytes(i & 0xFF for i in range(4096))
    ep.reorder = random.Random(5)
    reqs = [host.mem_read(dev.bars[0].base + 256 * i, 256) for i in range(5)]
    assert [r.tag for r in reqs] == [0, 1, None, None, None]
    assert len(host.outstanding()) == 5 and host.tx_pending == 2
    pump(host, ep, seed=4)
    assert all(r.ok for r in reqs)
    assert {r.tag for r in reqs} == {0, 1}
    for i, r in enumerate(reqs):
        assert bytes(r.data) == bytes(ep.bar0[256 * i:256 * i + 256])
    assert host.idle and not host.errors


def test_unexpected_completion_flagged():
    host = HostModel(strict=False)
    host.receive(completion(0x0100, 0x0000, 3, data=bytes(4)).encode())
    host.receive(completion(0x0100, 0x0200, 0, data=bytes(4)).encode())  # not the host's ID
    assert sum("unexpected completion" in e for e in host.errors) == 2


def test_duplicate_completion_flagged():
    host, ep, dev = enumerated()
    host.strict = False
    sent = []

    def m(t):
        sent.append(t.encode())
        return t
    ep.mutate = m
    r = host.mem_read(dev.bars[0].base, 8)
    pump(host, ep)
    assert r.ok
    host.receive(sent[0])  # the same completion again: its tag is free now
    assert any("unexpected completion" in e for e in host.errors)


def test_completion_timeout():
    host = HostModel(timeout=100, strict=False)
    r = host.cfg_read(BDF, 0)
    assert host.pop_tx() is not None
    host.tick(100)
    assert not r.done
    host.tick(1)
    assert r.status == "timeout" and any("timeout" in e for e in host.errors)
    host.receive(completion(bdf_id(*BDF), 0, r.tag, data=bytes(4)).encode())
    assert any("unexpected completion" in e for e in host.errors)


# ----------------------------------------------------------------------------
# Credits and ordering on transmit
def test_credit_gating_and_ordering():
    host = HostModel()
    rd = host.cfg_read(BDF, 0)
    wr = host.mem_write(0x8000_0000, bytes(64))[0]
    no_np = {"nph": 0, "npd": 0}
    raw = host.pop_tx(no_np)  # the read is blocked; the later write passes it
    assert Tlp.decode(raw).kind == "MWr" and wr.sent_at == 0
    assert host.pop_tx(no_np) is None
    assert Tlp.decode(host.pop_tx({"nph": 1, "npd": 0})).kind == "CfgRd0"
    assert rd.sent_at == 0


def test_np_never_passes_blocked_posted():
    host = HostModel()
    host.mem_write(0x8000_0000, bytes(128))
    host.cfg_read(BDF, 0)
    assert host.pop_tx({"ph": 1, "pd": 4}) is None  # 8 data credits needed
    assert Tlp.decode(host.pop_tx({"ph": 0xFF, "pd": 0xFFF})).kind == "MWr"  # infinite
    assert Tlp.decode(host.pop_tx({"ph": 0})).kind == "CfgRd0"


def test_receive_returns_credit_cost():
    host, ep, dev = enumerated()
    ep.dma_write(0x1000_0000, bytes(70))
    assert host.receive(ep.out.pop()) == ("P", 1, 5)


# ----------------------------------------------------------------------------
# The endpoint as requester: host memory and completions
@pytest.mark.parametrize("split, sizes", [("fewest", [124, 128, 4]), ("rcb", [60, 64, 64, 64, 4])])
def test_endpoint_dma_read_and_write(split, sizes):
    host, ep, dev = enumerated()
    host.cpl_split = split
    host.ep_max_read_request = 256  # a requester with a larger limit, to see the splits
    data = bytes(range(256))
    ep.dma_write(0x2000_0004, data)
    pump(host, ep)
    assert host.memory.read(0x2000_0004, 256) == data
    ep.dma_read(0x2000_0004, 256, tag=3)
    pump(host, ep)
    d = ep.dma[3]
    assert d["status"] == CPL_SC and bytes(d["data"]) == data and d["sizes"] == sizes
    assert not host.errors


def test_command_byte_enables_kept():
    """A Command write of byte 1 only keeps Bus Master Enable (byte 0)."""
    host, ep, dev = enumerated()
    host.cfg_write(BDF, 0x04, 0x0400, 0b0010)  # Interrupt Disable only
    pump(host, ep)
    ep.dma_write(0x100, bytes(8))
    pump(host, ep)
    assert not host.errors


def test_endpoint_read_limits_flagged():
    host, ep, dev = enumerated()
    host.strict = False
    ep.dma_read(0x2000_0000, 256, tag=1)  # above the 128-byte maximum read request
    ep.dma_read(0x2000_0000, 4, tag=8)  # a tag above 7 with extended tags off
    pump(host, ep)
    assert any("maximum read request" in e for e in host.errors)
    assert any("tag 8" in e for e in host.errors)


def test_endpoint_read_ur_status():
    host, ep, dev = enumerated()
    host.read_status = lambda addr, n: CPL_STATUS["UR"]
    ep.dma_read(0x3000_0000, 16, tag=1)
    pump(host, ep)
    assert ep.dma[1]["status"] == CPL_UR


def test_endpoint_request_without_bus_master_flagged():
    host, ep, dev = enumerated()
    host.cfg_write(dev.bdf, 0x04, 0x2, 0b0011)
    pump(host, ep)
    host.strict = False
    ep.dma_write(0x1000, bytes(4))
    pump(host, ep)
    assert any("Bus Master Enable 0" in e for e in host.errors)


def test_endpoint_write_crossing_4k_flagged():
    host, ep, dev = enumerated()
    host.strict = False
    host.receive(mem_write(dev.id, 0x1FF8, bytes(16), 0xF, 0xF).encode())
    assert any("crosses 4 KiB" in e for e in host.errors)


# ----------------------------------------------------------------------------
# Messages
def test_messages_both_ways():
    host, ep, dev = enumerated()
    host.send_message(MSG_SET_SLOT_POWER_LIMIT, data=bytes([0x19, 0x01, 0, 0]))
    pump(host, ep)
    assert ep.last_msg.msg_code == 0x50 and ep.last_msg.data[:2] == bytes([0x19, 0x01])
    host.receive(message(dev.id, MSG_ERR_COR, ROUTE_TO_RC).encode())
    assert [(m.code, m.routing, m.requester_id) for m in host.messages] == [
        (0x30, ROUTE_TO_RC, dev.id)]


def test_malformed_tlp_flagged():
    host = HostModel(strict=False)
    assert host.receive(bytes(8)) is None
    assert any("malformed" in e for e in host.errors)


def test_send_request_raw_io_read():
    host, ep, dev = enumerated()
    t = Tlp(fmt=0, type=0b00010, length=1, first_be=0xF, address=0x100)
    r = host.send_request(t)
    raw = host.pop_tx()
    assert Tlp.decode(raw).kind == "IORd" and r.tag is not None
    host.receive(completion(dev.id, 0, r.tag, CPL_UR).encode())
    assert r.done and r.status == "UR"


def test_link_down_drops_state_then_reenumerates():
    host, ep, dev = enumerated()
    r = host.mem_read(dev.bars[0].base, 8)
    host.link_down()
    assert r.done and r.status == "link_down" and host.idle and not host.devices
    ep2 = EndpointStub()  # the function is back at its reset values
    dev2 = pump(host, ep2, host.start(host.enumerate_device(BDF)))
    assert dev2.bars[0].base == dev.bars[0].base and not host.errors


# ----------------------------------------------------------------------------
# Independence: only the standard library, pytest and the host's own modules
# (the shadow's tests reuse this file's endpoint stub)
_OWN = {"pcie_core_host_model", "pcie_core_host_tlp", "pcie_core_host_shadow",
        "test_pcie_core_host_model"}
_DYNAMIC = {"__import__", "exec", "eval", "compile", "import_module"}
_FILES = ["pcie_core_host_model.py", "pcie_core_host_tlp.py", "pcie_core_host_shadow.py",
          "test_pcie_core_host_model.py", "test_pcie_core_host_tlp.py",
          "test_pcie_core_host_shadow.py"]


@pytest.mark.parametrize("name", _FILES)
def test_independence_no_shared_code(name):
    tree = ast.parse((HERE / name).read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods = [a.name.split(".")[0] for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            mods = [(node.module or "").split(".")[0]]
        elif isinstance(node, ast.Call) and (
                getattr(node.func, "id", "") in _DYNAMIC
                or getattr(node.func, "attr", "") in _DYNAMIC):
            pytest.fail(f"{name}: dynamic import, exec or compile")
        else:
            continue
        for m in mods:
            ok = m in _OWN or m == "pytest" or m in sys.stdlib_module_names
            assert ok, f"{name} imports {m}"
            assert m not in ("importlib", "runpy"), f"{name} imports {m}"
    if name.startswith("test_"):
        return
    # Code (identifiers and strings other than docstrings) names no
    # architecture model, arch path or other testbench model.
    docs = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.ClassDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                docs.add(id(first.value))
    for node in ast.walk(tree):
        text = None
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docs:
            text = node.value
        elif isinstance(node, ast.Name):
            text = node.id
        elif isinstance(node, ast.Attribute):
            text = node.attr
        if text is None:
            continue
        for bad in ("_arch", "dv/tl", "dv/cfg", "dv/dll", "dv/link", "partner", "dv/rc",
                    "pcie_rc", "rc_tl", "rc_seq"):
            assert bad not in text, f"{name} names {bad!r} in code: {text!r}"
        segments = re.split(r"[/\\.]", text)
        assert "arch" not in segments, f"{name} names an arch path segment: {text!r}"
