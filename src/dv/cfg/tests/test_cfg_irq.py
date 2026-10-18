"""pcie_cfg interrupt and message tests (wiki/src/dv/cfg/DV_PLAN.md): the
resolution of the application's requests under MSI and MSI-X, masks and
pending bits, the withdrawal rule and the power states, each judged by its
HAS rule (wiki/src/arch/cfg/HAS.md#messages, #interrupts, #power-states)
written out below, with the architecture-model shadow of tb/cfg_env.py
checking every cycle as well.
"""

from __future__ import annotations

import cocotb

from tb.cfg_env import Access, Env

CMD, PM_CSR, DEVCTL = 0x04 // 4, 0x44 // 4, 0x78 // 4
MSI_CTL, MSI_LO, MSI_HI, MSI_DAT, MSIX = 0x50 // 4, 0x54 // 4, 0x58 // 4, 0x5C // 4, 0x60 // 4
MSE_BME = 0x6
PBA = 0x200
MSI_ADDR = 0xFEE0_0100


def seed() -> int:
    return int(cocotb.RANDOM_SEED or 0)


def entry_addr(v: int) -> int:
    return 0xFEE0_0000 + 16 * v       # Env.init_table


async def msi_on(env: Env, hi: int = 0, data: int = 0xABCD) -> None:
    await env.cfg_write(CMD, MSE_BME, be=0x3)
    await env.cfg_write(MSI_LO, MSI_ADDR)
    await env.cfg_write(MSI_HI, hi)
    await env.cfg_write(MSI_DAT, data)
    await env.cfg_write(MSI_CTL, 1 << 16, be=0x4)


async def msix_on(env: Env) -> None:
    await env.cfg_write(CMD, MSE_BME, be=0x3)
    await env.init_table()
    for v in range(env.p.msix_table_size):
        await env.bar2_write(4 * v + 3, 0)
    await env.cfg_write(MSIX, 0x80 << 24, be=0x8)


async def pba_bit(env: Env, v: int) -> int:
    return (await env.bar2_read(PBA + v // 32)) >> (v % 32) & 1


def last(env: Env, n: int):
    return [(t.kind, t.addr, t.data) for t in env.taken[n:]]


@cocotb.test()
async def irq_msi(dut):
    """No mode: dropped. MSI: one write with the MSI address (3-DW when the
    upper address is 0) and data in [15:0]; not allowed (Bus Master Enable
    0, or D3hot): pending, several requests give one write once allowed;
    clearing MSI Enable discards the pending flag."""
    env = Env(dut, seed())
    await env.start()
    await env.cfg_write(CMD, MSE_BME, be=0x3)
    assert await env.irq(0) == "dropped"
    assert env.taken == []
    for hi in (0, 0x1234_5678):
        await msi_on(env, hi=hi, data=0xBEEF)
        n = len(env.taken)
        assert await env.irq(0) == "sent"
        assert last(env, n) == [(0, MSI_ADDR | hi << 32, 0xBEEF)]
    # Bus mastering off: pending; merged; one write when it is set again.
    await env.cfg_write(CMD, 0x2, be=0x3)
    n = len(env.taken)
    assert await env.irq(0) == "pending"
    assert await env.irq(0) == "pending"
    await env.run(10)
    assert last(env, n) == []
    await env.cfg_write(CMD, MSE_BME, be=0x3)
    await env.run(10)
    assert last(env, n) == [(0, MSI_ADDR | 0x1234_5678 << 32, 0xBEEF)]
    # D3hot: pending; nothing goes out until D0.
    await env.cfg_write(PM_CSR, 3, be=0x1)
    n = len(env.taken)
    assert await env.irq(0) == "pending"
    await env.run(10)
    assert last(env, n) == []
    await env.cfg_write(PM_CSR, 0, be=0x1)
    await env.run(10)
    assert len(env.taken) == n + 1
    # Clearing MSI Enable discards the pending flag.
    await env.cfg_write(CMD, 0x2, be=0x3)
    assert await env.irq(0) == "pending"
    await env.cfg_write(MSI_CTL, 0, be=0x4)
    await env.cfg_write(MSI_CTL, 1 << 16, be=0x4)
    await env.cfg_write(CMD, MSE_BME, be=0x3)
    await env.run(10)
    assert len(env.taken) == n + 1, "MSI pending flag survived MSI Enable cleared"
    await env.drain()


@cocotb.test()
async def irq_msix(dut):
    """MSI-X: each vector's write carries its entry; a masked vector (its
    Mask bit or the Function Mask) or one not allowed pends in the PBA and
    goes on the unmask, lowest vector first, its bit clearing on the take;
    a request merges into a set bit; MSI-X wins over MSI; disabling MSI-X
    keeps the bits; an entry change after the offer reaches the next write
    only; a table entry looked up for a write that then may not go out is
    looked up again after a BAR2 write."""
    env = Env(dut, seed())
    await env.start()
    await msix_on(env)
    nvec, rng = env.p.msix_table_size, env.rng
    for v in range(nvec):
        n = len(env.taken)
        assert await env.irq(v) == "sent"
        assert last(env, n) == [(0, entry_addr(v), 0x100 + v)]
    v = rng.randrange(nvec)
    await env.bar2_write(4 * v + 3, 1)
    n = len(env.taken)
    assert await env.irq(v) == "pending"
    assert await env.irq(v) == "pending"
    assert await pba_bit(env, v) == 1
    await env.run(10)
    assert last(env, n) == []
    await env.bar2_write(4 * v + 3, 0)
    await env.run(10)
    assert last(env, n) == [(0, entry_addr(v), 0x100 + v)]
    assert await pba_bit(env, v) == 0
    # Function Mask: every request pends; cleared, they go lowest first.
    await env.cfg_write(MSIX, 0xC0 << 24, be=0x8)
    vs = sorted(rng.sample(range(nvec), min(nvec, 4)), reverse=True)
    for u in vs:
        assert await env.irq(u) == "pending"
    n = len(env.taken)
    await env.cfg_write(MSIX, 0x80 << 24, be=0x8)
    await env.run(20)
    assert [t.addr for t in env.taken[n:]] == [entry_addr(u) for u in sorted(vs)]
    # MSI-X wins over MSI.
    await env.cfg_write(MSI_LO, MSI_ADDR)
    await env.cfg_write(MSI_CTL, 1 << 16, be=0x4)
    n = len(env.taken)
    assert await env.irq(v) == "sent"
    assert last(env, n) == [(0, entry_addr(v), 0x100 + v)]
    await env.cfg_write(MSI_CTL, 0, be=0x4)
    # Disabling MSI-X keeps the pending bits; nothing is sent until enabled.
    await env.bar2_write(4 * v + 3, 1)
    assert await env.irq(v) == "pending"
    await env.cfg_write(MSIX, 0, be=0x8)
    await env.bar2_write(4 * v + 3, 0)
    n = len(env.taken)
    await env.run(10)
    assert last(env, n) == [] and await pba_bit(env, v) == 1
    await env.cfg_write(MSIX, 0x80 << 24, be=0x8)
    await env.run(10)
    assert last(env, n) == [(0, entry_addr(v), 0x100 + v)]
    # The entry is read when the write is offered.
    env.msg_ready_p = 0.0
    irq = env.request(v)
    await env.run_until(lambda: int(dut.msg_valid_o.value) == 1, 20, "offer")
    await env.bar2_write(4 * v + 2, 0x5555)
    env.msg_ready_p = 1.0
    await env.run_until(lambda: irq.status is not None, 20, "sent")
    assert env.taken[-1].data == 0x100 + v
    assert await env.irq(v) == "sent"
    assert env.taken[-1].data == 0x5555
    # A stale lookup (MAS, Messages and interrupts): vectors u < w pend
    # under the Function Mask; u is offered and taken at edge E, w's entry
    # is looked up at E+1 while a write at E+1 sets the Function Mask, so
    # nothing loads. A BAR2 write then changes w's data: once unmasked, w's
    # write must carry the new data, not the looked-up word.
    if nvec >= 2:
        u, w = sorted(rng.sample(range(nvec), 2))
        env.msg_ready_p = 0.0
        await env.cfg_write(MSIX, 0xC0 << 24, be=0x8)
        assert await env.irq(u) == "pending"
        assert await env.irq(w) == "pending"
        await env.cfg_write(MSIX, 0x80 << 24, be=0x8)
        await env.run_until(lambda: int(dut.msg_valid_o.value) == 1, 20, "offer of u")
        n = len(env.taken)
        env.msg_ready_p = 1.0
        await env.run(1)                      # msg_ready_i 1: u taken at the next edge
        env.msg_ready_p = 0.0
        env.accesses.append(Access(False, True, MSIX, 0x8, 0xC0 << 24))
        await env.run(6)
        assert last(env, n) == [(0, entry_addr(u), 0x100 + u)]
        assert int(dut.msg_valid_o.value) == 0, "w offered under the Function Mask"
        await env.bar2_write(4 * w + 2, 0xD00D)
        env.msg_ready_p = 1.0
        await env.cfg_write(MSIX, 0x80 << 24, be=0x8)
        await env.run(10)
        assert last(env, n) == [(0, entry_addr(u), 0x100 + u), (0, entry_addr(w), 0xD00D)], (
            last(env, n))
    await env.drain()


@cocotb.test()
async def irq_withdraw(dut):
    """Withdrawal (TL_CFG): an offered write is withdrawn when Bus Master
    Enable, its mode's enable or D0 is lost, and becomes pending (MSI-X:
    its bit; MSI: the flag, else the vector's bit under MSI-X, else
    dropped); a mask after the offer does not withdraw it; a request
    waiting behind an error message that becomes masked turns pending
    without an offer."""
    env = Env(dut, seed())
    await env.start()
    await msix_on(env)
    v = env.rng.randrange(env.p.msix_table_size)

    async def offered(vector: int):
        env.msg_ready_p = 0.0
        irq = env.request(vector)
        await env.run_until(lambda: int(dut.msg_valid_o.value) == 1, 20, "offer")
        return irq

    # Bus Master Enable cleared: MSI-X write withdrawn, pending.
    irq = await offered(v)
    await env.cfg_write(CMD, 0x2, be=0x3)
    assert irq.status == "pending" and int(dut.msg_valid_o.value) == 0
    assert await pba_bit(env, v) == 1
    env.msg_ready_p = 1.0
    n = len(env.taken)
    await env.cfg_write(CMD, MSE_BME, be=0x3)
    await env.run(10)
    assert last(env, n) == [(0, entry_addr(v), 0x100 + v)]
    # D3hot: withdrawn, pending; D0: sent.
    irq = await offered(v)
    await env.cfg_write(PM_CSR, 3, be=0x1)
    assert irq.status == "pending"
    env.msg_ready_p = 1.0
    n = len(env.taken)
    await env.cfg_write(PM_CSR, 0, be=0x1)
    await env.run(10)
    assert len(env.taken) == n + 1
    # A mask after the offer does not withdraw it.
    irq = await offered(v)
    await env.bar2_write(4 * v + 3, 1)
    await env.cfg_write(MSIX, 0xC0 << 24, be=0x8)
    assert int(dut.msg_valid_o.value) == 1
    env.msg_ready_p = 1.0
    await env.run_until(lambda: irq.status is not None, 20, "sent")
    assert irq.status == "sent"
    await env.cfg_write(MSIX, 0x80 << 24, be=0x8)
    await env.bar2_write(4 * v + 3, 0)
    # Waiting behind an error message, then masked: pending, never offered.
    await env.cfg_write(DEVCTL, 1, be=0x1)
    env.msg_ready_p = 0.0
    env.events.append((0, 0, 1))
    await env.run(4)
    irq = env.request(v)
    await env.run(4)
    assert irq.status is None and int(dut.msg_kind_o.value) == 1
    await env.bar2_write(4 * v + 3, 1)
    assert irq.status == "pending"
    env.msg_ready_p = 1.0
    await env.run(10)
    assert [t.kind for t in env.taken[-1:]] == [1]
    await env.bar2_write(4 * v + 3, 0)
    await env.run(10)
    assert env.taken[-1].addr == entry_addr(v)
    # MSI: withdrawn by MSI Enable cleared: to the vector's bit under MSI-X,
    # dropped with neither mode.
    await env.cfg_write(MSIX, 0, be=0x8)
    await msi_on(env)
    irq = await offered(v)
    await env.cfg_write(MSIX, 0x80 << 24, be=0x8)
    assert int(dut.msg_valid_o.value) == 1, "MSI-X Enable withdrew an MSI write"
    await env.cfg_write(MSI_CTL, 0, be=0x4)
    assert irq.status == "pending" and await pba_bit(env, v) == 1
    env.msg_ready_p = 1.0
    await env.run(10)
    assert env.taken[-1].addr == entry_addr(v)
    await env.cfg_write(MSIX, 0, be=0x8)
    await env.cfg_write(MSI_CTL, 1 << 16, be=0x4)
    irq = await offered(v)
    await env.cfg_write(MSI_CTL, 0, be=0x4)
    assert irq.status == "dropped"
    env.msg_ready_p = 1.0
    # MSI withdrawn by Bus Master Enable: the MSI pending flag.
    await env.cfg_write(MSI_CTL, 1 << 16, be=0x4)
    irq = await offered(v)
    await env.cfg_write(CMD, 0x2, be=0x3)
    assert irq.status == "pending"
    env.msg_ready_p = 1.0
    n = len(env.taken)
    await env.cfg_write(CMD, MSE_BME, be=0x3)
    await env.run(10)
    assert last(env, n) == [(0, MSI_ADDR, 0xABCD)]
    await env.drain()
