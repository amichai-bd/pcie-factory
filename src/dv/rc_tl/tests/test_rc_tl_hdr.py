"""pcie_rc_tl Type 1 header through HDR_RD and HDR_WR
(wiki/src/rtl/rc_tl/MAS.md#type-1-header, wiki/src/arch/rc/HAS.md#type-1-header,
TL_SEQ.md#header-access).

Each test states its expected DWs from the HAS header table itself, besides
the environment's per-cycle check against the reference model.
"""

import cocotb

from tb.rc_tl_env import Env

# The HAS table: RW bits per DW index; every other bit is a constant.
RW = {1: 0x0000_0006, 3: 0x0000_00FF, 6: 0x00FF_FFFF, 8: 0xFFF0_FFF0}


def constants(env: Env) -> dict[int, int]:
    """The read-only value of each DW (reset value of its RW bits is 0)."""
    p = env.p
    return {0: (p.device_id << 16) | p.vendor_id, 2: (0x060400 << 8) | p.revision_id,
            3: 0x01 << 16}


async def check_reset_values(env: Env, where: str) -> None:
    const = constants(env)
    for idx in range(1024):
        got = await env.hdr_rd(idx)
        want = const.get(idx, 0)
        assert got == want, f"{where}: DW {idx} reset value 0x{want:08x}, got 0x{got:08x}"


@cocotb.test()
async def hdr_reset(dut):
    """Every header DW, indices 16 to 1023 included, reads its reset value,
    out of reset and again after a reset that follows writes to every DW."""
    env = Env(dut, int(cocotb.RANDOM_SEED or 0))
    await env.start()
    await case_hdr_reset(env)


async def case_hdr_reset(env: Env):
    """Every header DW, indices 16 to 1023 included, reads its reset value,
    out of reset and again after a reset that follows writes to every DW."""
    dut = env.dut
    await check_reset_values(env, "after reset")
    for idx in range(16):
        await env.hdr_wr(idx, 0xFFFF_FFFF)
    assert await env.hdr_rd(8) == 0xFFF0_FFF0
    await env.reset(4)
    await check_reset_values(env, "after a second reset")


@cocotb.test()
async def hdr_rw(dut):
    """Read and write of every field: RW bits take the data in the bytes
    first_be enables, RO bits and indices 16 to 1023 ignore it."""
    env = Env(dut, int(cocotb.RANDOM_SEED or 0))
    await env.start()
    await case_hdr_rw(env)


async def case_hdr_rw(env: Env):
    """Read and write of every field: RW bits take the data in the bytes
    first_be enables, RO bits and indices 16 to 1023 ignore it."""
    dut = env.dut
    const = constants(env)
    shadow = {idx: const.get(idx, 0) for idx in range(16)}

    async def write_check(idx: int, data: int, be: int) -> None:
        await env.hdr_wr(idx, data, be)
        if idx in RW:
            mask = RW[idx] & sum(0xFF << (8 * i) for i in range(4) if (be >> i) & 1)
            shadow[idx] = (shadow[idx] & ~mask) | (data & mask)
        for j in range(16):
            got = await env.hdr_rd(j)
            assert got == shadow[j], (f"after HDR_WR {idx} be {be:04b} data 0x{data:08x}: "
                                      f"DW {j} expected 0x{shadow[j]:08x} got 0x{got:08x}")

    # Every DW: all ones, then all zeros, with every byte enabled.
    for idx in range(16):
        await write_check(idx, 0xFFFF_FFFF, 0xF)
    for idx in range(16):
        await write_check(idx, 0x0000_0000, 0xF)
    # Each RW field alone, with two complementary patterns, under every
    # byte-enable combination.
    for idx in RW:
        for be in range(16):
            await write_check(idx, 0xA5A5_A5A5, be)
            await write_check(idx, 0x5A5A_5A5A, be)
    # Every RW bit alone, so a field mapped to the wrong bits shows.
    for idx, mask in RW.items():
        for bit in range(32):
            if (mask >> bit) & 1:
                await write_check(idx, 1 << bit, 0xF)
        await write_check(idx, 0, 0xF)
    # Indices 16 to 1023 ignore writes and read 0.
    for idx in (16, 17, 31, 64, 513, 1023):
        await env.hdr_wr(idx, 0xFFFF_FFFF)
        assert await env.hdr_rd(idx) == 0
    for j in range(16):
        assert await env.hdr_rd(j) == shadow[j], f"DW {j} changed by a write above 15"
