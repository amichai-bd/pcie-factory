"""pcie_cfg random test (wiki/src/dv/cfg/DV_PLAN.md): random register
accesses, table and mask writes, interrupt requests, error events, power
state changes, slot power events, link-down resets and a stalling message
taker, all judged
every cycle by the architecture-model shadow of tb/cfg_env.py; then a drain
with every interrupt allowed, after which nothing may stay pending.
Seeded by cocotb.RANDOM_SEED; random_ops runs 1500 operations (level 1),
random_long 12000 (level 2).
"""

from __future__ import annotations

import cocotb

from tb.cfg_env import Access, Env

CMD, PM_CSR, DEVCAP, DEVCTL = 0x04 // 4, 0x44 // 4, 0x74 // 4, 0x78 // 4
MSI_CTL, MSI_LO, MSI_HI, MSI_DAT, MSIX = 0x50 // 4, 0x54 // 4, 0x58 // 4, 0x5C // 4, 0x60 // 4
PBA = 0x200


def seed() -> int:
    return int(cocotb.RANDOM_SEED or 0)


@cocotb.test()
async def random_ops(dut):
    """1500 random operations under the shadow, then a drain."""
    await run_random(dut, 1500)


@cocotb.test()
async def random_long(dut):
    """12000 random operations under the shadow, then a drain."""
    await run_random(dut, 12000)


async def run_random(dut, ops: int) -> None:
    env = Env(dut, seed())
    await env.start()
    rng, n = env.rng, env.p.msix_table_size
    await env.init_table()
    await env.cfg_write(MSI_LO, 0xFEE0_0100)
    await env.cfg_write(MSI_DAT, 0x0055)
    await env.cfg_write(DEVCTL, 0xF, be=0x1)
    await env.cfg_write(CMD, 0x6, be=0x3)
    env.err_p = 0.02
    env.np_p = 0.5
    env.spl_p = 0.01

    def write(dw, data, be=0xF, bar2=False):
        env.accesses.append(Access(bar2, True, dw, be, data & 0xFFFFFFFF,
                                   rng.getrandbits(8), rng.getrandbits(5)))

    def read(dw, bar2=False):
        env.accesses.append(Access(bar2, False, dw, rng.getrandbits(4)))

    for i in range(ops):
        if i % 200 == 0:
            env.msg_ready_p = rng.choice((1.0, 0.5, 0.1, 0.0))
        op = rng.random()
        if op < 0.25:
            if env.irq_open is None and env.irq_offering is None and not env.irqs:
                env.request(rng.randrange(n))
        elif op < 0.35:
            write(4 * rng.randrange(n) + 3, int(rng.random() < 0.15), bar2=True)
        elif op < 0.42:
            write(MSIX, (int(rng.random() < 0.7) << 1 | int(rng.random() < 0.1)) << 30, be=0x8)
        elif op < 0.47:
            write(MSI_CTL, rng.getrandbits(1) << 16 | rng.getrandbits(3) << 20, be=0x4)
        elif op < 0.55:
            write(CMD, 0x2 | (0x4 if rng.random() < 0.8 else 0) | rng.choice((0, 0x40, 0x100)),
                  be=0x3)
        elif op < 0.61:
            write(PM_CSR, rng.choice((0, 0, 0, 3, 1, 2)), be=rng.choice((0x1, 0x1, 0x0)))
        elif op < 0.65:
            write(DEVCTL, rng.getrandbits(32), be=rng.getrandbits(4))
        elif op < 0.69:
            v = rng.randrange(n)
            write(4 * v + rng.randrange(3), rng.getrandbits(32), be=rng.getrandbits(4), bar2=True)
        elif op < 0.72:
            write(rng.choice((MSI_LO, MSI_HI, MSI_DAT)), rng.getrandbits(32))
        elif op < 0.74:
            write(rng.randrange(0x40), rng.getrandbits(32), be=rng.getrandbits(4))
        elif op < 0.84:
            read(rng.randrange(0x40) if rng.random() < 0.9 else DEVCAP)
        elif op < 0.92:
            dw = rng.choice((PBA, PBA + 1, 4 * rng.randrange(n) + rng.randrange(4),
                             rng.randrange(0x400)))
            read(dw, bar2=True)
        elif op < 0.9995:
            pass
        else:
            # Link down: the open request is taken first (tl and the
            # application are reset together with cfg in core).
            ready_p, env.msg_ready_p = env.msg_ready_p, 1.0
            await env.drain()
            await env.reset(rng.randrange(1, 4))
            env.msg_ready_p = ready_p
        await env.run(rng.randrange(1, 4))
        if len(env.accesses) > 8:
            await env.run_until(lambda: len(env.accesses) <= 2, 200, "access queue")
    # Drain: D0, bus mastering, messages taken at once, every vector
    # unmasked, MSI-X on; then MSI alone. Nothing may stay pending.
    env.err_p = 0.0
    env.msg_ready_p = 1.0
    await env.drain()
    await env.cfg_write(PM_CSR, 0, be=0x1)
    await env.cfg_write(CMD, 0x6, be=0x3)
    for v in range(n):
        await env.bar2_write(4 * v + 3, 0)
    await env.cfg_write(MSIX, 0x80 << 24, be=0x8)
    await env.run(4 * n + 20)
    for k in range((n + 31) // 32):
        assert await env.bar2_read(PBA + k) == 0, "MSI-X pending bit left after the drain"
    await env.cfg_write(MSIX, 0, be=0x8)
    await env.run(20)
    assert int(dut.msg_valid_o.value) == 0
    assert not env.model.msi_pending
    sent = sum(1 for r in env.resolved if r.status == "sent")
    pending = sum(1 for r in env.resolved if r.status == "pending")
    dut._log.info(f"random: {ops} ops, {len(env.resolved)} requests ({sent} sent, "
                  f"{pending} pending), {len(env.taken)} messages taken, {env.withdrawn} "
                  f"withdrawn, {env.cycle} cycles")
    assert sent and pending and env.withdrawn, "random test reached too little"
