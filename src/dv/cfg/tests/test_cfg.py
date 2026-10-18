"""pcie_cfg register-access tests (wiki/src/dv/cfg/DV_PLAN.md).

Every test runs on tb/pcie_cfg_tb.sv through tb/cfg_env.py, whose
architecture-model shadow checks every cycle. On top, the register-map
tests judge each field against tb/cfg_regmap.py, the map written from
REGISTERS.md and CAPS.md, and the enumeration test plays a host. Seeded
tests take cocotb.RANDOM_SEED, which the regression passes.
"""

from __future__ import annotations

import cocotb

from tb.cfg_env import RESET_VALUES, Env
from tb.cfg_regmap import ENTRY_RW, LISTED, dw_map

# Offsets of the capabilities and their IDs (REGISTERS.md, Map).
CAPS = {0x01: 0x40, 0x05: 0x50, 0x11: 0x60, 0x10: 0x70}
PM_CSR = 0x44 // 4
DEVCTL = 0x78 // 4


def seed() -> int:
    return int(cocotb.RANDOM_SEED or 0)


@cocotb.test()
async def smoke(dut):
    """L0: reset values in reset and after it; the IDs read back and an RW
    field takes a write, through the tl-cfg handshake."""
    env = Env(dut, seed())
    await env.start()
    await env.run(2)
    for name in ("bus_num_o", "bar0_base_o", "d_state_o", "msg_valid_o", "max_read_req_o"):
        assert int(getattr(dut, name).value) == RESET_VALUES[name], name
    p = env.p
    assert await env.cfg_read(0) == p.vendor_id | p.device_id << 16
    await env.cfg_write(0x0C // 4, 0xA5, bus=3, dev=4)
    assert await env.cfg_read(0x0C // 4) == 0xA5
    assert (int(dut.bus_num_o.value), int(dut.dev_num_o.value)) == (3, 4)
    await env.reset()
    env.check_reset_values("after reset")
    assert await env.cfg_read(0x0C // 4) == 0


async def check_dw(env: Env, off: int, want: int, what: str, be: int = 0xF) -> None:
    got = await env.cfg_read(off // 4, be)
    assert got == want, (f"{what}: DW 0x{off:03x} expected 0x{want:08x} got 0x{got:08x}")


@cocotb.test()
async def regmap(dut):
    """Every field of REGISTERS.md and CAPS.md: reset value, RW bits taking
    writes byte by byte, RO and HwInit bits ignoring them, RW1C bits not set
    by a write; unlisted DWs and the extended space read 0 and ignore
    writes; every write captures the bus and device number; BAR sizing."""
    env = Env(dut, seed())
    await env.start()
    m = dw_map(env.p)
    rng = env.rng
    ext = sorted(rng.sample(range(0x101, 0x400), 24))
    offsets = list(range(0, 0x100, 4)) + [4 * dw for dw in [0x100 // 4] + ext]
    for off in offsets:
        d = m.get(off)
        # A read returns the whole DW whatever its byte enables.
        await check_dw(env, off, d.reset if d else 0, "reset", be=rng.getrandbits(4))
    for off in offsets:
        if off == 0x44:
            continue                                  # PowerState: power_states below
        d = m.get(off)
        reset = d.reset if d else 0
        rw = d.rw if d else 0
        fixed = reset & ~rw
        bus, dev = rng.getrandbits(8), rng.getrandbits(5)
        await env.cfg_write(off // 4, 0xFFFFFFFF, bus=bus, dev=dev)
        assert (int(env.dut.bus_num_o.value), int(env.dut.dev_num_o.value)) == (bus, dev), (
            f"bus/dev not captured by the write of DW 0x{off:03x}")
        await check_dw(env, off, fixed | rw, "all ones")
        await env.cfg_write(off // 4, 0)
        await check_dw(env, off, fixed, "all zeros")
        for lane in range(4):
            await env.cfg_write(off // 4, 0xFFFFFFFF, be=1 << lane)
            await check_dw(env, off, fixed | (rw & 0xFF << 8 * lane), f"byte {lane} only")
            await env.cfg_write(off // 4, 0, be=1 << lane)
        await env.cfg_write(off // 4, 0xFFFFFFFF, be=0)
        await check_dw(env, off, fixed, "no byte enabled")
        await env.cfg_write(off // 4, reset)
        await check_dw(env, off, reset, "restored")
    # Every listed DW was covered by the walk above.
    assert all(off in offsets for off in LISTED)


@cocotb.test()
async def power_states(dut):
    """PowerState: D3hot and D0 take effect at the write's edge on
    d_state_o; a write of D1 or D2 is discarded; No_Soft_Reset keeps every
    register across D3hot to D0. Transactions Pending shows
    tl_np_pending_i."""
    env = Env(dut, seed())
    await env.start()
    await env.cfg_write(0x10 // 4, 0xFFFFFFFF)
    base = await env.cfg_read(0x10 // 4)
    await env.cfg_write(0x04 // 4, 0x6, be=0x1)
    for value, want in ((3, 3), (1, 3), (2, 3), (0, 0), (1, 0), (2, 0), (3, 3), (0, 0)):
        await env.cfg_write(PM_CSR, value, be=0x1)
        assert int(dut.d_state_o.value) == want, f"PowerState write {value}: d_state_o"
        assert await env.cfg_read(PM_CSR) == want | 0x8
    assert await env.cfg_read(0x10 // 4) == base
    assert await env.cfg_read(0x04 // 4) & 0x6 == 0x6
    env.np_p = 1.0
    assert await env.cfg_read(DEVCTL) >> 21 & 1 == 1
    env.np_p = 0.0
    assert await env.cfg_read(DEVCTL) >> 21 & 1 == 0


@cocotb.test()
async def bar2_map(dut):
    """BAR2: each table entry's address (bits 1:0 read 0), upper address
    and data take writes byte by byte, the mask bit resets to 1, the
    pending bits read 0 and ignore writes, every other offset reads 0; the
    table entries survive a reset, the mask bits do not."""
    env = Env(dut, seed())
    await env.start()
    n, rng = env.p.msix_table_size, env.rng
    for v in range(n):
        assert await env.bar2_read(4 * v + 3) == 1, f"entry {v} mask reset"
    pattern = {}
    for v in range(n):
        for f in range(3):
            value = rng.getrandbits(32)
            await env.bar2_write(4 * v + f, value)
            pattern[4 * v + f] = value & ENTRY_RW[f]
    for dw, want in pattern.items():
        be = rng.getrandbits(4)
        assert await env.bar2_read(dw, be) == want, f"BAR2 DW 0x{dw:x} read with be 0x{be:x}"
    v = rng.randrange(n)
    for f in range(4):
        dw = 4 * v + f
        for lane in range(4):
            value = rng.getrandbits(32)
            before = await env.bar2_read(dw)
            await env.bar2_write(dw, value, be=1 << lane)
            lane_m = 0xFF << 8 * lane & ENTRY_RW[f]
            want = (before & ~lane_m) | (value & lane_m)
            assert await env.bar2_read(dw) == want, f"entry {v} field {f} byte {lane}"
            if f < 3:
                pattern[dw] = want
    await env.bar2_write(4 * v + 3, 0)
    assert await env.bar2_read(4 * v + 3) == 0
    others = [4 * n + rng.randrange(0x200 - 4 * n) for _ in range(4)] if n < 128 else []
    others += [0x202, 0x3FF, 0x200, 0x201] + [rng.randrange(0x202, 0x400) for _ in range(4)]
    for dw in others:
        await env.bar2_write(dw, 0xFFFFFFFF)
        assert await env.bar2_read(dw) == 0, f"BAR2 DW 0x{dw:x} must read 0"
    await env.reset()
    assert await env.bar2_read(4 * v + 3) == 1, "mask bit resets to 1"
    for dw, want in pattern.items():
        assert await env.bar2_read(dw) == want, f"BAR2 DW 0x{dw:x} after reset"


@cocotb.test()
async def enumeration(dut):
    """A host enumeration through the ports: IDs, header type, capability
    walk, extended header, BAR sizing and assignment, enables, then one MSI
    interrupt and one MSI-X interrupt that pends first (HAS, Targets)."""
    env = Env(dut, seed())
    await env.start()
    p, rng = env.p, env.rng
    bus, dev = rng.getrandbits(8), rng.getrandbits(5)

    async def wr(dw, data, be=0xF):
        await env.cfg_write(dw, data, be, bus, dev)

    assert await env.cfg_read(0) == p.vendor_id | p.device_id << 16
    assert await env.cfg_read(2) == p.revision_id | p.class_code << 8
    assert (await env.cfg_read(3)) >> 16 & 0xFF == 0
    assert (await env.cfg_read(1)) >> 20 & 1 == 1
    caps, ptr = {}, (await env.cfg_read(0x34 // 4)) & 0xFC
    while ptr and len(caps) < 8:
        hdr = await env.cfg_read(ptr // 4)
        caps[hdr & 0xFF] = ptr
        ptr = hdr >> 8 & 0xFC
    assert caps == CAPS, f"capability list {caps}"
    assert await env.cfg_read(0x100 // 4) == 0
    sizes = []
    for i in range(6):
        await wr(4 + i, 0xFFFFFFFF)
        mask = (await env.cfg_read(4 + i)) & 0xFFFFFFF0
        sizes.append((~mask + 1) & 0xFFFFFFFF if mask else 0)
    assert sizes == [1 << p.bar0_addr_w, 0, 4096, 0, 0, 0], f"BAR sizes {sizes}"
    base0 = rng.randrange(1, 1 << (32 - p.bar0_addr_w)) << p.bar0_addr_w
    base2 = rng.randrange(1, 1 << 20) << 12
    await wr(4, base0)
    await wr(6, base2)
    await wr(1, 0x106, be=0x3)
    assert int(dut.bar0_base_o.value) == base0 and int(dut.bar2_base_o.value) == base2
    assert (int(dut.bus_num_o.value), int(dut.dev_num_o.value)) == (bus, dev)
    assert int(dut.mem_space_en_o.value) == 1 and int(dut.bus_master_en_o.value) == 1
    # MSI, 64-bit address.
    hi, data = rng.getrandbits(32), rng.getrandbits(16)
    await wr(0x54 // 4, 0xFEE0_0000)
    await wr(0x58 // 4, hi)
    await wr(0x5C // 4, data)
    await wr(0x50 // 4, 1 << 16, be=0x4)
    assert await env.irq(0) == "sent"
    msg = env.taken[-1]
    assert (msg.kind, msg.addr, msg.data) == (0, 0xFEE0_0000 | hi << 32, data)
    await wr(0x50 // 4, 0, be=0x4)
    # MSI-X: function masked during set-up; the request pends, then goes.
    assert ((await env.cfg_read(0x60 // 4)) >> 16 & 0x7FF) + 1 == p.msix_table_size
    v = rng.randrange(p.msix_table_size)
    xdata = rng.getrandbits(32)
    await wr(0x60 // 4, 0xC0 << 24, be=0x8)
    await env.bar2_write(4 * v, 0xFEE0_0000 + 4 * v)
    await env.bar2_write(4 * v + 1, 0)
    await env.bar2_write(4 * v + 2, xdata)
    await env.bar2_write(4 * v + 3, 0)
    assert await env.irq(v) == "pending"
    assert (await env.bar2_read(0x200 + v // 32)) >> (v % 32) & 1 == 1
    n = len(env.taken)
    await wr(0x60 // 4, 0x80 << 24, be=0x8)
    await env.run_until(lambda: len(env.taken) > n, 50, "pending MSI-X write")
    msg = env.taken[-1]
    assert (msg.kind, msg.addr, msg.data) == (0, 0xFEE0_0000 + 4 * v, xdata)
    assert await env.bar2_read(0x200 + v // 32) == 0
    assert await env.irq(v) == "sent"
    msg = env.taken[-1]
    assert (msg.kind, msg.addr, msg.data) == (0, 0xFEE0_0000 + 4 * v, xdata)
    await env.drain()
