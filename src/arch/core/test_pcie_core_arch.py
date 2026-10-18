"""Self-tests of the pcie_core architecture model; no simulator needed.

Run: python3 -m pytest src/arch/core (./build ci unit runs them as arch:<ip>).

Each test states a rule of the core HAS pages (HAS.md, APP.md, TL_CFG.md)
as an example, then checks the composed model against it.
"""

import random
from pathlib import Path

import pytest

import pcie_core_arch as m

tlm, cfgm = m.tlm, m.cfgm
BAR0, BAR2 = 0x8000_0000, 0x9000_0000


def bench(**kw):
    return m.Bench(random.Random(1), **kw)


def run(b, prog, limit=100_000):
    b.prog = prog
    b.done = False
    return b.run(b.now + limit)


def test_siblings_are_loaded_unchanged():
    """The tl, cfg, link and dll models are the sibling folders' own
    modules, not copies (arch SPEC, import rule)."""
    arch = Path(m.__file__).resolve().parent.parent
    for mod, ip in ((m.tlm, "tl"), (m.cfgm, "cfg"), (m.link, "link"), (m.dll, "dll")):
        assert Path(mod.__file__).resolve() == arch / ip / f"pcie_{ip}_arch.py"
    b = bench()
    assert isinstance(b.core.tl, tlm.Tl) and isinstance(b.core.cfg, cfgm.Cfg)
    assert b.core.dll is b.b and b.core.tl.cfg.c is b.core.cfg


def test_register_access_is_by_dw_index():
    """reg_req_addr is a DW index: configuration DW 4 is BAR0 (0x10), BAR2
    DW 3 is vector 0's control word; a configuration write captures the bus
    and device number, which give the completer ID (TL_CFG register
    access and state)."""
    c = cfgm.Cfg()
    p = m.TlCfg(c)
    p.access(False, True, 0x10 >> 2, 0xF, BAR0, 5, 3)
    assert c.bar0_base == BAR0 and p.bar0 == BAR0
    assert (p.bus, p.dev, p.id) == (5, 3, 5 << 8 | 3 << 3)
    assert p.access(False, False, 0, 0xF, 0, 0, 0) == c.cfg_read(0)
    p.access(True, True, 3, 0xF, 0, 0, 0)
    assert c.table[0][3] == 0 and p.access(True, False, 3, 0xF, 0, 0, 0) == 0
    p.access(False, True, 0x04 >> 2, 0x3, m.CMD_MSE | m.CMD_BME, 5, 3)
    assert p.mem_en and p.bm_en and p.d_state == cfgm.D0


def test_enumeration_over_the_link():
    """IDs, capability walk, BAR sizing and assignment, enables, MSI and
    MSI-X set-up with one interrupt each way: no mismatch, three interrupt
    writes (core HAS, Targets: enumeration)."""
    r = m.perf({"kind": "enumeration", "trials": 3}, 1)
    assert r["enumeration_errors"] == 0
    assert r["interrupts_received"] == 9


def test_enumeration_finds_a_wrong_id():
    """The host stand-in compares what it reads with the parameters."""
    b = bench()
    bad = []
    prog = m.enumerate_core(b, random.Random(1), bad)
    b.params = cfgm.Params(vendor_id=0x1234)     # what the host expects, not what cfg has
    run(b, prog)
    assert any(x.startswith("ids") for x in bad)


def test_bar_decode():
    """A memory request hits BAR0 only inside the 2^BAR0_ADDR_W bytes at
    bar0_base, and only with Memory Space Enable set; anything else is an
    Unsupported Request (core HAS, Request routing; TL_CFG BAR hit)."""
    b = bench()
    h = b.host
    got = {}

    def prog():
        yield from m.wait(lambda: b.a.dl_up and b.b.dl_up)
        got["before"] = (yield from h.mem_rd(BAR0))["status"]          # BAR0 unassigned
        yield from h.cfg_wr(0x10, BAR0)
        got["mse_off"] = (yield from h.mem_rd(BAR0))["status"]         # assigned, MSE 0
        yield from m.setup(b, BAR0, BAR2)
        yield from h.mem_wr(BAR0 + 0xFFC, [0x11223344])
        got["edge"] = (yield from h.mem_rd(BAR0 + 0x1000))["status"]
        got["bar2"] = (yield from h.mem_rd(BAR2 + 12))["status"]
        yield from h.bar0_rd(BAR0 + 0x40, 64)
        yield from m.wait(lambda: not h.bar0)

    run(b, prog())
    assert got == {"before": tlm.UR, "mse_off": tlm.UR, "edge": tlm.UR, "bar2": tlm.SC}
    writes = [p for p in b.app.cq_got if p["write"]]
    assert [(p["addr"], p["data"]) for p in writes] == [(0xFFC, (0x11223344).to_bytes(4, "little"))]
    assert h.reads_done == 1 and h.read_errors == 0


@pytest.mark.parametrize("start,size,parts,ok", [
    (4, 512, [(4, 124), (128, 128), (256, 128), (384, 128), (512, 4)], True),
    (4, 512, [(4, 60), (64, 64), (128, 64), (192, 64), (256, 64), (320, 64), (384, 64),
              (448, 64), (512, 4)], False),                 # split at every boundary
    (4, 512, [(4, 128), (132, 128), (260, 128), (388, 128)], False),  # not on a boundary
    (4, 128, [(4, 128)], True),
    (4, 128, [(4, 124), (128, 4)], False),                 # an unneeded split
    (40, 100, [(40, 24), (64, 76)], False),                # the same, 100 bytes at 40
    (40, 100, [(40, 100)], True),
])
def test_host_checks_splitting(start, size, parts, ok):
    """The host stand-in checks each BAR0 read completion by rule: a split
    only where needed (more than 128 bytes left), ending at the highest
    64-byte boundary within 128 bytes (tl COMPLETIONS.md)."""
    h = m.Host(0, 12, 1, 0)
    h.bar0[7] = {"next": start, "left": size, "size": size}
    left = size
    for addr, n in parts:
        data = b"".join(tlm.App.pattern((addr + 4 * i) & 0xFFF).to_bytes(4, "little")
                        for i in range(n // 4))
        h.receive(tlm.cpl_tlp(0x100, tlm.SC, left, m.HOST_RID, 7, addr & 0x7F, data), 0)
        left -= n
        if 7 not in h.bar0:
            break
    assert (h.read_errors == 0 and h.reads_done == 1) is ok


@pytest.mark.parametrize("field", ["bc", "la", "data", "status"])
def test_host_checks_completion_fields(field):
    h = m.Host(0, 12, 1, 0)
    h.bar0[1] = {"next": 0, "left": 8, "size": 8}
    data = b"".join(tlm.App.pattern(4 * i).to_bytes(4, "little") for i in range(2))
    kw = {"bc": 8, "la": 0, "data": data, "status": tlm.SC}
    kw[field] = {"bc": 12, "la": 4, "data": bytes(8), "status": tlm.CA}[field]
    h.receive(tlm.cpl_tlp(0x100, kw["status"], kw["bc"], m.HOST_RID, 1, kw["la"], kw["data"]), 0)
    assert h.read_errors == 1


@pytest.mark.parametrize("w,frac,floor", [
    ({"host": "write"}, 0.98, 0.833),
    ({"app": "write"}, 0.98, 0.833),
    ({"host": "read"}, 0.98, 0.745),
    ({"app": "read"}, 0.98, 0.745),
    ({"host": "write", "app": "write"}, 0.98, 0.745),
])
def test_throughput_targets(w, frac, floor):
    """The core HAS throughput targets, on a shorter run than the
    scenarios: at least 0.98 of the link payload rate and of the link
    target as a fraction of the line."""
    r = m.perf({"kind": "traffic", "tlps": 300, **w}, 1)
    assert r["fraction_of_link"] >= frac
    assert r["payload_line_fraction"] >= floor
    assert r["completion_errors"] == 0 and r["tl_errors"] == 0


@pytest.mark.parametrize("size,offset,cpls", [(512, 4, 5), (128, 4, 1), (100, 40, 1)])
def test_split_reads(size, offset, cpls):
    """A 512-byte host read at offset 4 comes back in 5 completions; a read
    whose rest fits in 128 bytes in one, even across a 64-byte boundary."""
    r = m.perf({"kind": "traffic", "host": "read", "tlps": 20, "bytes": size, "offset": offset}, 1)
    assert (r["completions_per_read"], r["completion_errors"], r["host_reads_done"]) == (cpls, 0, 20)


def test_msi_follows_the_data():
    """An interrupt write goes out after every posted request the
    application had taken on rq before it, with writes still queued in tl
    when the interrupt is raised (TL_CFG messages)."""
    r = m.perf({"kind": "interrupts", "mode": "msi", "count": 10, "burst": 4}, 1)
    assert r["interrupt_writes"] == r["reported_sent"] == 10
    assert r["interrupts_wrong"] == r["interrupt_overtakes"] == 0


def test_msix_masked_pending_unmask():
    """Masked vectors pend, the pending bits read through BAR2, unmasking
    sends one write per vector and clears its bit (TL_CFG interrupt
    resolution)."""
    r = m.perf({"kind": "interrupts", "mode": "msix", "vectors": 8}, 1)
    assert r["reported_pending"] == r["pending_bits_seen"] == 8
    assert r["pending_bits_after"] == 0
    assert r["interrupt_writes"] == 16 and r["interrupts_wrong"] == 0


def test_unsupported_request():
    """I/O, a memory read just past BAR0, a memory write hitting no BAR, and a
    configuration read to function 1: three UR completions, Unsupported
    Request Detected, ERR_COR for each non-posted one (advisory), ERR_NONFATAL
    for the posted one, and BAR0 still served."""
    r = m.perf({"kind": "unsupported", "rounds": 1}, 1)
    assert r["ur_completions"] == 3 and r["ur_completion_errors"] == 0
    assert (r["ur_posted_events"], r["ur_nonposted_events"], r["ur_detected"]) == (1, 3, 1)
    assert (r["err_cor_messages"], r["err_nonfatal_messages"], r["err_fatal_messages"]) == (3, 1, 0)
    assert r["bar0_reads_after"] == 1 and r["completion_errors"] == 0


def test_transactions_pending():
    """Device Status Transactions Pending shows tl_np_pending: 1 while an
    application read awaits its completion, 0 once it is done (TL_CFG
    requester state)."""
    b = bench(host_latency=3000)
    h, app = b.host, b.app
    seen = []

    def prog():
        yield from m.setup(b, BAR0, BAR2)
        seen.append((yield from h.cfg_rd(cfgm.CAP_PCIE + 8)) >> 21 & 1)
        app.rq.append({"write": False, "addr": m.HOST_MEM, "len": 32, "fbe": 0xF, "lbe": 0xF,
                       "tag": 3})
        yield from m.wait(lambda: not app.rq)
        seen.append((yield from h.cfg_rd(cfgm.CAP_PCIE + 8)) >> 21 & 1)
        yield from m.wait(lambda: app.done_reads)
        seen.append((yield from h.cfg_rd(cfgm.CAP_PCIE + 8)) >> 21 & 1)

    run(b, prog())
    assert seen == [0, 1, 0]


def test_slot_power_limit_reaches_device_capabilities():
    """A Set_Slot_Power_Limit from the host reaches Device Capabilities
    through the slot power event: Captured Slot Power Limit Value and Scale
    read back over the link; a link down clears them (TL_CFG slot power
    limit)."""
    b = bench()
    h = b.host
    seen = []

    def prog():
        yield from m.setup(b, BAR0, BAR2)
        seen.append((yield from h.cfg_rd(cfgm.CAP_PCIE + 4)))
        yield tlm.msg_tlp(tlm.MSG_SET_SLOT_POWER, tlm.R_LOCAL, m.HOST_RID,
                          bytes([0x4B, 0x02, 0, 0]))
        seen.append((yield from h.cfg_rd(cfgm.CAP_PCIE + 4)))

    run(b, prog())
    assert seen == [1 << 15, 1 << 15 | 0x4B << 18 | 2 << 26]
    b.link_up = [False, False]
    for _ in range(50):
        b.step()
    assert b.core.cfg.cfg_read(cfgm.CAP_PCIE + 4) == 1 << 15


def test_link_down_resets_tl_and_cfg():
    """dl_up falling resets tl and cfg: BARs, enables and bus number back
    to reset, queues empty; the MSI-X table address and data are kept
    (core HAS, Link down). The host enumerates again and BAR0 works."""
    b = bench()
    h = b.host

    def prog():
        yield from m.setup(b, BAR0, BAR2)
        yield from h.mem_wr(BAR2, [m.MSI_ADDR, 0, 0x77, 0])

    run(b, prog())
    for _ in range(200):
        b.step()
    c = b.core.cfg
    assert c.bar0_base == BAR0 and c.table[0][:3] == [m.MSI_ADDR, 0, 0x77]
    b.link_up = [False, False]
    for _ in range(50):
        b.step()
    assert (c.bar0_base, c.mem_space_en, c.bus_num, c.table[0][3]) == (0, False, 0, 1)
    assert not b.core.tl.pq and not b.core.tl.handles
    assert c.table[0][:3] == [m.MSI_ADDR, 0, 0x77]
    b.link_up = [True, True]

    def again():
        yield from m.setup(b, BAR0, BAR2)
        yield from h.bar0_rd(BAR0, 128)
        yield from m.wait(lambda: not h.bar0)

    run(b, again())
    assert h.reads_done == 1 and h.read_errors == 0


def test_no_stale_error_pulse_after_link_down():
    """Error events while dl_up is 0 reach a cfg held in reset and are
    lost (Q10): none is pulsed into cfg when the link comes back."""
    b = bench()
    run(b, m.setup(b, BAR0, BAR2))
    b.link_up = [False, False]
    for _ in range(20):
        b.step()
    b.b.err(0)                                   # a Bad TLP counted while down
    b.core.tl.err(tlm.E_MALFORMED)
    b.link_up = [True, True]
    while not b.b.dl_up:
        b.step()
    for _ in range(20):
        b.step()
    ds = b.core.cfg.cfg_read(cfgm.CAP_PCIE + 8)
    assert ds & (cfgm.DS_CED | cfgm.DS_FED) == 0


@pytest.mark.parametrize("w", [
    {"kind": "derived"},
    {"kind": "traffic"},
    {"kind": "traffic", "host": "copy"},
    {"kind": "traffic", "host": "write", "bytes": 256},
    {"kind": "traffic", "app": "read", "bytes": 512},
    {"kind": "traffic", "host": "read", "offset": 4000, "bytes": 128},
    {"kind": "traffic", "host": "write", "tlps": 0},
    {"kind": "traffic", "host": "write", "speed": 1},
    {"kind": "interrupts", "mode": "intx"},
    {"kind": "enumeration", "trials": True},
])
def test_bad_workloads_raise(w):
    with pytest.raises(ValueError):
        m.perf(w, 1)
