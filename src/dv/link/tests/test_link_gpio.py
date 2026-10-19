"""Back-to-back e2e of two pcie_link stacks over the GPIO lower layer
(wiki/src/dv/link/DV_PLAN.md, GPIO lower layer).

End a (downstream) and end b (upstream) on the top pcie_link_gpio_tb, each
a pcie_dll over a pcie_gpio, joined by the modelled GPIO wires
(tb/link_gpio_env.py). Every test brings both dlls from reset to
DL_Active over the adapter, runs the dll traffic mix both ways, drains,
and passes the environment's checks: the scoreboards, the credits, the
error pulses, liveness and the reset values.
"""

import cocotb

from tb.link_e2e_env import BAD_DLLP, BAD_TLP, ROLLOVER, TIMEOUT, now_ps, ptn
from tb.link_gpio_env import GpioEnv


def seed() -> int:
    return cocotb.RANDOM_SEED


async def up(dut) -> GpioEnv:
    env = GpioEnv(dut, seed())
    env.start()
    await env.link_up(limit=20_000)
    return env


def pulses(env, bit, since=0):
    return {e.name: e.pulses(bit, since) for e in env.ends}


@cocotb.test()
async def gpio_smoke(dut):
    """Both dlls reach DL_Active over the adapter; 40 TLPs of the mix each
    way, lossless and in order; an error-free run: no Bad TLP, Bad DLLP,
    replay timeout or rollover, and no rcv_err."""
    env = await up(dut)
    env.send(0, 40)
    env.send(1, 40)
    await env.drain()
    await env.cycles(env.replay_timeout + 500)
    stats = env.finish()
    for bit, name in ((BAD_TLP, "Bad TLP"), (BAD_DLLP, "Bad DLLP"), (TIMEOUT, "replay timeout"),
                      (ROLLOVER, "rollover")):
        assert not any(pulses(env, bit).values()), f"{name} in an error-free run: {stats['err']}"
    assert not any(stats["rcv_err"].values()), f"rcv_err in an error-free run: {stats['rcv_err']}"


@cocotb.test()
async def gpio_load(dut):
    """Sustained load both ways, maximum-payload writes (the replay timeout's
    loaded case, link HAS replay timeout at LOWER 1): 150 TLPs of 32 DW each
    way, offered back to back. No replay timeout fires and no TLP is
    lost; the largest replay round trip seen is reported against the
    replay timeout the dlls run with."""
    env = await up(dut)
    for e in env.ends:
        e.valid_p = 1.0
    env.send(0, 150, kinds=("MWr",), dw=32)
    env.send(1, 150, kinds=("MWr",), dw=32)
    await env.drain(limit=600_000)
    await env.cycles(env.replay_timeout + 500)
    stats = env.finish()
    t = pulses(env, TIMEOUT)
    assert not any(t.values()), (f"replay timeouts under load with REPLAY_TIMEOUT "
                                 f"{env.replay_timeout}: {t}; round trips {stats['round_trip_max']}")
    for name, rt in stats["round_trip_max"].items():
        assert rt < env.replay_timeout, (f"end {name}: replay round trip {rt} cycles, not below "
                                         f"REPLAY_TIMEOUT {env.replay_timeout}")


@cocotb.test()
async def gpio_damage(dut):
    """One damaged slot each way while traffic flows (gpio LINK_UP.md,
    convergence: a single damaged slot): both adapters and both dlls stay
    up (DL_Active throughout), the receiver pulses rcv_err, and the dll
    recovers any damaged packet by Nak or replay, losslessly. A damaged
    marker on a TLP's EOP slot, its bytes intact: the adapter's error eop
    reaches the dll through link, a Bad TLP, and the replay delivers it
    once. Then two damaged slots within 8 on a to b: both links go down
    once and both dlls come back to DL_Active."""
    env = await up(dut)
    for d in ("ab", "ba"):
        s, r = (env.ends[0], env.ends[1]) if d == "ab" else (env.ends[1], env.ends[0])
        rcv = r.rcv_err
        env.send(0, 20)
        env.send(1, 20)
        await env.cycles(300)
        env.damage(d)
        await env.drain()
        assert r.rcv_err == rcv + 1, f"{d}: rcv_err {r.rcv_err - rcv} at the receiver, expected 1"
    # A damaged marker on a TLP's EOP slot: its bytes arrive intact, but
    # the adapter closes the packet with pkt_rx_err (gpio HAS, receive), so
    # the dll counts a Bad TLP and Naks; the TLP is replayed and delivered
    # once.
    await env.drain()
    await env.cycles(500)
    t0, mark = now_ps(), len(env.codes["ab"])
    tlp = ptn.make_tlp("MWr", 16, 0x3E00)
    beats = (2 + len(tlp) + 4) // 2           # sequence number, TLP, LCRC
    env.ends[0].send([tlp])
    await env.until(lambda: env.find_slot("ab", mark, 2) >= 0, 5000, "the TLP's sop on the pins")
    env.damage_at["ab"].add(env.find_slot("ab", mark, 2) + beats - 1)
    await env.drain()
    got = pulses(env, BAD_TLP, t0)["b"]
    assert got >= 1, f"a TLP closed with pkt_rx_err: Bad TLP pulses at b {got}, expected 1 or more"
    # Two damaged slots within 8: a link-down, expected.
    env.expect_down = True
    env.send(0, 10)
    env.send(1, 10)
    await env.cycles(300)
    si = env.damage("ab")
    env.damage_at["ab"].add(si + 5)
    await env.until(lambda: env.link_downs >= 2, 20_000, "both dlls down")
    await env.until(lambda: all(e.live for e in env.ends), 20_000, "both dlls back in DL_Active")
    env.expect_down = False
    for sb in env.sb.values():
        sb.lost = 0
    env.send(0, 20)
    env.send(1, 20)
    await env.drain()
    await env.cycles(2500)      # the UpdateFC refresh (2048 cycles) returns every credit
    env.finish()


@cocotb.test()
async def gpio_hot_reset(dut):
    """Hot Reset over the adapter (gpio LINK_UP.md, Hot Reset): a
    (downstream) requests it with TLPs in flight; both links go down, both
    dlls leave DL_Active and return to it after the dwell, and traffic
    then flows lossless."""
    env = await up(dut)
    env.expect_down = True
    env.send(0, 10)
    env.send(1, 10)
    await env.cycles(200)
    t0 = now_ps()
    env.ends[0].s["hot_reset_req_i"].value = 1
    await env.cycles(50)
    env.ends[0].s["hot_reset_req_i"].value = 0
    await env.until(lambda: env.link_downs >= 2, 20_000, "both dlls down")
    await env.until(lambda: all(e.live for e in env.ends), 20_000, "both dlls back in DL_Active")
    env.expect_down = False
    assert all(e.ltssm == 0x20 for e in env.ends)
    env.log.info("Hot Reset recovered %.0f a cycles after the request", (now_ps() - t0) / 8000)
    env.send(0, 20)
    env.send(1, 20)
    await env.drain()
    await env.cycles(2500)      # the UpdateFC refresh (2048 cycles) returns every credit
    env.finish()
