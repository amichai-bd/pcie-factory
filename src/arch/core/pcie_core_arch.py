"""Architecture model of pcie_core, the endpoint: tl and cfg over link
(wiki/src/arch/core/HAS.md and its pages APP.md, TL_CFG.md).

For definition and performance analysis, not verification
(wiki/bkm/arch.md). It composes the sibling architecture models, loaded
unchanged from src/arch/ by bare name (the arch SPEC import rule), as the
core HAS stack wiring joins the IPs:

- ``pcie_tl_arch.Tl`` over the endpoint's ``pcie_dll_arch.Dll``, one of the
  two ends of the link model's ``LinkPair``;
- ``pcie_cfg_arch.Cfg`` behind ``TlCfg``, the tl-cfg interface (TL_CFG.md):
  the register-access channel and the register state ``tl`` reads, the
  ``tl_err`` and ``dl_err`` events and ``tl_np_pending`` into ``cfg``, and
  the message channel (``cfg`` offers, ``tl`` takes);
- the application's interrupt port on ``cfg``;
- ``tl`` and ``cfg`` held in reset while ``dl_up`` is 0 (Q10).

The far end of the link is ``Host``, a minimal host stand-in for these
scenarios only: it enumerates the function with configuration and BAR2
requests, sends BAR0 traffic, answers the application's reads, receives
interrupt writes and error messages, and checks every BAR0 read
completion against the splitting rules (COMPLETIONS.md). It is not the
independent host model DV uses, which is #288's; the application stand-in
is the tl model's ``App`` with an interrupt queue. Until the dll and link
gain the credit-release input (#303), the host stand-in keeps the release
rule's credit count itself, as the tl model's host does. Error-free: the
receiver-error input (``rcv_err``) is not driven. Python standard library
only (plus the sibling dll, link, tl and cfg models).
"""

from __future__ import annotations

import random
import sys
from collections import deque
from pathlib import Path

# The sibling models by bare name from src/arch: arch may use another IP's
# arch model; it never imports DV, RTL or tools.
for _ip in ("dll", "link", "tl", "cfg"):
    _d = str(Path(__file__).resolve().parent.parent / _ip)
    if _d not in sys.path:
        sys.path.append(_d)

import pcie_cfg_arch as cfgm  # noqa: E402
import pcie_dll_arch as dll  # noqa: E402
import pcie_link_arch as link  # noqa: E402
import pcie_tl_arch as tlm  # noqa: E402

# ---------------------------------------------------------------- constants

MAX_PAYLOAD = tlm.MAX_PAYLOAD      # 128 bytes (F2)
RCB = tlm.RCB                      # 64-byte read completion boundary (F2)
HOST_RID = 0x0000                  # the host's requester ID: bus 0, device 0
REG_LATENCY = 2                    # cycles from taking a register access to reg_cpl_valid
MSI_ADDR = 0xFEE0_0000             # the host's interrupt window, 4 KiB
HOST_MEM = 0x4000                  # host memory the application writes and reads
FC = {"ph": 8, "pd": 32, "nph": 8, "npd": 8}   # core parameter defaults

# Capability IDs the host walks for (cfg CAPS.md): PM, MSI, MSI-X, PCI Express.
CAP_PM, CAP_MSI, CAP_MSIX, CAP_PCIE = 0x01, 0x05, 0x11, 0x10
CMD_MSE, CMD_BME, CMD_SERR = 1 << 1, 1 << 2, 1 << 8
DEV_CTL_ALL = 0xF                  # Correctable, Non-Fatal, Fatal, UR Reporting Enables
DS_URD = 1 << 19                   # Device Status Unsupported Request Detected (DW 0x78)


def link_payload(both_ways: bool) -> float:
    """The link payload rate the targets compare with, as a fraction of
    the line: 128-byte posted TLPs one way or both ways (link closed form)."""
    return link.derived_fractions(12 + MAX_PAYLOAD, MAX_PAYLOAD, both_ways)[
        "payload_line_fraction"]


# ------------------------------------------------------------ tl-cfg

class TlCfg:
    """The tl-cfg interface (TL_CFG.md) seen from ``tl``: the register
    state levels and the register-access channel the tl model reads, all
    driven by the cfg model. ``reg_req_addr`` is a DW index; the cfg model
    takes byte addresses."""

    def __init__(self, c: cfgm.Cfg, latency: int = REG_LATENCY):
        self.c, self.latency = c, latency

    bus = property(lambda s: s.c.bus_num)
    dev = property(lambda s: s.c.dev_num)
    mem_en = property(lambda s: s.c.mem_space_en)
    bm_en = property(lambda s: s.c.bus_master_en)
    d_state = property(lambda s: s.c.d_state)
    cpl_timeout_dis = property(lambda s: s.c.cpl_timeout_dis)
    bar0 = property(lambda s: s.c.bar0_base)
    bar2 = property(lambda s: s.c.bar2_base)

    @property
    def id(self) -> int:
        """Completer and requester ID: bus_num, dev_num, function 0."""
        return (self.bus << 8) | (self.dev << 3)

    def access(self, bar2: bool, write: bool, addr: int, be: int, data: int,
               bus: int, dev: int) -> int:
        """One DW access taken on reg_req; returns reg_cpl_data."""
        a = 4 * addr
        if bar2:
            if write:
                self.c.bar2_write(a, data, be)
                return 0
            return self.c.bar2_read(a)
        if write:
            self.c.cfg_write(a, data, be, bus, dev)
            return 0
        return self.c.cfg_read(a)


# -------------------------------------------------------------- core

class App(tlm.App):
    """The tl model's application stand-in plus the interrupt port: it
    raises the vectors in ``irqs`` one at a time, and counts the payload
    bytes of the rq writes taken, so an interrupt can be checked against
    the data written before it."""

    def __init__(self, rng: random.Random, **kw):
        super().__init__(rng, **kw)
        self.irqs: deque = deque()
        self.irq_log: list[list] = []          # [cycle, vector, status, rq bytes taken before]
        self.rq_bytes = 0

    def rq_done(self, req: dict, now: int) -> None:
        super().rq_done(req, now)
        if req["write"]:
            self.rq_bytes += 4 * req["len"]


class Core:
    """pcie_core over its end of the link: ``tl`` and ``cfg`` wired as the
    core HAS stack wiring and the tl-cfg page say."""

    def __init__(self, d: dll.Dll, params: cfgm.Params, fc: dict, on_release=None,
                 cpl_timeout_div: int = 1):
        self.dll = d
        self.cfg = cfgm.Cfg(params)
        self.port = TlCfg(self.cfg)
        self.tl = tlm.Tl(self.port, fc, params.bar0_addr_w, cpl_timeout_div, on_release)
        self.up = False
        self.seen = 0
        self.tl_err = [0] * len(tlm.ERR_NAMES)
        self.dl_err = [0] * len(dll.ERR_NAMES)
        self.messages_taken = 0

    @staticmethod
    def _pulses(now: list[int], before: list[int]) -> int:
        """The error bits that pulse this cycle: kinds whose count grew."""
        bits = sum(1 << i for i, (a, b) in enumerate(zip(now, before)) if a != b)
        before[:] = now
        return bits

    def step(self, now: int, app: App) -> None:
        """One clk_i cycle, before the link moves: deliveries into tl, the
        interrupt port, the message channel, tl's streams, then the error
        pulses into cfg."""
        if not self.dll.dl_up:
            if self.up:                              # link down: reset tl and cfg (Q10)
                self.tl.reset()
                self.cfg.reset()
            self.up = False
            # cfg is in reset: events while down are lost, never pulsed later
            self.tl_err[:] = self.tl.errors
            self.dl_err[:] = self.dll.errors
            self.seen = len(self.dll.delivered)
            return
        self.up = True
        c, t = self.cfg, self.tl
        while self.seen < len(self.dll.delivered):
            t.receive(self.dll.delivered[self.seen], now)
            self.seen += 1
        c.np_pending = t.np_pending
        if app.irqs and not c.irq_busy:
            app.irq_log.append([now, app.irqs[0], c.irq_request(app.irqs.popleft()),
                                app.rq_bytes])
        m = c.offer()
        if m is not None and t.message(m.kind, m.addr, m.data):
            c.take()
            self.messages_taken += 1
        if app.irq_log and app.irq_log[-1][2] is None and c.irq_status is not None:
            app.irq_log[-1][2] = c.irq_status
        t.step(now, app, self.dll)
        tl_bits = self._pulses(t.errors, self.tl_err)
        dl_bits = self._pulses(self.dll.errors, self.dl_err)
        if tl_bits or dl_bits:
            c.error(tl=tl_bits, dl=dl_bits)


# -------------------------------------------------------------- host

class Host(tlm.Host):
    """The host stand-in (scenarios only; #288 owns the independent host
    model). Over the tl model's host (credit gate, answers to the
    application's reads, MWr and message counts) it adds configuration and
    BAR2 requests, BAR0 reads checked by the splitting rules, and the
    interrupt window."""

    def __init__(self, fc: dict, latency: int, bar0_addr_w: int, bus: int, dev: int):
        super().__init__(fc, latency)
        self.bus, self.dev = bus, dev
        self.bar0_mask = (1 << bar0_addr_w) - 1
        self.np: dict[int, dict | None] = {}       # tag -> completion header, None while waiting
        self.bar0: dict[int, dict] = {}             # tag -> BAR0 read state
        self.next_tag = 0
        self.irqs: list[tuple] = []                 # (cycle, address, data, MWr bytes before)
        self.read_cpls = self.read_errors = self.reads_done = self.read_bytes = 0
        self.unexpected = 0

    def _tag(self) -> int:
        while self.next_tag in self.np or self.next_tag in self.bar0:
            self.next_tag = (self.next_tag + 1) % 256
        t, self.next_tag = self.next_tag, (self.next_tag + 1) % 256
        return t

    def receive(self, tlp: bytes, now: int) -> None:
        h = tlm.parse(tlp)
        if h["type"] == tlm.T_CPL:
            if self.np.get(h["tag"], 0) is None:
                self.np[h["tag"]] = h
            elif h["tag"] in self.bar0:
                self._bar0_cpl(h)
            else:
                self.unexpected += 1
        elif (h["type"] == tlm.T_MEM and h["fmt"] & 2 and h["length"] == 1
              and (h["addr"] & 0xFFFFFFFF) >> 12 == MSI_ADDR >> 12):
            self.irqs.append((now, h["addr"], int.from_bytes(h["data"], "little"),
                              self.mwr_bytes))
        else:
            super().receive(tlp, now)

    def _bar0_cpl(self, h: dict) -> None:
        """A BAR0 read completion, checked by rule: status, Byte Count (the
        bytes left), Lower Address, at most 128 bytes, the data, and a split
        only where one is needed: a completion that is not the last is legal
        only while more than 128 bytes are left, and ends at the highest
        64-byte boundary within 128 bytes of its start."""
        r = self.bar0[h["tag"]]
        data, n = h["data"], len(h["data"])
        end = r["next"] + n
        last = n == r["left"]
        want = b"".join(tlm.App.pattern((r["next"] + 4 * i) & self.bar0_mask).to_bytes(4, "little")
                        for i in range(n // 4))
        ok = (h["status"] == tlm.SC and h["bc"] == r["left"] and h["la"] == r["next"] & 0x7F
              and 0 < n <= min(MAX_PAYLOAD, r["left"]) and data == want
              and (last or (r["left"] > MAX_PAYLOAD
                            and end == (r["next"] + MAX_PAYLOAD) // RCB * RCB)))
        self.read_cpls += 1
        self.read_errors += not ok
        r["next"], r["left"] = end, r["left"] - n
        if last or not ok:
            del self.bar0[h["tag"]]
            self.reads_done += ok
            self.read_bytes += r["size"] if ok else 0

    # ---- request helpers: generators that yield TLPs to send, None to wait
    def cfg_req(self, addr: int, data: int | None = None, be: int = 0xF, fn: int = 0):
        """A Type 0 configuration request; returns its completion header."""
        tag = self._tag()
        self.np[tag] = None
        yield tlm.cfg_tlp(data is not None, self.bus, self.dev, fn, addr >> 2, be, HOST_RID, tag,
                          b"" if data is None else data.to_bytes(4, "little"))
        while self.np[tag] is None:
            yield None
        return self.np.pop(tag)

    def cfg_rd(self, addr: int):
        h = yield from self.cfg_req(addr)
        return int.from_bytes(h["data"], "little") if h["status"] == tlm.SC else None

    def cfg_wr(self, addr: int, data: int, be: int = 0xF):
        h = yield from self.cfg_req(addr, data, be)
        return h["status"]

    def mem_rd(self, addr: int, dws: int = 1):
        """A memory read answered by one completion; returns its header."""
        tag = self._tag()
        self.np[tag] = None
        yield tlm.mem_tlp(False, addr, dws, 0xF, 0xF if dws > 1 else 0, HOST_RID, tag)
        while self.np[tag] is None:
            yield None
        return self.np.pop(tag)

    def mem_wr(self, addr: int, dws: list[int]):
        yield tlm.mem_tlp(True, addr, len(dws), 0xF, 0xF if len(dws) > 1 else 0, HOST_RID, 0,
                          b"".join(x.to_bytes(4, "little") for x in dws))

    def bar0_rd(self, addr: int, size: int):
        """A BAR0 read; its completions are checked as they arrive."""
        tag = self._tag()
        self.bar0[tag] = {"next": addr, "left": size, "size": size}
        yield tlm.mem_tlp(False, addr, size // 4, 0xF, 0xF if size > 4 else 0, HOST_RID, tag)


# -------------------------------------------------------------- bench

class Bench:
    """``core`` and the host stand-in back to back over the link model."""

    def __init__(self, rng: random.Random, params: cfgm.Params | None = None,
                 host_latency: int = 100, app_latency: int = 16, bus: int = 1, dev: int = 0):
        self.params = params or cfgm.Params()
        dfc = dict(dll.DEFAULT_FC, **FC)            # completion credits infinite
        self.a, self.b = dll.Dll("host", dfc), dll.Dll("core", dfc)
        self.pair = link.LinkPair(self.a, self.b, link.Line(rng))
        self.host = Host(FC, host_latency, self.params.bar0_addr_w, bus, dev)
        self.app = App(rng, latency=app_latency)
        self.core = Core(self.b, self.params, FC, on_release=self.host.release)
        self.prog = iter(())                        # the host program
        self.app_prog = iter(())                    # the application program
        self.pending = None
        self.seen = 0
        self.done = False
        self.link_up = [True, True]                 # the phy link of the host, of core

    @property
    def now(self) -> int:
        return self.pair.now

    def step(self) -> None:
        now, host = self.pair.now, self.host
        if self.pending is None:
            if host.out and host.out[0][0] <= now:
                self.pending = host.out.popleft()[1]
            elif not self.done:
                try:
                    self.pending = next(self.prog)
                except StopIteration:
                    self.done = True
        if (self.pending is not None and host.gate(self.pending, now)
                and self.a.offer(self.pending, now)):
            host.consume(self.pending)
            self.pending = None
        while self.seen < len(self.a.delivered):
            host.receive(self.a.delivered[self.seen], now)
            self.seen += 1
        next(self.app_prog, None)
        self.core.step(now, self.app)
        self.pair.step(link=tuple(self.link_up))

    def run(self, limit: int) -> int:
        while not self.done and self.now < limit:
            self.step()
        if not self.done:
            raise RuntimeError(f"host program not done after {limit} cycles")
        return self.now


# ------------------------------------------------------- host programs

def wait(cond):
    while not cond():
        yield None


def setup(b: Bench, bar0: int = 0x8000_0000, bar2: int = 0x9000_0000):
    """The short enumeration every traffic scenario starts with: BARs,
    then Memory Space, Bus Master and SERR# enables."""
    h = b.host
    yield from wait(lambda: b.a.dl_up and b.b.dl_up)
    yield from h.cfg_wr(0x10, bar0)
    yield from h.cfg_wr(0x18, bar2)
    yield from h.cfg_wr(0x04, CMD_MSE | CMD_BME | CMD_SERR, 0x3)


def enumerate_core(b: Bench, rng: random.Random, bad: list[str]):
    """A host enumeration over the link: IDs, capability walk, BAR sizing
    and assignment, enables, MSI with one interrupt, MSI-X with one
    interrupt pending under the function mask and sent on the unmask, then
    one sent at once. Mismatches go to ``bad``."""
    h, p, app = b.host, b.params, b.app

    def expect(what, got, want):
        if got != want:
            bad.append(f"{what}: got {got!r} want {want!r}")

    yield from wait(lambda: b.a.dl_up and b.b.dl_up)
    expect("ids", (yield from h.cfg_rd(0x00)), p.vendor_id | p.device_id << 16)
    expect("class", (yield from h.cfg_rd(0x08)), p.revision_id | p.class_code << 8)
    expect("header type", (yield from h.cfg_rd(0x0C)) >> 16 & 0x7F, 0)
    expect("subsystem", (yield from h.cfg_rd(0x2C)), p.subsys_vendor_id | p.subsys_id << 16)
    expect("cap list", (yield from h.cfg_rd(0x04)) >> 20 & 1, 1)
    caps, ptr = {}, (yield from h.cfg_rd(0x34)) & 0xFC
    while ptr and len(caps) < 48:
        hdr = yield from h.cfg_rd(ptr)
        caps[hdr & 0xFF] = ptr
        ptr = hdr >> 8 & 0xFC
    expect("capabilities", sorted(caps), sorted((CAP_PM, CAP_MSI, CAP_MSIX, CAP_PCIE)))
    expect("extended capabilities", (yield from h.cfg_rd(0x100)), 0)
    sizes = []
    for i in range(6):
        a = 0x10 + 4 * i
        old = yield from h.cfg_rd(a)
        yield from h.cfg_wr(a, 0xFFFFFFFF)
        mask = (yield from h.cfg_rd(a)) & 0xFFFFFFF0
        yield from h.cfg_wr(a, old)
        sizes.append((~mask + 1) & 0xFFFFFFFF if mask else 0)
    expect("bar sizes", sizes, [1 << p.bar0_addr_w, 0, cfgm.BAR2_BYTES, 0, 0, 0])
    bar0 = rng.randrange(1, 1 << (32 - p.bar0_addr_w)) << p.bar0_addr_w
    bar2 = rng.randrange(1, 1 << 20) << 12
    while bar2 >> p.bar0_addr_w == bar0 >> p.bar0_addr_w:
        bar2 = rng.randrange(1, 1 << 20) << 12
    yield from h.cfg_wr(0x10, bar0)
    yield from h.cfg_wr(0x18, bar2)
    yield from h.cfg_wr(0x04, CMD_MSE | CMD_BME | CMD_SERR, 0x3)
    expect("bars", ((yield from h.cfg_rd(0x10)), (yield from h.cfg_rd(0x18))), (bar0, bar2))
    expect("enables", (yield from h.cfg_rd(0x04)) & 0x7, CMD_MSE | CMD_BME)
    expect("completer id", b.core.port.id, h.bus << 8 | h.dev << 3)
    # MSI: address, data, enable; one interrupt from the application.
    msi = caps.get(CAP_MSI, 0)
    expect("msi 64-bit", (yield from h.cfg_rd(msi)) >> 23 & 1, 1)
    hi, data = rng.choice((0, rng.randrange(1, 1 << 32))), rng.randrange(1 << 16)
    yield from h.cfg_wr(msi + 4, MSI_ADDR)
    yield from h.cfg_wr(msi + 8, hi)
    yield from h.cfg_wr(msi + 12, data)
    yield from h.cfg_wr(msi, 1 << 16, 0x4)
    k = len(h.irqs)
    app.irqs.append(0)
    yield from wait(lambda: len(h.irqs) > k)
    expect("msi write", h.irqs[k][1:3], (MSI_ADDR | hi << 32, data))
    expect("msi status", app.irq_log[-1][2], "sent")
    yield from h.cfg_wr(msi, 0, 0x4)
    # MSI-X: table and pending-bit array in BAR2; enable with the function
    # masked, program one vector, an interrupt pends, the unmask sends it.
    mx = caps.get(CAP_MSIX, 0)
    expect("msix size", ((yield from h.cfg_rd(mx)) >> 16 & 0x7FF) + 1, p.msix_table_size)
    expect("msix table", (yield from h.cfg_rd(mx + 4)), 0x2)
    expect("msix pba", (yield from h.cfg_rd(mx + 8)), cfgm.PBA_OFFSET | 2)
    v = rng.randrange(p.msix_table_size)
    xdata = rng.randrange(1 << 32)
    yield from h.cfg_wr(mx, 0xC000 << 16, 0x8)
    yield from h.mem_wr(bar2 + 16 * v, [MSI_ADDR + 4 * v, 0, xdata, 0])
    pba = bar2 + cfgm.PBA_OFFSET + 4 * (v // 32)
    k = len(h.irqs)
    app.irqs.append(v)
    yield from wait(lambda: app.irq_log[-1][2] is not None and not app.irqs)
    expect("msix pending status", app.irq_log[-1][2], "pending")
    cpl = yield from h.mem_rd(pba)
    expect("pba bit", int.from_bytes(cpl["data"], "little") >> v % 32 & 1, 1)
    yield from h.cfg_wr(mx, 0x8000 << 16, 0x8)               # clear the function mask
    yield from wait(lambda: len(h.irqs) > k)
    expect("msix pending write", h.irqs[k][1:3], (MSI_ADDR + 4 * v, xdata))
    cpl = yield from h.mem_rd(pba)
    expect("pba cleared", int.from_bytes(cpl["data"], "little"), 0)
    app.irqs.append(v)
    yield from wait(lambda: len(h.irqs) > k + 1)
    expect("msix write", h.irqs[k + 1][1:3], (MSI_ADDR + 4 * v, xdata))
    expect("msix status", app.irq_log[-1][2], "sent")


# ---------------------------------------------------------------- perf

def _int(w: dict, key: str, default: int, lo: int, hi: int) -> int:
    v = w.get(key, default)
    if not isinstance(v, int) or isinstance(v, bool) or not lo <= v <= hi:
        raise ValueError(f"workload {key}={v!r} not an integer in {lo}..{hi}")
    return v


def _check_keys(w: dict, keys: set) -> None:
    extra = set(w) - keys - {"kind"}
    if extra:
        raise ValueError(f"unknown workload keys {sorted(extra)}")


def _random_params(rng: random.Random) -> cfgm.Params:
    return cfgm.Params(bar0_addr_w=rng.randint(12, 28), msix_table_size=rng.randint(1, 64),
                       vendor_id=rng.randrange(0xFFFF), device_id=rng.randrange(0xFFFF),
                       subsys_vendor_id=rng.randrange(0xFFFF), subsys_id=rng.randrange(0xFFFF),
                       class_code=rng.randrange(1 << 24), revision_id=rng.randrange(256))


def run_enumeration(w: dict, seed: int) -> dict[str, float]:
    """``trials`` enumerations, the first with the default parameters, the
    rest with random ones, each from link-up."""
    _check_keys(w, {"trials", "max_cycles"})
    rng = random.Random(seed)
    trials = _int(w, "trials", 4, 1, 1000)
    limit = _int(w, "max_cycles", 200_000, 1000, 10_000_000)
    bad: list[str] = []
    worst = cfg_reqs = irqs = 0
    for i in range(trials):
        b = Bench(rng, cfgm.Params() if i == 0 else _random_params(rng),
                  bus=rng.randrange(1, 256), dev=rng.randrange(32))
        b.prog = enumerate_core(b, rng, bad)
        b.run(limit)
        start = max(b.a.dl_up_at or 0, b.b.dl_up_at or 0)
        worst = max(worst, b.now - start)
        cfg_reqs += sum(1 for x in b.core.tl.released if x[1] == tlm.NP)
        irqs += len(b.host.irqs)
    return {
        "enumeration_errors": float(len(bad)),
        "enumeration_cycles_max": float(worst),
        "nonposted_requests": float(cfg_reqs),
        "interrupts_received": float(irqs),
        "trials": float(trials),
    }


def run_traffic(w: dict, seed: int) -> dict[str, float]:
    """BAR0 traffic from the host and rq traffic from the application, each
    way or both at once, after the short enumeration. Payload rates are
    measured from the first traffic cycle to the last byte delivered."""
    _check_keys(w, {"host", "app", "tlps", "bytes", "offset", "host_reads", "app_latency",
                    "host_latency", "max_cycles"})
    rng = random.Random(seed)
    host_op, app_op = w.get("host", "none"), w.get("app", "none")
    for name, op in (("host", host_op), ("app", app_op)):
        if op not in ("none", "write", "read"):
            raise ValueError(f"workload {name}={op!r}: none, write or read")
    if host_op == app_op == "none":
        raise ValueError("no traffic")
    n = _int(w, "tlps", 500, 1, 100000)
    size = _int(w, "bytes", MAX_PAYLOAD, 4, 4096)
    offset = _int(w, "offset", 0, 0, 4092)
    if size % 4 or offset % 4 or offset + size > 4096 or (
            size > MAX_PAYLOAD and (host_op == "write" or app_op != "none")):
        raise ValueError("bytes and offset: DW multiples in one 4 KiB page; "
                         f"above {MAX_PAYLOAD} bytes only for host reads")
    outstanding = _int(w, "host_reads", 16, 1, 128)
    b = Bench(rng, host_latency=_int(w, "host_latency", 100, 0, 100000),
              app_latency=_int(w, "app_latency", 16, 1, 10000))
    h, app, t = b.host, b.app, b.core.tl
    bar0 = 0x8000_0000
    start = [None]
    dws = size // 4

    def host_prog():
        yield from setup(b, bar0)
        start[0] = b.now
        if host_op == "none":
            return
        for i in range(n):
            if host_op == "read":
                yield from wait(lambda: len(h.bar0) < outstanding)
                yield from h.bar0_rd(bar0 + offset, size)
            else:
                addr = bar0 + (i * size) % 4096
                yield from h.mem_wr(addr, [(i + k) & 0xFFFFFFFF for k in range(dws)])

    issued = [0]
    busy: set[int] = set()

    def app_prog():
        while start[0] is None or app_op == "none":
            yield
        while issued[0] < n:
            for tag, _ in app.done_reads:
                busy.discard(tag)
            app.done_reads.clear()
            if not app.rq:
                addr = HOST_MEM + 128 * (issued[0] % 32)
                req = {"write": app_op == "write", "addr": addr, "len": dws, "fbe": 0xF,
                       "lbe": 0xF if dws > 1 else 0}
                if app_op == "write":
                    app.rq.append({**req, "data": [issued[0]] * dws})
                    issued[0] += 1
                else:
                    free = [x for x in range(tlm.TAGS) if x not in busy]
                    if free:
                        busy.add(free[0])
                        app.rq.append({**req, "tag": free[0]})
                        issued[0] += 1
            yield

    b.prog, b.app_prog = host_prog(), app_prog()

    def finished() -> bool:
        ok = b.done
        if host_op == "read":
            ok = ok and not h.bar0
        elif host_op == "write":
            ok = ok and t.counts["cq_bytes"] >= n * size
        if app_op == "write":
            ok = ok and h.mwr_bytes >= n * size
        elif app_op == "read":
            ok = ok and sum(1 for p in app.rc_got if p["done"]) >= n
        return ok

    limit = _int(w, "max_cycles", 3_000_000, 1000, 50_000_000)
    while b.now < limit and not finished():
        b.step()
    span = max(1, b.now - (start[0] or 0))
    pay = []
    if host_op != "none":
        pay.append(h.read_bytes if host_op == "read" else t.counts["cq_bytes"])
    if app_op != "none":
        pay.append(t.counts["rc_bytes"] if app_op == "read" else h.mwr_bytes)
    frac = min(pay) / (link.LINE_BYTES_PER_CYCLE * span)
    both = len(pay) == 2 or "read" in (host_op, app_op)
    occ = t.counts["occ_max"]
    return {
        "payload_line_fraction": frac,
        "fraction_of_link": frac / link_payload(both),
        "completions_per_read": h.read_cpls / max(1, h.reads_done) if host_op == "read" else 0.0,
        "completion_errors": float(h.read_errors + h.unexpected + h.cpl_errors),
        "host_reads_done": float(h.reads_done),
        "app_reads_done": float(sum(1 for p in app.rc_got if p["done"] and p["status"] == 0)),
        "posted_header_credits_held_max": float(occ[0]),
        "nonposted_header_credits_held_max": float(occ[2]),
        "tl_errors": float(sum(t.errors)),
        "cycles": float(span),
    }


def run_interrupts(w: dict, seed: int) -> dict[str, float]:
    """MSI: the application writes ``bytes`` to host memory, then raises an
    interrupt, ``count`` times; each MSI write must reach the host after
    the data written before it. MSI-X: every vector programmed and masked,
    one request each pends, the host reads the pending bits through BAR2,
    unmasks every vector, receives one write each, reads the pending bits
    clear, then one request each is sent at once."""
    _check_keys(w, {"mode", "count", "bytes", "vectors", "max_cycles"})
    rng = random.Random(seed)
    mode = w.get("mode")
    if mode not in ("msi", "msix"):
        raise ValueError(f"workload mode {mode!r}: msi or msix")
    vectors = _int(w, "vectors", 8, 1, 64)
    b = Bench(rng, cfgm.Params(msix_table_size=vectors))
    h, app = b.host, b.app
    bar2 = 0x9000_0000
    count = _int(w, "count", 50, 1, 10000)
    size = _int(w, "bytes", MAX_PAYLOAD, 0, MAX_PAYLOAD)
    bad: list[str] = []
    pba_seen = [0]
    pba_after = [0]

    def prog():
        yield from setup(b, bar2=bar2)
        if mode == "msi":
            yield from h.cfg_wr(cfgm.CAP_MSI + 4, MSI_ADDR)
            yield from h.cfg_wr(cfgm.CAP_MSI + 12, 0x5A)
            yield from h.cfg_wr(cfgm.CAP_MSI, 1 << 16, 0x4)
            for i in range(count):
                if size:
                    app.rq.append({"write": True, "addr": HOST_MEM, "len": size // 4, "fbe": 0xF,
                                   "lbe": 0xF if size > 4 else 0, "data": [i] * (size // 4)})
                    yield from wait(lambda: not app.rq)
                app.irqs.append(0)
                yield from wait(lambda: len(h.irqs) > i)
            return
        yield from h.cfg_wr(cfgm.CAP_MSIX, 0x8000 << 16, 0x8)   # enable; vectors masked at reset
        for v in range(vectors):
            yield from h.mem_wr(bar2 + 16 * v, [MSI_ADDR + 4 * v, 0, 0x100 + v, 1])
        app.irqs.extend(range(vectors))
        yield from wait(lambda: not app.irqs and app.irq_log[-1][2] is not None)
        for k in range((vectors + 31) // 32):
            cpl = yield from h.mem_rd(bar2 + cfgm.PBA_OFFSET + 4 * k)
            pba_seen[0] += bin(int.from_bytes(cpl["data"], "little")).count("1")
        for v in range(vectors):
            yield from h.mem_wr(bar2 + 16 * v + 12, [0])
        yield from wait(lambda: len(h.irqs) >= vectors)
        for k in range((vectors + 31) // 32):
            cpl = yield from h.mem_rd(bar2 + cfgm.PBA_OFFSET + 4 * k)
            pba_after[0] += bin(int.from_bytes(cpl["data"], "little")).count("1")
        app.irqs.extend(range(vectors))
        yield from wait(lambda: len(h.irqs) >= 2 * vectors)

    b.prog = prog()
    b.run(_int(w, "max_cycles", 2_000_000, 1000, 50_000_000))
    log, got = app.irq_log, h.irqs
    if mode == "msi":
        want = [(MSI_ADDR, 0x5A)] * count
        # the data written before each request reached the host first
        overtakes = sum(1 for (_, _, _, before), (_, _, _, seen) in zip(log, got)
                        if seen < before)
    else:
        want = [(MSI_ADDR + 4 * v, 0x100 + v) for v in range(vectors)] * 2
        overtakes = 0
    wrong = sum(1 for x, y in zip(got, want) if x[1:3] != y) + abs(len(got) - len(want))
    bad.extend(f"status {x[2]}" for x in log if x[2] not in ("sent", "pending"))
    met = {
        "interrupts_requested": float(len(log)),
        "reported_pending": float(sum(1 for x in log if x[2] == "pending")),
        "reported_sent": float(sum(1 for x in log if x[2] == "sent")),
        "interrupt_writes": float(len(got)),
        "interrupts_wrong": float(wrong + len(bad)),
        "interrupt_overtakes": float(overtakes),
        "pending_bits_seen": float(pba_seen[0]),
        "pending_bits_after": float(pba_after[0]),
        "cycles": float(b.now),
    }
    if mode == "msi":                    # from the request to the write at the host
        met["interrupt_latency_cycles_max"] = float(max(g[0] - r[0] for r, g in zip(log, got)))
    return met


def run_unsupported(w: dict, seed: int) -> dict[str, float]:
    """Requests no BAR or function serves: an I/O read, a memory read just
    past BAR0, a memory write hitting no BAR, and a configuration read to
    function 1,
    with every error reporting enable on. The non-posted ones get an
    Unsupported Request completion, the posted one none; cfg records
    Unsupported Request Detected and sends ERR_COR (advisory, non-posted)
    and ERR_NONFATAL (posted); BAR0 still serves a read after."""
    _check_keys(w, {"rounds", "max_cycles"})
    rng = random.Random(seed)
    rounds = _int(w, "rounds", 1, 1, 100)
    b = Bench(rng)
    h = b.host
    bar0, nowhere = 0x8000_0000, 0xA000_0000
    past_bar0 = bar0 + (1 << b.params.bar0_addr_w)           # the first byte after BAR0
    ur = {"completions": 0, "wrong": 0, "urd": 0}

    def check(cpl, bc, la):
        ur["completions"] += cpl["status"] == tlm.UR
        ur["wrong"] += (cpl["status"], cpl["bc"], cpl["la"], len(cpl["data"])) != (tlm.UR, bc, la, 0)

    def prog():
        yield from setup(b, bar0)
        yield from h.cfg_wr(cfgm.CAP_PCIE + 8, DEV_CTL_ALL, 0x1)
        for _ in range(rounds):
            tag = h._tag()
            h.np[tag] = None
            io = bytearray(tlm.mem_tlp(False, 0x100, 1, 0xF, 0, HOST_RID, tag))
            io[0] = tlm.T_IO
            yield bytes(io)
            yield from wait(lambda: h.np[tag] is not None)
            check(h.np.pop(tag), 4, 0)
            check((yield from h.mem_rd(past_bar0 + 0x44, 2)), 8, 0x44)
            yield from h.mem_wr(nowhere, [1, 2])
            check((yield from h.cfg_req(0x00, fn=1)), 4, 0)
            ds = yield from h.cfg_rd(cfgm.CAP_PCIE + 8)
            ur["urd"] += bool(ds & DS_URD)
            yield from h.cfg_wr(cfgm.CAP_PCIE + 8, DS_URD | DEV_CTL_ALL, 0x5)   # RW1C clear
            yield from h.bar0_rd(bar0 + 0x40, 64)
            yield from wait(lambda: not h.bar0)

    b.prog = prog()
    b.run(_int(w, "max_cycles", 200_000, 1000, 10_000_000))
    t = b.core.tl
    return {
        "ur_completions": float(ur["completions"]),
        "ur_completion_errors": float(ur["wrong"]),
        "ur_posted_events": float(t.errors[tlm.E_UR_P]),
        "ur_nonposted_events": float(t.errors[tlm.E_UR_NP]),
        "ur_detected": float(ur["urd"]),
        "err_cor_messages": float(h.msgs.count(tlm.MSG_ERR[1])),
        "err_nonfatal_messages": float(h.msgs.count(tlm.MSG_ERR[2])),
        "err_fatal_messages": float(h.msgs.count(tlm.MSG_ERR[3])),
        "bar0_reads_after": float(h.reads_done),
        "completion_errors": float(h.read_errors + h.unexpected),
        "other_tl_errors": float(sum(t.errors) - t.errors[tlm.E_UR_P] - t.errors[tlm.E_UR_NP]),
    }


def perf(workload: dict, seed: int) -> dict[str, float]:
    kind = workload.get("kind")
    if kind == "enumeration":
        return run_enumeration(workload, seed)
    if kind == "traffic":
        return run_traffic(workload, seed)
    if kind == "interrupts":
        return run_interrupts(workload, seed)
    if kind == "unsupported":
        return run_unsupported(workload, seed)
    raise ValueError(f"workload kind {kind!r}")
