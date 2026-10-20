"""Back-to-back e2e of pcie_core against the independent host model
(wiki/src/dv/core/DV_PLAN.md).

The endpoint and the host's link sit on the top pcie_core_e2e_tb; the
independent host model drives the host link and the application model the
endpoint's application interface (tb/core_e2e_env.py). Every test brings
the link up, has the host enumerate the function, runs its scenario, drains
and passes the environment's final checks. The host model judges every
completion, interrupt and endpoint request; the cfg architecture model
shadows every configuration read and error message; the tests add the
expectations of the core, tl and cfg HAS pages on top.
"""

import hashlib
import os
import random
import sys
from pathlib import Path

import cocotb
from cocotb.triggers import FallingEdge

from tb.core_e2e_env import (COV_ENUM, COV_IRQ, EP_BDF, HIGH_MEM, HOST_MEM, HOST_RID,
                             IRQ_DROPPED, IRQ_PENDING, IRQ_SENT, MSI_ADDR, RC_CA, RC_FAILED,
                             RC_SC, RC_UR, Env, hm, ht)

sys.path.append(str(Path(__file__).resolve().parents[3] / "arch" / "core"))
import pcie_core_arch as arch  # noqa: E402  (the link rate the targets compare with)

# Configuration offsets this design uses (cfg REGISTERS and CAPS pages).
CFG_COMMAND = 0x04
CAP_PM, CAP_MSI, CAP_MSIX, CAP_PCIE = 0x40, 0x50, 0x60, 0x70
DEVCAP = CAP_PCIE + 0x04
DEVCTL = CAP_PCIE + 0x08            # Device Control [15:0], Device Status [31:16]
DEVCTL2 = CAP_PCIE + 0x28
CMD_MSE, CMD_BME, CMD_PER, CMD_SERR = 1 << 1, 1 << 2, 1 << 6, 1 << 8
STS_MDPE, STS_RTA, STS_RMA, STS_SSE, STS_DPE = 1 << 24, 1 << 28, 1 << 29, 1 << 30, 1 << 31
DC_ALL = 0xF                        # Correctable, Non-Fatal, Fatal, UR Reporting Enables
DS_CED, DS_NFED, DS_FED, DS_URD, DS_TP = 1 << 16, 1 << 17, 1 << 18, 1 << 19, 1 << 21
LINE_BYTES_PER_CYCLE = 2            # 2.5 GT/s, 8b/10b, 125 MHz: two symbols a cycle


def seed() -> int:
    return cocotb.RANDOM_SEED


async def up(dut, host_kw=None, test_seed=None) -> Env:
    env = Env(dut, seed() if test_seed is None else test_seed, host_kw=host_kw)
    env.start()
    await env.link_up()
    return env


async def enumerate_ep(env: Env, bus_master: bool = True) -> hm.DeviceInfo:
    """Enumeration (goal item 4) with the HAS checks of what it found."""
    dev = await env.enumerate(bus_master=bus_master)
    p = env.p
    want = {"vendor_id": p["vendor_id"], "device_id": p["device_id"],
            "revision": p["revision_id"], "class_code": p["class_code"], "header_type": 0,
            "subsys_vendor_id": p["subsys_vendor_id"], "subsys_id": p["subsys_id"]}
    for k, v in want.items():
        assert getattr(dev, k) == v, f"enumeration: {k} {getattr(dev, k):#x}, expected {v:#x}"
    COV_ENUM.sample(step="ids")
    caps = [(0x01, CAP_PM), (0x05, CAP_MSI), (0x11, CAP_MSIX), (0x10, CAP_PCIE)]
    assert dev.caps == caps, f"capability list {dev.caps}, expected {caps}"
    for s in ("cap_pm", "cap_msi", "cap_msix", "cap_pcie"):
        COV_ENUM.sample(step=s)
    assert dev.ext_caps == [], f"extended capabilities {dev.ext_caps}, expected none"
    assert dev.pcie_port_type == 0, f"PCI Express port type {dev.pcie_port_type}: an Endpoint is 0"
    assert dev.mps_supported == 128, f"Max_Payload_Size Supported {dev.mps_supported}, expected 128"
    assert dev.msi is not None and dev.msi.is_64 and dev.msi.multiple_capable == 0 \
        and not dev.msi.per_vector_mask, f"MSI capability {dev.msi}"
    mx = dev.msix
    assert mx is not None and (mx.table_size, mx.table_bir, mx.table_offset, mx.pba_bir,
                               mx.pba_offset) == (env.msix_n, 2, 0, 2, 0x800), f"MSI-X {mx}"
    assert sorted(dev.bars) == [0, 2], f"BARs {sorted(dev.bars)}: BAR0 and BAR2 only"
    b0, b2 = dev.bars[0], dev.bars[2]
    assert (b0.size, b0.is_64, b0.prefetchable, b0.io) == (1 << env.bar0_addr_w, False, False,
                                                            False), f"BAR0 {b0}"
    assert (b2.size, b2.is_64, b2.prefetchable, b2.io) == (4096, False, False, False), \
        f"BAR2 {b2}"
    COV_ENUM.sample(step="bar0")
    COV_ENUM.sample(step="bar2")
    assert dev.rom_size == 0, f"Expansion ROM of {dev.rom_size} bytes, expected none"
    await env.cycles(4)
    assert env.mem_space_en and env.bus_master_en == bus_master, (
        f"after enumeration: mem_space_en_o {int(env.mem_space_en)}, bus_master_en_o "
        f"{int(env.bus_master_en)}")
    assert env.rd["max_payload_o"]() == 0 and env.rd["max_read_req_o"]() == 2, (
        f"max_payload_o {env.rd['max_payload_o']()}, max_read_req_o {env.rd['max_read_req_o']()}")
    assert env.d_state == 0
    COV_ENUM.sample(step="enables")
    return dev


async def cfg_rd(env: Env, reg: int) -> int:
    req = await env.request(env.host.model.cfg_read(EP_BDF, reg), what=f"cfg read {reg:#x}")
    assert req.status == "SC", f"configuration read {reg:#x}: status {req.status}"
    return req.value


async def cfg_wr(env: Env, reg: int, value: int, be: int = 0xF) -> None:
    req = await env.request(env.host.model.cfg_write(EP_BDF, reg, value, be),
                            what=f"cfg write {reg:#x}")
    assert req.status == "SC", f"configuration write {reg:#x}: status {req.status}"


async def enable_reporting(env: Env, serr: bool = True, per: bool = False) -> None:
    """Every Device Control error reporting enable, and SERR# (and Parity
    Error Response) in the Command register."""
    ctl = await cfg_rd(env, DEVCTL)
    await cfg_wr(env, DEVCTL, (ctl & 0xFFFF) | DC_ALL, 0b0011)
    cmd = CMD_MSE | CMD_BME | (CMD_SERR if serr else 0) | (CMD_PER if per else 0)
    await cfg_wr(env, CFG_COMMAND, cmd, 0b0011)


def msgs(env: Env, since: int) -> list[int]:
    return [m.code for m in env.host.model.messages[since:]]


def tl_errs(env: Env, bit: int) -> int:
    return env.tl_err_count[bit] - getattr(env, "phase_tl_baseline", [0] * 9)[bit]


async def host_mem_read(env: Env, addr: int, n: int) -> hm.Request:
    req = await env.request(env.host.model.mem_read(addr, n), what=f"read {addr:#x}+{n}")
    return req


async def app_wait(env: Env, items, limit: int = 50_000) -> None:
    await env.until(lambda: all(r.done for r in items), limit, "application reads")


# ------------------------------------------------------------------- L1

async def phase_e2e_enumeration(env):
    """Goal item 4: the host reads the IDs, walks the capabilities, sizes and
    assigns both BARs, enables memory space and bus mastering, captures a
    Set_Slot_Power_Limit, then configures MSI and MSI-X and receives an
    interrupt through each (every MSI-X vector)."""
    t0 = env.cycle
    dev = await enumerate_ep(env)
    env.log.info("enumerated in %d cycles", env.cycle - t0)
    h = env.host.model
    # Neither MSI nor MSI-X enabled: the request is dropped (no INTx).
    r = env.app.irq(0)
    await env.until(lambda: r["status"] is not None, 200, "irq without MSI")
    assert r["status"] == IRQ_DROPPED, f"irq with no interrupt mode: status {r['status']}"
    # Set_Slot_Power_Limit: value and scale captured in Device Capabilities.
    val, scale = env.rng.randrange(256), env.rng.randrange(4)
    h.send_message(ht.MSG_SET_SLOT_POWER_LIMIT, ht.ROUTE_LOCAL, bytes([val, 0xFC | scale, 0, 0]))
    devcap = await cfg_rd(env, DEVCAP)
    assert (devcap >> 18 & 0xFF, devcap >> 26 & 3) == (val, scale), (
        f"Device Capabilities {devcap:#010x}: captured slot power {devcap >> 18 & 0xFF:#x} scale "
        f"{devcap >> 26 & 3}, sent {val:#x} scale {scale}")
    # MSI: one interrupt.
    await env.run(h.setup_msi(dev, MSI_ADDR, 0x4A5B), what="MSI set-up")
    env.irq_mode = "msi"
    COV_ENUM.sample(step="msi")
    r = env.app.irq(env.rng.randrange(env.msix_n))  # ignored under MSI
    await env.until(lambda: len(h.interrupts) == 1, 2000, "MSI interrupt")
    irq = h.interrupts[0]
    assert (irq.kind, irq.address, irq.data, irq.bdf) == ("msi", MSI_ADDR, 0x4A5B, dev.id), irq
    await env.until(lambda: r["status"] is not None, 200, "MSI done")
    assert r["status"] == IRQ_SENT, f"MSI request: status {r['status']}"
    # MSI-X: every vector, each with its own address and data.
    vecs = {v: (MSI_ADDR + 0x100 + 4 * v, 0xC0DE0000 | v) for v in range(env.msix_n)}
    await env.run(h.setup_msix(dev, vecs), what="MSI-X set-up")
    env.irq_mode = "msix"
    COV_ENUM.sample(step="msix")
    order = list(range(env.msix_n))
    env.rng.shuffle(order)
    reqs = [env.app.irq(v) for v in order]
    await env.until(lambda: len(h.interrupts) == 1 + env.msix_n, 20 * env.msix_n + 2000,
                    "MSI-X interrupts")
    got = [(i.kind, i.vector, i.address, i.data) for i in h.interrupts[1:]]
    want = [("msix", v, *vecs[v]) for v in order]
    assert got == want, f"MSI-X interrupts {got[:4]}..., expected {want[:4]}..."
    await env.until(lambda: all(q["status"] is not None for q in reqs), 500, "MSI-X done")
    assert all(q["status"] == IRQ_SENT for q in reqs), [q["status"] for q in reqs]
    await env.drain()
    env.finish()


@cocotb.test()
async def e2e_enumeration(dut):
    """Standalone recipe; L1 shares its fixture in the acceptance scenario."""
    env = await up(dut)
    await phase_e2e_enumeration(env)


async def phase_e2e_bar_traffic(env):
    """Goal item 5, host side: memory writes and reads to BAR0 of every size
    and alignment, reads split into several completions (the HAS splitting
    examples), a zero-length read, with the application stalling cq; every
    read returns the data written before it."""
    dev = env.dev
    h, rng = env.host.model, env.rng
    env.app.cq_ready_p = 0.7
    size = 1 << env.bar0_addr_w
    base = dev.bars[0].base
    mirror = bytearray(size)
    # The HAS splitting examples: (offset, bytes, completions).
    examples = [(0x40, 128, 1), (4, 128, 1), (4, 512, 5), (0, 4096, 32), (4, 256, 3),
                (0x7C, 8, 1)]
    for off, n, cpls in examples:
        data = bytes(rng.getrandbits(8) for _ in range(n))
        h.mem_write(base + off, data)
        mirror[off:off + n] = data
        req = await host_mem_read(env, base + off, n)
        assert req.ok and bytes(req.data) == data, (
            f"read {off:#x}+{n}: status {req.status}, data mismatch "
            f"{bytes(req.data)[:8].hex()} vs {data[:8].hex()}")
        assert len(req.completion_sizes) == cpls, (
            f"read {off:#x}+{n}: {len(req.completion_sizes)} completions "
            f"{req.completion_sizes}, the HAS splits it into {cpls}")
    # Zero-length read: one completion, the ep answers with one DW.
    req = await host_mem_read(env, base + 0x100, 0)
    assert req.ok and len(req.completion_sizes) == 1, f"zero-length read: {req}"
    # Random writes and reads, several outstanding.
    pend = []
    for k in range(48):
        off = rng.randrange(size - 1)
        n = rng.choice((1, 2, 3, 4, rng.randint(1, 128), rng.randint(1, 600)))
        n = min(n, ((off | 0xFFF) + 1) - off)
        if rng.random() < 0.5:
            data = bytes(rng.getrandbits(8) for _ in range(n))
            h.mem_write(base + off, data)
            mirror[off:off + n] = data
        else:
            pend.append((off, n, bytes(mirror[off:off + n]), h.mem_read(base + off, n)))
        if len(pend) >= 6 or k == 47:
            await env.until(lambda: all(p[3].done for p in pend), 50_000, "host reads")
            for off_, n_, want, r in pend:
                assert r.ok and bytes(r.data) == want, (
                    f"read {off_:#x}+{n_}: status {r.status}, got {bytes(r.data)[:8].hex()}..., "
                    f"expected {want[:8].hex()}...")
            pend = []
    await env.drain()
    assert bytes(env.app.bar0) == bytes(mirror), "application BAR0 differs from the host's writes"
    env.finish()


@cocotb.test()
async def e2e_bar_traffic(dut):
    """Standalone recipe; L1 shares its fixture in the acceptance scenario."""
    env = await up(dut)
    await enumerate_ep(env)
    await phase_e2e_bar_traffic(env)


async def phase_e2e_bus_master(env):
    """Goal item 5, application side: rq writes to host memory with 3-DW and
    4-DW headers, then rq reads of the same bytes, up to eight outstanding,
    answered with the fewest completions and then one per 64-byte block;
    the application stalls rc."""
    h, app, rng = env.host.model, env.app, env.rng
    app.rc_ready_p = 0.7
    for split in ("fewest", "rcb"):
        h.cpl_split = split
        writes = []
        for k in range(24):
            hi = HIGH_MEM if k % 3 == 2 else 0
            addr = hi + HOST_MEM + rng.randrange(0x4000)
            n = min(rng.choice((1, 4, rng.randint(1, 128), 128)), ((addr | 0xFFF) + 1) - addr)
            n = min(n, 128 - (addr & 3))
            data = bytes(rng.getrandbits(8) for _ in range(n))
            writes.append((addr, data))
            app.write(addr, data)
        await env.until(lambda: not app.rq_q and app.rq_cur is None, 20_000, "rq writes taken")
        reads = [(a, d, app.read(a, len(d))) for a, d in writes]
        await app_wait(env, [r for _, _, r in reads])
        # Later writes may overlap earlier ones: the expected bytes are the
        # host memory after all writes, which the read follows in rq order.
        for a, d, r in reads:
            want = h.memory.read(a, len(d))
            assert r.status == RC_SC and app.read_bytes(r) == want, (
                f"rq read {a:#x}+{len(d)} ({split}): status {r.status}, got "
                f"{app.read_bytes(r)[:8].hex()}..., host memory {want[:8].hex()}...")
        last = {}
        for a, d in writes:
            for i, b in enumerate(d):
                last[a + i] = b
        bad = [a for a, b in last.items() if h.memory.read(a, 1)[0] != b]
        assert not bad, f"host memory differs from the rq writes at {len(bad)} bytes"
    await env.drain()
    env.finish()


@cocotb.test()
async def e2e_bus_master(dut):
    """Standalone recipe; L1 shares its fixture in the acceptance scenario."""
    env = await up(dut)
    await enumerate_ep(env)
    await phase_e2e_bus_master(env)


async def phase_e2e_msi(env):
    """Interrupt ordering and pending under MSI: four 128-byte rq writes,
    then an interrupt raised as soon as the last is taken, whose write
    reaches the host after the data; with bus mastering off a request is
    pending and its write follows once bus mastering is enabled; repeated
    requests while pending give one write."""
    dev = env.dev
    h, app, rng = env.host.model, env.app, env.rng
    await env.run(h.setup_msi(dev, MSI_ADDR, 0x1234), what="MSI set-up")
    env.irq_mode = "msi"
    for rnd in range(6):
        data = [bytes(rng.getrandbits(8) for _ in range(128)) for _ in range(4)]
        addr = HOST_MEM + 0x1000 * rnd
        seen = []
        h.on_interrupt = lambda irq, a=addr: seen.append(h.memory.read(a, 512))
        reqs = [app.write(addr + 128 * k, data[k]) for k in range(4)]
        await env.until(lambda: reqs[-1]["taken"] is not None, 5000, "last write taken")
        r = app.irq(0)
        await env.until(lambda: len(seen) == 1 and r["status"] is not None, 5000, "MSI")
        assert r["status"] == IRQ_SENT, f"round {rnd}: status {r['status']}"
        assert seen[0] == b"".join(data), (
            f"round {rnd}: the MSI write reached the host before the data written before it")
    h.on_interrupt = None
    n0 = len(h.interrupts)
    # Bus mastering off: pending, and several requests merge into one write.
    await cfg_wr(env, CFG_COMMAND, CMD_MSE, 0b0011)
    reqs = [app.irq(0) for _ in range(3)]
    await env.until(lambda: all(q["status"] is not None for q in reqs), 1000, "pending")
    assert [q["status"] for q in reqs] == [IRQ_PENDING] * 3, [q["status"] for q in reqs]
    await env.cycles(500)
    assert len(h.interrupts) == n0, "an MSI went out with bus mastering off"
    await cfg_wr(env, CFG_COMMAND, CMD_MSE | CMD_BME, 0b0011)
    await env.until(lambda: len(h.interrupts) == n0 + 1, 2000, "pending MSI sent")
    COV_IRQ.sample(pending_sent="msi")
    await env.cycles(500)
    assert len(h.interrupts) == n0 + 1, f"{len(h.interrupts) - n0} MSI writes, expected one"
    await env.drain()
    env.finish()


@cocotb.test()
async def e2e_msi(dut):
    """Standalone recipe; L1 shares its fixture in the acceptance scenario."""
    env = await up(dut)
    await enumerate_ep(env)
    await phase_e2e_msi(env)


async def phase_e2e_msix(env):
    """MSI-X masking and pending bits: with every vector masked each request
    pends and sets its bit, read through BAR2; each unmask sends one write
    and clears its bit; Function Mask pends unmasked vectors too; unmasked
    requests go out at once."""
    dev = env.dev
    h, app, rng = env.host.model, env.app, env.rng
    irq0 = len(h.interrupts)
    n = env.msix_n
    vecs = {v: (MSI_ADDR + 8 * v, 0x5000 + v) for v in range(n)}
    await env.run(h.setup_msix(dev, vecs, unmask=False), what="MSI-X set-up, masked")
    env.irq_mode = "msix"
    pick = sorted(rng.sample(range(n), max(1, min(n, rng.randint(n // 2, n)))))
    reqs = [app.irq(v) for v in pick]
    await env.until(lambda: all(q["status"] is not None for q in reqs), 200 * n, "masked")
    assert all(q["status"] == IRQ_PENDING for q in reqs), [q["status"] for q in reqs]
    pba = await env.run(h.read_msix_pba(dev), what="PBA read")
    want = sum(1 << v for v in pick)
    assert pba == want, f"PBA {pba:#x}, expected {want:#x}"
    assert len(h.interrupts) == irq0, "an interrupt went out for a masked vector"
    order = list(pick)
    rng.shuffle(order)
    for v in order:
        before = len(h.interrupts)
        h.msix_mask(dev, v, False)
        await env.until(lambda: len(h.interrupts) == before + 1
                        and h.interrupts[-1].vector == v, 2000,
                        f"vector {v} after unmask")
        COV_IRQ.sample(pending_sent="msix")
    got = [i.vector for i in h.interrupts[irq0:]]
    assert got == order, f"interrupts {got}, expected one per unmask in order {order}"
    pba = await env.run(h.read_msix_pba(dev), what="PBA read")
    assert pba == 0, f"PBA {pba:#x} after every unmask, expected 0"
    # Function Mask: every vector pends, sent when it clears.
    for v in range(n):
        h.msix_mask(dev, v, False)
    req = h.msix_function_mask(dev, True)
    await env.request(req, what="Function Mask set")
    n0 = len(h.interrupts)
    v = rng.randrange(n)
    r = app.irq(v)
    await env.until(lambda: r["status"] is not None, 500, "function-masked request")
    assert r["status"] == IRQ_PENDING and len(h.interrupts) == n0
    await env.request(h.msix_function_mask(dev, False), what="Function Mask clear")
    await env.until(lambda: len(h.interrupts) == n0 + 1, 2000, "sent after Function Mask")
    assert h.interrupts[-1].vector == v
    # Unmasked: sent at once, each its own write.
    order = [rng.randrange(n) for _ in range(2 * n)]
    reqs = [app.irq(v) for v in order]
    await env.until(lambda: len(h.interrupts) == n0 + 1 + len(order), 100 * len(order) + 2000,
                    "unmasked interrupts")
    assert [i.vector for i in h.interrupts[n0 + 1:]] == order
    assert all(q["status"] == IRQ_SENT for q in reqs)
    await env.drain()
    env.finish()


@cocotb.test()
async def e2e_msix(dut):
    """Standalone recipe; L1 shares its fixture in the acceptance scenario."""
    env = await up(dut)
    await enumerate_ep(env)
    await phase_e2e_msix(env)


async def phase_e2e_unsupported(env):
    """Unsupported Request (goal items 5, 6): an I/O read, a memory read past
    the BARs, a memory write hitting no BAR, Type 0 configuration reads to
    function 1 and a Type 1 read: UR completions for the non-posted ones,
    Unsupported Request Detected, ERR_COR (advisory) and ERR_NONFATAL with
    reporting on, nothing with it off, and BAR0 still served; a BAR0 read
    with Memory Space Enable 0 is an Unsupported Request too."""
    dev = env.dev
    h = env.host.model
    base = dev.bars[0].base
    past = dev.bars[2].base + 4096                 # past the last BAR
    ep = dev.id

    def raw_np():
        return [
            ht.Tlp(fmt=ht.FMT_3DW, type=ht.TYPE_IO, length=1, requester_id=HOST_RID,
                   first_be=0xF, address=0x1000),
            ht.cfg_read(HOST_RID, 0, 1, 0, 1, 0x00),               # function 1
            ht.cfg_read(HOST_RID, 0, 1, 0, 0, 0x00, type1=True),   # Type 1
        ]

    for reporting in (False, True):
        if reporting:
            await enable_reporting(env)
        m0, ur0, np0 = len(h.messages), tl_errs(env, 0), tl_errs(env, 1)
        reqs = [h.send_request(t) for t in raw_np()]
        rd = h.mem_read(past, 8)
        reqs.append(rd)
        await env.until(lambda: all(r.done for r in reqs), 5000, "UR completions")
        assert all(r.status == "UR" for r in reqs), [r.status for r in reqs]
        h.mem_write(past + 0x40, b"\x01\x02\x03\x04")           # posted, hits no BAR
        await env.until(lambda: tl_errs(env, 0) == ur0 + 1, 2000, "posted UR event")
        assert tl_errs(env, 1) == np0 + 4, f"{tl_errs(env, 1) - np0} non-posted UR events"
        ds = await cfg_rd(env, DEVCTL)
        assert ds & DS_URD, f"Device Status {ds >> 16:#06x}: Unsupported Request Detected 0"
        await env.cycles(300)
        got = msgs(env, m0)
        if reporting:
            assert ht.MSG_ERR_COR in got and ht.MSG_ERR_NONFATAL in got and \
                ht.MSG_ERR_FATAL not in got, f"messages {[hex(c) for c in got]}"
            ds = await cfg_rd(env, CFG_COMMAND)
            assert ds & STS_SSE, "Signaled System Error 0 after ERR_NONFATAL with SERR# on"
        else:
            assert not got, f"messages {[hex(c) for c in got]} with reporting off"
        # Clear the Device Status bits for the next round (RW1C).
        await cfg_wr(env, DEVCTL, 0xF << 16, 0b1100)
        # BAR0 still served.
        data = bytes(env.rng.getrandbits(8) for _ in range(64))
        h.mem_write(base + 0x80, data)
        r = await host_mem_read(env, base + 0x80, 64)
        assert r.ok and bytes(r.data) == data, "BAR0 not served after Unsupported Requests"
    assert ep == dev.id
    # Memory Space Enable off: BAR0 no longer decodes (tl-cfg BAR hit).
    await cfg_wr(env, CFG_COMMAND, CMD_BME | CMD_SERR, 0b0011)
    r = await host_mem_read(env, base, 4)
    assert r.status == "UR", f"BAR0 read with Memory Space Enable 0: status {r.status}"
    await cfg_wr(env, CFG_COMMAND, CMD_MSE | CMD_BME | CMD_SERR, 0b0011)
    r = await host_mem_read(env, base, 4)
    assert r.ok, f"BAR0 read with Memory Space Enable 1 again: status {r.status}"
    await env.drain()
    env.finish()


@cocotb.test()
async def e2e_unsupported(dut):
    """Standalone recipe; L1 shares its fixture in the acceptance scenario."""
    env = await up(dut)
    await enumerate_ep(env)
    await phase_e2e_unsupported(env)


async def phase_e2e_cpl_timeout(env):
    """Completion timeout (goal items 5, 6): an rq read the host never
    answers ends with rc status failed after the tl timeout, with Device
    Status Transactions Pending 1 meanwhile, ERR_NONFATAL and Non-Fatal
    Error Detected; its late completion is unexpected (ERR_COR, advisory);
    with Completion Timeout Disable set the read waits, and gets a fresh
    timeout when the bit clears; other reads complete meanwhile."""
    await enable_reporting(env)
    h, app = env.host.model, env.app
    tmo = env.cpl_timeout
    lost = HOST_MEM + 0x800
    env.host.drop_rd = lambda t: t.address == lost
    m0 = len(h.messages)
    r = app.read(lost, 16)
    ok = [app.read(HOST_MEM + 64 * k, 64) for k in range(3)]
    await env.until(lambda: r.issued_at is not None, 1000, "lost read sent")
    await app_wait(env, ok)
    assert all(x.status == RC_SC for x in ok)
    ds = await cfg_rd(env, DEVCTL)
    assert ds & DS_TP, f"Device Status {ds >> 16:#06x}: Transactions Pending 0 with a read out"
    await env.until(lambda: r.done, tmo + tmo // 16 + 2000, "completion timeout")
    waited = r.done_at - r.issued_at
    assert r.status == RC_FAILED, f"timed-out read: rc status {r.status}"
    # tl COMPLETIONS: a read times out between 16P and 17P cycles after the
    # link takes it, P = 2^21 / (16 CPL_TIMEOUT_DIV); rc adds a few cycles.
    assert tmo <= waited <= tmo + tmo // 16 + 200, (
        f"timeout after {waited} cycles; tl times out between {tmo} and {tmo + tmo // 16} "
        "cycles (16P to 17P)")
    assert tl_errs(env, 6) == 1
    await env.cycles(300)
    assert ht.MSG_ERR_NONFATAL in msgs(env, m0), [hex(c) for c in msgs(env, m0)]
    ds = await cfg_rd(env, DEVCTL)
    assert ds & DS_NFED and not ds & DS_TP, f"Device Status {ds >> 16:#06x} after the timeout"
    # The late completion: unexpected, advisory, ERR_COR.
    t = env.host.dropped[-1]
    await cfg_wr(env, DEVCTL, 0xF << 16, 0b1100)       # clear Device Status (RW1C)
    ds = await cfg_rd(env, DEVCTL)
    assert not ds & DS_CED, f"Device Status {ds >> 16:#06x}: not cleared"
    m1 = len(h.messages)
    h.send_request(ht.completion(HOST_RID, t.requester_id, t.tag, ht.CPL_SC, 16, lost & 0x7F,
                                 bytes(16)))
    await env.until(lambda: tl_errs(env, 5) == 1, 2000, "unexpected completion")
    await env.cycles(300)
    assert msgs(env, m1) == [ht.MSG_ERR_COR], [hex(c) for c in msgs(env, m1)]
    ds = await cfg_rd(env, DEVCTL)
    assert ds & DS_CED, (f"Device Status {ds >> 16:#06x}: Correctable Error Detected 0 after "
                         "the unexpected completion")
    # Completion Timeout Disable: no timeout while set; a fresh one after.
    await cfg_wr(env, DEVCTL2, 1 << 4, 0b0001)
    r2 = app.read(lost, 4)
    await env.cycles(2 * tmo)
    assert not r2.done, "a read timed out with Completion Timeout Disable set"
    t_clear = env.cycle
    await cfg_wr(env, DEVCTL2, 0, 0b0001)
    await env.until(lambda: r2.done, tmo + tmo // 16 + 2000, "timeout after the disable cleared")
    assert r2.status == RC_FAILED and r2.done_at - t_clear >= tmo - 200, (
        f"read ended {r2.done_at - t_clear} cycles after the disable cleared, status {r2.status}")
    env.host.drop_rd = None
    await env.drain()
    env.finish()


@cocotb.test()
async def e2e_cpl_timeout(dut):
    """Standalone recipe; L1 shares its fixture in the acceptance scenario."""
    env = await up(dut)
    await enumerate_ep(env)
    await phase_e2e_cpl_timeout(env)


async def phase_e2e_errors(env):
    """The other error classes of goal item 6: a malformed request
    (ERR_FATAL), a poisoned write to BAR0 (dropped, Detected Parity Error,
    ERR_NONFATAL), a poisoned completion to an rq read (rc failed, Master
    Data Parity Error with Parity Error Response on), Unsupported Request
    and Completer Abort completions (Received Master and Target Abort, no
    message); BAR0 and rq still work."""
    dev = env.dev
    await enable_reporting(env, per=True)
    h, app = env.host.model, env.app
    base = dev.bars[0].base
    h.strict = False                                 # raw TLPs the host would flag
    # Malformed: a BAR0 write crossing a 4 KiB boundary.
    m0 = len(h.messages)
    h.send_request(ht.mem_write(HOST_RID, base + (1 << env.bar0_addr_w) - 8, bytes(16), 0xF,
                                0xF))
    await env.until(lambda: tl_errs(env, 2) == 1, 2000, "malformed")
    await env.cycles(300)
    assert msgs(env, m0) == [ht.MSG_ERR_FATAL], [hex(c) for c in msgs(env, m0)]
    ds = await cfg_rd(env, DEVCTL)
    assert ds & DS_FED, f"Device Status {ds >> 16:#06x}: Fatal Error Detected 0"
    # Poisoned write to BAR0: dropped.
    m0 = len(h.messages)
    before = bytes(env.app.bar0[0x200:0x210])
    h.send_request(ht.mem_write(HOST_RID, base + 0x200, b"\xAA" * 16, 0xF, 0xF, ep=1))
    await env.until(lambda: tl_errs(env, 3) == 1, 2000, "poisoned request")
    await env.cycles(300)
    assert msgs(env, m0) == [ht.MSG_ERR_NONFATAL], [hex(c) for c in msgs(env, m0)]
    assert bytes(env.app.bar0[0x200:0x210]) == before, "a poisoned write reached BAR0"
    sts = await cfg_rd(env, CFG_COMMAND)
    assert sts & STS_DPE and sts & STS_SSE, f"Status {sts >> 16:#06x} after a poisoned request"
    # Poisoned completion to an rq read.
    m0 = len(h.messages)
    addr = HOST_MEM + 0x400
    env.host.drop_rd = lambda t: t.address == addr
    dropped0 = len(env.host.dropped)
    r = app.read(addr, 32)
    await env.until(lambda: len(env.host.dropped) > dropped0, 2000, "read to poison")
    t = env.host.dropped[-1]
    h.send_request(ht.completion(HOST_RID, t.requester_id, t.tag, ht.CPL_SC, 32, addr & 0x7F,
                                 bytes(32), ep=1))
    await app_wait(env, [r])
    assert r.status == RC_FAILED, f"poisoned completion: rc status {r.status}"
    assert tl_errs(env, 4) == 1
    await env.cycles(300)
    assert msgs(env, m0) == [ht.MSG_ERR_NONFATAL], [hex(c) for c in msgs(env, m0)]
    sts = await cfg_rd(env, CFG_COMMAND)
    assert sts & STS_MDPE, f"Status {sts >> 16:#06x}: Master Data Parity Error 0"
    env.host.drop_rd = None
    # UR and CA completions.
    m0 = len(h.messages)
    h.read_status = lambda a, n: {HOST_MEM + 0x600: ht.CPL_UR,
                                  HOST_MEM + 0x640: ht.CPL_CA}.get(a, ht.CPL_SC)
    ru, rc = app.read(HOST_MEM + 0x600, 8), app.read(HOST_MEM + 0x640, 8)
    await app_wait(env, [ru, rc])
    assert (ru.status, rc.status) == (RC_UR, RC_CA), (ru.status, rc.status)
    assert tl_errs(env, 7) == 1 and tl_errs(env, 8) == 1
    await env.cycles(300)
    assert not msgs(env, m0), [hex(c) for c in msgs(env, m0)]
    sts = await cfg_rd(env, CFG_COMMAND)
    assert sts & STS_RMA and sts & STS_RTA, f"Status {sts >> 16:#06x}: Master or Target Abort 0"
    h.read_status = None
    h.strict = True
    # Still working.
    data = bytes(env.rng.getrandbits(8) for _ in range(32))
    h.mem_write(base + 0x300, data)
    rr = await host_mem_read(env, base + 0x300, 32)
    assert rr.ok and bytes(rr.data) == data
    app.write(HOST_MEM + 0x700, data)
    r = app.read(HOST_MEM + 0x700, 32)
    await app_wait(env, [r])
    assert r.status == RC_SC and app.read_bytes(r) == data
    errs = list(h.errors)
    h.errors.clear()
    await env.drain()
    env.log.info("host flags on the raw TLPs: %s", errs)
    env.finish()


@cocotb.test()
async def e2e_errors(dut):
    """Standalone recipe; L1 shares its fixture in the acceptance scenario."""
    env = await up(dut)
    await enumerate_ep(env)
    await phase_e2e_errors(env)


async def phase_e2e_link_errors(env):
    """Link errors into cfg (TL_CFG, link to cfg): bit flips on the host to
    ep line make the ep link pulse rcv_err_o and dl_err_o (Bad TLP or Bad
    DLLP), correctable errors: Correctable Error Detected and ERR_COR with
    reporting on, no other message; traffic across the flips stays
    lossless (the link replays). Then each wire alone: a flip on the idle
    line (rcv_err_o only) and a data symbol replaced by another valid one
    inside a packet (dl_err_o Bad TLP or Bad DLLP only), each correctable."""
    dev = env.dev
    await enable_reporting(env)
    h, rng = env.host.model, env.rng
    base = dev.bars[0].base
    dut = env.dut
    env.allow_link_errors = True
    m0 = len(h.messages)
    link0 = len(env.link_err_log)
    data = bytes(rng.getrandbits(8) for _ in range(1024))
    h.mem_write(base, data)
    for k in range(4):
        await env.cycles(rng.randint(20, 120))
        env.wr("line_flip_i", 1 << rng.randrange(20))
        await env.cycles(1)
        env.wr("line_flip_i", 0)
    await env.until(lambda: len(env.link_err_log) > link0, 2000, "a link error pulse")
    await env.drain()
    env.allow_link_errors = False
    r = await host_mem_read(env, base, 1024)
    assert r.ok and bytes(r.data) == data, "BAR0 data lost or corrupted across the line errors"
    ds = await cfg_rd(env, DEVCTL)
    assert ds & DS_CED, f"Device Status {ds >> 16:#06x}: Correctable Error Detected 0"
    got = msgs(env, m0)
    assert ht.MSG_ERR_COR in got and set(got) == {ht.MSG_ERR_COR}, (
        f"messages {[hex(c) for c in got]} for correctable link errors "
        f"{env.link_err_log[:4]}")
    # rcv_err alone: a flip on the idle line (no packet to hit) is a
    # receiver error only, correctable.
    await cfg_wr(env, DEVCTL, 0xF << 16, 0b1100)
    env.allow_link_errors = True
    m0, n0 = len(h.messages), len(env.link_err_log)
    env.wr("line_flip_i", 1 << rng.randrange(20))
    await env.cycles(1)
    env.wr("line_flip_i", 0)
    await env.until(lambda: len(env.link_err_log) > n0, 2000, "a receiver error")
    await env.drain()
    env.allow_link_errors = False
    ev = env.link_err_log[n0:]
    assert all(dl == 0 for _, dl, _ in ev) and any(rcv for _, _, rcv in ev), (
        f"a flip on the idle line: pulses (cycle, dl_err_o, rcv_err_o) {ev}; expected "
        "rcv_err_o only")
    ds = await cfg_rd(env, DEVCTL)
    assert ds & DS_CED and not ds & (DS_NFED | DS_FED), (
        f"Device Status {ds >> 16:#06x} after a receiver error alone")
    assert set(msgs(env, m0)) == {ht.MSG_ERR_COR}, [hex(c) for c in msgs(env, m0)]
    # dl_err alone: one data symbol inside a TLP on the line (between STP
    # and END, as the tapped line shows them) replaced by another valid
    # data symbol of the same disparity: its LCRC fails (Bad TLP), no
    # receiver error; correctable, and the TLP is replayed.
    await cfg_wr(env, DEVCTL, 0xF << 16, 0b1100)
    env.allow_link_errors = True
    m0, n0 = len(h.messages), len(env.link_err_log)
    for _ in range(4):
        h.mem_write(base, bytes(rng.getrandbits(8) for _ in range(1024)))
    subs, in_tlp, seen, pos = 0, False, 0, 0
    while len(env.link_err_log) == n0 and subs < 20 and seen < 20_000:
        await FallingEdge(dut.clk_i)
        seen += 1
        word = env.rd["mon_line_o"]()
        syms = env.line_symbols(word)
        if syms[0] == (0xFB, True) or syms[1] == (0xFB, True):     # STP
            in_tlp, pos = True, 0
            continue
        if any(x is not None and x[1] for x in syms):              # END, SKP, ...
            in_tlp = False
            continue
        pos += 1
        new = env.substitute_symbol(word) if in_tlp and pos == 8 else None
        if new is None:
            continue
        env.wr("line_sub_data_i", new)
        env.wr("line_sub_i", 1)
        await FallingEdge(dut.clk_i)
        env.wr("line_sub_i", 0)
        in_tlp = False
        subs += 1
        await env.cycles(300)
    await env.drain()
    env.allow_link_errors = False
    ev = env.link_err_log[n0:]
    assert ev and all(dl and dl & ~3 == 0 and rcv == 0 for _, dl, rcv in ev), (
        f"{subs} symbol substitutions: pulses (cycle, dl_err_o, rcv_err_o) {ev}; expected "
        "Bad TLP or Bad DLLP only")
    ds = await cfg_rd(env, DEVCTL)
    assert ds & DS_CED and not ds & (DS_NFED | DS_FED), f"Device Status {ds >> 16:#06x} after it"
    assert set(msgs(env, m0)) == {ht.MSG_ERR_COR}, [hex(c) for c in msgs(env, m0)]
    await env.drain()
    env.finish()


@cocotb.test()
async def e2e_link_errors(dut):
    """Standalone recipe; L1 shares its fixture in the acceptance scenario."""
    env = await up(dut)
    await enumerate_ep(env)
    await phase_e2e_link_errors(env)


# ------------------------------------------------------------------- L2

def _measure(env: Env, payload: int, span: int, both: bool, what: str) -> dict:
    frac = payload / (LINE_BYTES_PER_CYCLE * span)
    link = arch.link_payload(both)
    res = {"payload_line_fraction": round(frac, 4), "fraction_of_link": round(frac / link, 4),
           "cycles": span, "payload": payload}
    env.log.info("%s throughput: %s (link closed form %.4f)", what, res, link)
    line_min = 0.98 * (0.76 if both else 0.85)
    assert frac / link >= 0.98 and frac >= line_min, (
        f"{what}: {frac:.4f} of the line, {frac / link:.4f} of the link rate; the core HAS "
        f"targets are >= 0.98 of the link and >= {line_min:.3f} of the line")
    return res


async def _throughput(dut, kind: str, tlps: int) -> dict:
    env = await up(dut)
    dev = await enumerate_ep(env)
    h, app = env.host.model, env.app
    app.rq_gap_p = app.cc_gap_p = 0.0
    base = dev.bars[0].base
    words = [bytes(range(k, k + 128)) for k in range(4)]
    await env.cycles(100)
    env.ep_line_count = {"tlp": 0, "dllp": 0}
    env.count_line = True
    if kind in ("host_write", "bidir"):
        hb0 = env.app.cq_bytes
        hs = len(env.host.sent_log)
        for i in range(tlps):
            h.mem_write(base + (128 * i) % 4096, words[i % 4])
    if kind in ("app_write", "bidir"):
        ar0 = len(env.host.rx_log)
        for i in range(tlps):
            app.write(HOST_MEM + 128 * (i % 32), words[i % 4])
    if kind == "host_read":
        app.cc_latency = (16, 16)
        hs = len(env.host.sent_log)
        reqs = []
        for i in range(tlps):
            reqs.append(h.mem_read(base + 128 * (i % 32), 128))
    if kind == "app_read":
        reads = []
        for i in range(tlps):
            reads.append(app.read(HOST_MEM + 128 * (i % 32), 128))
    await env.drain(limit=200 * tlps + 20_000)
    env.count_line = False
    c = env.ep_line_count
    env.log.info("%s: the ep transmit line carried %d TLPs and %d DLLPs, %.3f DLLPs per TLP",
                 kind, c["tlp"], c["dllp"], c["dllp"] / max(1, c["tlp"]))
    res = {}
    if kind in ("host_write", "bidir"):
        t0 = env.host.sent_log[hs][0]
        res["host_write"] = (env.app.cq_bytes - hb0, env.app.cq_last - t0)
    if kind in ("app_write", "bidir"):
        mwr = [c for c, raw in env.host.rx_log[ar0:] if raw[0] & 0x1F == 0 and raw[0] & 0x40]
        t0 = min(r["taken"] for r in app.rq_taken[-tlps:])
        res["app_write"] = (128 * tlps, mwr[-1] - t0)
        assert len(mwr) == tlps
    if kind == "host_read":
        t0 = env.host.sent_log[hs][0]
        cpl = [c for c, raw in env.host.rx_log if raw[0] & 0x1F == 0x0A]
        assert all(r.ok for r in reqs)
        res["host_read"] = (128 * tlps, cpl[-1] - t0)
    if kind == "app_read":
        assert all(r.status == RC_SC for r in reads)
        t0 = min(r.issued_at for r in reads)
        res["app_read"] = (128 * tlps, max(r.done_at for r in reads) - t0)
    both = kind in ("bidir", "host_read", "app_read")
    if kind == "bidir":
        pay = min(v[0] for v in res.values())
        span = max(v[1] for v in res.values())
        out = {"bidir": _measure(env, pay, span, True, "bidir")}
    else:
        pay, span = res[kind]
        out = {kind: _measure(env, pay, span, both, kind)}
    env.finish()
    return out


@cocotb.test()
async def e2e_throughput_short(dut):
    """L1 screen of the core HAS throughput targets end to end: 300 host
    writes of 128 bytes to BAR0."""
    await _throughput(dut, "host_write", 300)


@cocotb.test()
async def e2e_throughput_host_write(dut):
    """Core HAS targets, host writes in: 2000 128-byte writes to BAR0."""
    await _throughput(dut, "host_write", 2000)


@cocotb.test()
async def e2e_throughput_app_write(dut):
    """Core HAS targets, application writes out: 2000 128-byte rq writes."""
    await _throughput(dut, "app_write", 2000)


@cocotb.test()
async def e2e_throughput_host_read(dut):
    """Core HAS targets, host reads: 1000 128-byte BAR0 reads, the
    application answering in 16 cycles."""
    await _throughput(dut, "host_read", 1000)


@cocotb.test()
async def e2e_throughput_app_read(dut):
    """Core HAS targets, application reads: 1000 128-byte rq reads."""
    await _throughput(dut, "app_read", 1000)


@cocotb.test()
async def e2e_throughput_bidir(dut):
    """Core HAS targets, both ways: 1000 host writes in and 1000
    application writes out at once."""
    await _throughput(dut, "bidir", 1000)


@cocotb.test()
async def e2e_d3hot(dut):
    """Outside D0 (core HAS decisions, Q11): in D3hot a BAR0 read gets an
    Unsupported Request completion, rq takes no request, an interrupt is
    pending and no error message goes out (status bits still set); back in
    D0 the request goes out, the pending interrupt follows, BAR0 is served
    again and the configuration is kept."""
    env = await up(dut)
    dev = await enumerate_ep(env)
    await enable_reporting(env)
    h, app = env.host.model, env.app
    base = dev.bars[0].base
    await env.run(h.setup_msi(dev, MSI_ADDR, 0x77), what="MSI set-up")
    env.irq_mode = "msi"
    await cfg_wr(env, CAP_PM + 4, 3, 0b0001)
    await env.cycles(4)
    assert env.d_state == 3, f"d_state_o {env.d_state} after writing D3hot"
    m0, n0 = len(h.messages), len(h.interrupts)
    r = await host_mem_read(env, base, 4)
    assert r.status == "UR", f"BAR0 read in D3hot: status {r.status}"
    w = app.write(HOST_MEM, b"\x11" * 8)
    q = app.irq(0)
    await env.until(lambda: q["status"] is not None, 500, "irq in D3hot")
    assert q["status"] == IRQ_PENDING, f"irq in D3hot: status {q['status']}"
    await env.cycles(600)
    assert w["taken"] is None, "rq took a request in D3hot"
    assert len(h.interrupts) == n0 and not msgs(env, m0), (
        f"in D3hot: {len(h.interrupts) - n0} interrupts, messages {msgs(env, m0)}")
    ds = await cfg_rd(env, DEVCTL)
    assert ds & DS_URD, "Unsupported Request Detected 0 after a UR in D3hot"
    await cfg_wr(env, CAP_PM + 4, 0, 0b0001)
    await env.until(lambda: w["taken"] is not None and len(h.interrupts) == n0 + 1, 3000,
                    "back in D0")
    data = bytes(range(16))
    h.mem_write(base + 0x10, data)
    r = await host_mem_read(env, base + 0x10, 16)
    assert r.ok and bytes(r.data) == data
    await env.cycles(300)
    assert not msgs(env, m0), "an error message for the event in D3hot went out on return to D0"
    assert (await cfg_rd(env, 0x10)) == dev.bars[0].base, "BAR0 lost across D3hot"
    await env.drain()
    env.finish()


@cocotb.test()
async def e2e_link_down(dut):
    """Link down (core HAS): the host's Hot Reset takes the link down with
    application and host traffic in flight; tl and cfg reset (the status
    outputs at reset values, the configuration space at reset values on
    the next enumeration); the function is enumerated again and works; a
    second link down from D3hot with interrupts resolving back to back."""
    env = await up(dut)
    dev = await enumerate_ep(env)
    h, app, rng = env.host.model, env.app, env.rng
    # Device Control Max_Payload_Size and Max_Read_Request_Size to non-reset
    # values (both RW as written, cfg CAPS), so the first link-down cycle
    # shows whether max_payload_o and max_read_req_o reach their reset
    # values at once; Memory Space and Bus Master Enable are 1 already.
    mps, mrrs = rng.randint(1, 5), rng.choice([v for v in range(6) if v != 2])
    ctl = await cfg_rd(env, DEVCTL)
    await cfg_wr(env, DEVCTL, (ctl & 0xFFFF & ~(7 << 5) & ~(7 << 12)) | mps << 5 | mrrs << 12,
                 0b0011)
    await env.cycles(4)
    got = (env.rd["max_payload_o"](), env.rd["max_read_req_o"]())
    assert got == (mps, mrrs), (
        f"max_payload_o, max_read_req_o {got} after writing Device Control {mps}, {mrrs}")
    assert env.mem_space_en and env.bus_master_en
    env.allow_link_errors = True
    base = dev.bars[0].base
    env.quiet_ok = True
    for k in range(8):
        h.mem_write(base + 128 * k, bytes(rng.getrandbits(8) for _ in range(128)))
        app.write(HOST_MEM + 128 * k, bytes(128))
    for k in range(4):
        app.read(HOST_MEM + 128 * k, 128)
    await env.cycles(rng.randint(50, 400))
    env.wr("host_hot_reset_req_i", 1)
    await env.until(lambda: not env.dl_up, 20_000, "ep dl_up_o falls")
    for name, want in (("bus_master_en_o", 0), ("mem_space_en_o", 0), ("cq_valid_o", 0),
                       ("rq_ready_o", 0), ("max_payload_o", 0), ("max_read_req_o", 2)):
        assert env.rd[name]() == want, f"{name} not at its reset value {want} with dl_up_o 0"
    await env.cycles(200)
    env.wr("host_hot_reset_req_i", 0)
    app.rq_q.clear()
    await env.link_up(limit=60_000)
    env.quiet_ok = False
    # A configuration write first, so the function captures its bus and
    # device number again (the host checks the completer ID), then the
    # reset values: Command, BAR0, MSI and MSI-X control, Device Control.
    await cfg_wr(env, 0x0C, 0, 0b0001)
    got = {reg: await cfg_rd(env, reg) for reg in (CFG_COMMAND, 0x10, 0x18, CAP_MSI, CAP_MSIX,
                                                    DEVCTL)}
    assert got[CFG_COMMAND] & 0xFFFF == 0 and got[0x10] == 0 and got[0x18] == 0, (
        f"after link down: Command {got[CFG_COMMAND] & 0xFFFF:#06x}, BAR0 {got[0x10]:#x}, BAR2 "
        f"{got[0x18]:#x}; the reset values are 0")
    assert not got[CAP_MSI] >> 16 & 1 and not got[CAP_MSIX] >> 31 & 1, "MSI or MSI-X still enabled"
    assert got[DEVCTL] & 0xFFFF == 0x2810, f"Device Control {got[DEVCTL] & 0xFFFF:#06x} after reset"
    assert env.link_downs == 1
    dev = await enumerate_ep(env)
    data = bytes(rng.getrandbits(8) for _ in range(64))
    h.mem_write(dev.bars[0].base, data)
    r = await host_mem_read(env, dev.bars[0].base, 64)
    assert r.ok and bytes(r.data) == data
    app.write(HOST_MEM + 0x2000, data)
    rr = app.read(HOST_MEM + 0x2000, 64)
    await app_wait(env, [rr])
    assert rr.status == RC_SC and app.read_bytes(rr) == data
    # Second round in D3hot, the application raising interrupts back to
    # back (each pending under MSI): every gated output, d_state_o and
    # irq_done_o included, at its reset value from the first cycle dl_up_o
    # is 0 (the environment checks every cycle).
    await env.run(h.setup_msi(dev, MSI_ADDR, 0x42), what="MSI set-up")
    env.irq_mode = "msi"
    await cfg_wr(env, CAP_PM + 4, 3, 0b0001)
    await env.cycles(4)
    assert env.d_state == 3
    storm = True

    async def irqs():
        while storm:
            if not app.irq_q and env.dl_up:
                app.irq(0)
            await env.cycles(1)

    cocotb.start_soon(irqs())
    env.quiet_ok = True
    await env.cycles(rng.randint(20, 200))
    env.wr("host_hot_reset_req_i", 1)
    await env.until(lambda: not env.dl_up, 20_000, "ep dl_up_o falls in D3hot")
    storm = False
    await env.cycles(200)
    env.wr("host_hot_reset_req_i", 0)
    app.irq_q.clear()
    await env.link_up(limit=60_000)
    env.quiet_ok = False
    assert env.link_downs == 2 and env.d_state == 0
    dev = await enumerate_ep(env)
    env.allow_link_errors = False
    await env.drain()
    env.finish()


@cocotb.test()
async def e2e_soak(dut):
    """Random mix: host BAR0 writes and reads, application writes and
    reads, MSI-X interrupts, with random stalls on cq and rc, after an
    enumeration; every read checked against its mirror."""
    env = await up(dut)
    dev = await enumerate_ep(env)
    h, app, rng = env.host.model, env.app, env.rng
    app.cq_ready_p = rng.choice((1.0, 0.8, 0.5))
    app.rc_ready_p = rng.choice((1.0, 0.8, 0.5))
    vecs = {v: (MSI_ADDR + 4 * v, 0x9000 + v) for v in range(env.msix_n)}
    await env.run(h.setup_msix(dev, vecs), what="MSI-X set-up")
    env.irq_mode = "msix"
    base = dev.bars[0].base
    size = 1 << env.bar0_addr_w
    mirror = bytearray(size)
    host_pend, app_pend, irqs = [], [], 0
    for k in range(400):
        op = rng.randrange(5)
        if op == 0:
            off = rng.randrange(size - 1)
            n = min(rng.randint(1, 256), ((off | 0xFFF) + 1) - off)
            # The host lets a posted write pass a read its credits hold back
            # (HOST, credits and ordering), so a write overlapping a read
            # still pending waits for it: the read's expected bytes stay put.
            if any(o < off + n and off < o + m for o, m, _, _ in host_pend):
                await env.until(lambda: all(p[3].done for p in host_pend), 50_000, "host reads")
            data = bytes(rng.getrandbits(8) for _ in range(n))
            h.mem_write(base + off, data)
            mirror[off:off + n] = data
        elif op == 1:
            off = rng.randrange(size - 1)
            n = min(rng.randint(0, 1024), ((off | 0xFFF) + 1) - off)
            host_pend.append((off, n, bytes(mirror[off:off + n]), h.mem_read(base + off, n)))
        elif op == 2:
            a = HOST_MEM + 0x10000 + 128 * k             # one block per op: no overlap
            data = bytes(rng.getrandbits(8) for _ in range(128))
            app.write(a, data)
            app_pend.append((a, data, app.read(a, 128)))
        elif op == 3:
            app.irq(rng.randrange(env.msix_n))
            irqs += 1
        else:
            await env.cycles(rng.randint(1, 200))
        if len(host_pend) > 8:
            await env.until(lambda: all(p[3].done for p in host_pend), 50_000, "host reads")
            for off, n, want, r in host_pend:
                assert r.ok and (n == 0 or bytes(r.data) == want), f"read {off:#x}+{n}"
            host_pend = []
    await env.drain(limit=400_000)
    for off, n, want, r in host_pend:
        assert r.ok and (n == 0 or bytes(r.data) == want), f"read {off:#x}+{n}"
    for a, data, r in app_pend:
        assert r.status == RC_SC and app.read_bytes(r) == data, f"rq read {a:#x}: {r.status}"
    if app_pend:
        waits = [r.done_at - r.issued_at for _, _, r in app_pend]
        env.log.info("soak: largest rq read completion wait %d cycles (mean %.0f), completion "
                     "timeout %d cycles", max(waits), sum(waits) / len(waits), env.cpl_timeout)
    assert len(h.interrupts) == irqs, f"{len(h.interrupts)} interrupts for {irqs} requests"
    assert bytes(app.bar0) == bytes(mirror)
    env.finish()


# Preserve each original regression seed and cocotb testcase fullname hash.
_FUNCTIONAL_SEEDS = {
    "e2e_enumeration": 914163979, "e2e_bar_traffic": 652310872,
    "e2e_bus_master": 1262466201, "e2e_msi": 934478017, "e2e_msix": 293482275,
}
_ERROR_SEEDS = {
    "e2e_unsupported": 116918796, "e2e_cpl_timeout": 2115626119,
    "e2e_errors": 904842732, "e2e_link_errors": 830112853,
}


def scenario_seed(name, seeds):
    base = seeds[name] + int(os.environ["COCOTB_RANDOM_SEED"]) - next(iter(seeds.values()))
    return base + int(hashlib.sha1(f"{__name__}.{name}".encode()).hexdigest(), 16)


def scenario_rng(env, name, seeds):
    value = scenario_seed(name, seeds)
    rng = random.Random(value)
    env.app.rng.seed(rng.getrandbits(32))  # The sole Env-construction draw.
    env.rng.setstate(rng.getstate())
    env.log.info("acceptance phase %s: original effective seed %d", name, value)
    env.app.cq_ready_p = env.app.rc_ready_p = 1.0
    env.app.cc_latency = (1, 24)
    env.app.cc_gap_p = env.app.rq_gap_p = 0.1
    env.host.model.cpl_split = "fewest"


@cocotb.test()
async def e2e_functional(dut):
    """One training/enumeration: BAR, bus-master and interrupt contracts."""
    name = "e2e_enumeration"
    value = scenario_seed(name, _FUNCTIONAL_SEEDS)
    env = await up(dut, test_seed=value)
    env.log.info("acceptance phase %s: original effective seed %d", name, value)
    await phase_e2e_enumeration(env)
    env.log.info("acceptance phase %s: all checks passed", name)
    for name, phase in (("e2e_bar_traffic", phase_e2e_bar_traffic),
                        ("e2e_bus_master", phase_e2e_bus_master),
                        ("e2e_msi", phase_e2e_msi), ("e2e_msix", phase_e2e_msix)):
        await env.drain()
        scenario_rng(env, name, _FUNCTIONAL_SEEDS)
        await phase(env)
        env.log.info("acceptance phase %s: all checks passed", name)


@cocotb.test()
async def e2e_error_paths(dut):
    """One training/enumeration, with isolated error observations per phase."""
    name = "e2e_unsupported"
    env = await up(dut, test_seed=scenario_seed(name, _ERROR_SEEDS))
    await enumerate_ep(env)
    for name, phase in (("e2e_unsupported", phase_e2e_unsupported),
                        ("e2e_cpl_timeout", phase_e2e_cpl_timeout),
                        ("e2e_errors", phase_e2e_errors), ("e2e_link_errors", phase_e2e_link_errors)):
        await env.drain()
        scenario_rng(env, name, _ERROR_SEEDS)
        env.phase_tl_baseline = list(env.tl_err_count)
        # Restore the original fresh-enumeration reporting state without
        # resetting the link or repeating enumeration. Status bits are W1C.
        ctl = await cfg_rd(env, DEVCTL)
        await cfg_wr(env, DEVCTL, (ctl & 0xffff & ~DC_ALL) | (0xf << 16))
        await cfg_wr(env, CFG_COMMAND, CMD_MSE | CMD_BME | STS_MDPE | STS_RTA
                     | STS_RMA | STS_SSE | STS_DPE)
        await phase(env)
        env.log.info("acceptance phase %s: all checks passed", name)
