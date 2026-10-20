"""Named directed phases sharing one clock, model and configured DUT.

Keep each phase's original testcase RNG identity even after consolidation.
The default suite seed reproduces the original regression seeds; rotated
suite seeds deterministically perturb them instead of repeating pinned work.
"""

import hashlib
import os
import random
import zlib

def entry_seed(name: str) -> int:
    return zlib.crc32(f"rc_tl:{name}:0".encode()) & 0x7FFF_FFFF


def scenario_seed(suite: str, entry: str, fullname: str) -> tuple[int, int]:
    outer = int(os.environ["COCOTB_RANDOM_SEED"])
    original = entry_seed(entry) ^ outer ^ entry_seed(suite)
    # cocotb's pinned regression manager uses this exact testcase identity.
    actual = original + int(hashlib.sha1(fullname.encode()).hexdigest(), 16)
    return original, actual


async def phase(env, suite: str, recipe, routes=None, entry=None, **settings):
    """Run a named phase without replacing the clock, model or histories.

    Drain first, require quiescence and a clean error record, restore only
    changed configuration, and retain all phase-local assertions. Explicit
    resets inside recipes remain real DUT resets.
    """
    from tb.rc_tl_env import FC_INPUTS

    name = recipe.__name__.removeprefix("case_")
    fullname = f"{recipe.__module__}.{name}"
    outer, actual = scenario_seed(suite, entry or name, fullname)
    env.answer = True
    env.tx_ready_p, env.rx_gap_p, env.answer_delay = 1.0, 0.0, (2, 2)
    env.toggle_idle = False
    assert not env.hooks, f"{name}: prior phase left a live injection hook"
    env.drv.update(FC_INPUTS | {"cmd_valid_i": 0, "dl_up_i": 1, "msi_ready_i": 0})
    await env.drain(4)
    assert not env.ep.plans and env.pending is None and env.m.req is None, (
        f"{name}: prior phase left an endpoint plan or pending request")
    assert await env.err_rd() == 0, f"{name}: prior phase left an error"
    env.rng = random.Random(actual)
    for dw, value in (routes or {}).items():
        if env.fe.hdr.read(dw) != value:
            await env.hdr_wr(dw, value)
    for key, value in settings.items():
        setattr(env, key, value)
    env.dut._log.info("scenario BEGIN %s regression_seed=%d random_seed=%d cycle=%d",
                      fullname, outer, actual, env.cycle)
    try:
        await recipe(env)
    except Exception as exc:
        raise AssertionError(f"scenario {fullname}, regression_seed={outer}: {exc}") from exc
    env.dut._log.info("scenario PASS %s regression_seed=%d cycle=%d", fullname, outer, env.cycle)
