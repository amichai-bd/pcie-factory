"""pcie_dll credit accounting tests (wiki/src/dv/dll/DV_PLAN.md, #214).

The transmit credit gate, CREDITS_CONSUMED and the credits available, the
receive-side CREDITS_ALLOCATED, CREDITS_RECEIVED and overflow check, and
UpdateFC on returned credits. Until the TLP paths attach (#212, #213) the
testbench's slot drivers stand in for them (DV plan, slot drivers); the
environment checks the gate and the overflow check every cycle against the
flow-control rules and the architecture model's counters, and every
UpdateFC's value, cause and round-robin order. Seeded tests take
cocotb.RANDOM_SEED, which the regression passes.
"""

from __future__ import annotations

import random

import cocotb

from tb.dll_env import (ACTIVE, CLASSES, INACTIVE, INIT1, INIT2, MODS, STATE_NAMES, Env,
                        arch_partner_traffic, ptn, raw_dllp)

# A credit returned in DL_Active is in an UpdateFC that starts within this
# many cycles when the phy never stalls: at most the packet on the stream
# and the two other classes' UpdateFCs ahead of it in round robin, each 3
# beats and a framing cycle (FLOW_CONTROL.md, UpdateFC scheduling).
UPD_BOUND = 16

# Partner advertisements for the starvation test, by seed: every class
# finite (two sizes), and the default (completions infinite).
STARVE_CREDITS = (
    {"P": (16, 64), "NP": (4, 2), "Cpl": (16, 64)},
    {"P": (1, 8), "NP": (1, 2), "Cpl": (1, 8)},
    {"P": (8, 32), "NP": (8, 8), "Cpl": (0, 0)},
    {"P": (3, 24), "NP": (2, 4), "Cpl": (2, 16)},
)
INFINITE = {"P": (0, 0), "NP": (0, 0), "Cpl": (0, 0)}

KINDS = ("MWr", "MRd", "CfgWr", "Msg", "MsgD", "Cpl", "CplD")


def fc_dllp(typ: int, hdr: int, data: int) -> bytes:
    return raw_dllp(ptn.dllp_body(typ, hdr, data))


def random_tlp(rng: random.Random, tag: int, big: float = 0.2) -> bytes:
    kind = rng.choice(KINDS)
    dw = 32 if rng.random() < big else rng.randint(1, 32)
    return ptn.make_tlp(kind, dw, tag & 0xFFFF)


async def wait_state(env: Env, state: int, limit: int) -> int:
    return await env.run_until(lambda: env.state == state, limit, STATE_NAMES[state])


async def bring_up(env: Env, far: dict[str, tuple[int, int]], *, in_init2=None) -> None:
    """Link up against a hand-driven far end advertising ``far``; await
    ``in_init2(env)`` in FC_INIT2 before ending it."""
    env.dut_link = True
    await wait_state(env, INIT1, 10)
    for cls in CLASSES:
        env.inject(fc_dllp(ptn.INITFC1[cls], *far[cls]))
    await wait_state(env, INIT2, 300)
    if in_init2 is not None:
        await in_init2(env)
    env.inject(fc_dllp(ptn.INITFC2["P"], *far["P"]))
    await wait_state(env, ACTIVE, 300)


def stalled(env: Env, cycles: int = 30) -> bool:
    return env.refusing and env.cycle - env.refuse_since > cycles


# ---------------------------------------------------------------------------


@cocotb.test()
async def fc_tx_gate(dut):
    """L0: the transmit credit gate against a hand-driven far end. A TLP
    source offers random TLPs of every class; the gate is checked every
    cycle (idle random headers included) against the rule and the
    architecture model, and the credits available every cycle. Each round
    consumes until the gate refuses, then an UpdateFC raises the limits:
    usually by a compliant amount, sometimes beyond half the counter range
    (the gate must then refuse). A bulk phase wraps the header and data
    CREDITS_CONSUMED counters. Infinite fields never refuse. A link-down
    mid-TLP clears the counters."""
    seed = cocotb.RANDOM_SEED
    rng = random.Random(seed)
    env = Env(dut, seed, slot_tx=True, valid_p=0.7)
    await env.start()
    far = {"P": (6, 20), "NP": (3, 0), "Cpl": (0, 0)}   # NP data and Cpl infinite
    tag = 0
    for epoch in range(2):
        await bring_up(env, far)
        sh = env.shadow
        for rnd in range(30):
            for _ in range(12):
                env.tlp_q.append(random_tlp(rng, tag))
                tag += 1
            await env.run_until(lambda: not env.tlp_q or stalled(env), 6000,
                                "the source drained or refused")
            if not env.tlp_q:
                continue
            for c, cls in enumerate(CLASSES):
                hdr = (sh.cc[c][0] + rng.randint(1, 24)) % MODS[0]
                data = (sh.cc[c][1] + rng.randint(8, 200)) % MODS[1]
                if rng.random() < 0.15:
                    # Beyond half the range: no compliant partner sends it,
                    # and the gate's modular rule refuses everything.
                    hdr = (sh.cc[c][0] + rng.randint(130, 255)) % MODS[0]
                env.inject(fc_dllp(ptn.UPDATEFC[cls], hdr, data))
            await env.run(40)
        # Room for what is left, then drained.
        for c, cls in enumerate(CLASSES):
            env.inject(fc_dllp(ptn.UPDATEFC[cls], (sh.cc[c][0] + 100) % MODS[0],
                               (sh.cc[c][1] + 1000) % MODS[1]))
        await env.run_until(lambda: not env.tlp_q, 6000, "the source drained")
        if epoch == 0:
            # The gate's edges: a 128-byte write leaves exactly half the
            # range (taken), or one credit more than that (refused), in the
            # header field and then in the data field.
            for extra_h, extra_d, taken in ((128, 2048, True), (129, 100, False),
                                            (5, 2049, False), (5, 100, True)):
                env.inject(fc_dllp(ptn.UPDATEFC["P"], (sh.cc[0][0] + 1 + extra_h) % MODS[0],
                                   (sh.cc[0][1] + 8 + extra_d) % MODS[1]))
                await env.run(20)
                count = len(env.accepted)
                env.tlp_q.append(ptn.make_tlp("MWr", 32, tag & 0xFFFF))
                tag += 1
                await env.run(60)
                assert (len(env.accepted) > count) == taken, (
                    f"limit {extra_h}, {extra_d} beyond the write: taken "
                    f"{len(env.accepted) > count}, expected {taken}")
                if not taken:
                    env.inject(fc_dllp(ptn.UPDATEFC["P"], (sh.cc[0][0] + 10) % MODS[0],
                                       (sh.cc[0][1] + 100) % MODS[1]))
                    await env.run_until(lambda: not env.tlp_q, 300, "the write taken")
            # Bulk: enough 128-byte writes to wrap both P counters.
            for _ in range(4):
                env.inject(fc_dllp(ptn.UPDATEFC["P"], (sh.cc[0][0] + 128) % MODS[0],
                                   (sh.cc[0][1] + 2048) % MODS[1]))
                await env.run(20)
                for _ in range(120):
                    env.tlp_q.append(ptn.make_tlp("MWr", 32, tag & 0xFFFF))
                    tag += 1
                await env.run_until(lambda: not env.tlp_q, 20000, "bulk writes taken")
            # Link down in the middle of a TLP: everything clears.
            env.tlp_q.append(ptn.make_tlp("CplD", 32, 1))
            env.inject(fc_dllp(ptn.UPDATEFC["P"], (sh.cc[0][0] + 10) % MODS[0],
                               (sh.cc[0][1] + 10) % MODS[1]))
            await env.run_until(lambda: env.tlp_cur is not None and env.tlp_idx > 3, 200,
                                "a TLP being taken")
            env.dut_link = False
            await wait_state(env, INACTIVE, 3)
            env.tlp_q.clear()
            await env.run(30)
            far = {"P": (2, 8), "NP": (1, 2), "Cpl": (4, 0)}
    assert env.refused > 100, f"the gate refused a sop in {env.refused} cycles only"
    dut._log.info("seed %d: %d gate checks, %d TLPs accepted, %d cycles refused",
                  seed, env.gate_checks, len(env.accepted), env.refused)
    env.check_clean()


async def rx_traffic(env: Env, rng: random.Random, count: int, burst: float) -> None:
    """Push ``count`` receive decisions: well-formed TLPs of every class,
    some malformed (a byte count other than the header's, or under 4
    bytes); with probability ``burst`` several back to back."""
    tag = 0
    for _ in range(count):
        tlp = random_tlp(rng, tag, big=0.1)
        tag += 1
        r = rng.random()
        if r < 0.08:
            env.rx_dec_q.append((tlp[:-4] if len(tlp) > 12 else tlp + bytes(4), True, False))
        elif r < 0.11:
            env.rx_dec_q.append((tlp[:2], True, True))
        else:
            env.rx_dec_q.append((tlp, False, False))
        if rng.random() > burst:
            await env.run(rng.randint(1, 60))


@cocotb.test()
async def fc_rx_accounting(dut):
    """L0/L1, per parameter set: receive accounting through the slot
    drivers. Decisions in FC_INIT2 and DL_Active, stored, overflowing and
    malformed TLPs of every class; the overflow check (finite credits, or
    the reserve of an infinite field) against the rule and the
    architecture model every cycle; each overflow pulses err_o bit 5 once;
    malformed TLPs return their header's credits at once, nothing under 4
    bytes; delivered TLPs return their credits at their eop; every UpdateFC
    carries CREDITS_ALLOCATED, is due, goes in round robin, and a return is
    sent within the bound; link down clears the counters. With every class
    finite the upper layer stalls."""
    seed = cocotb.RANDOM_SEED
    rng = random.Random(seed)
    env = Env(dut, seed, slot_rx=True)
    await env.start()
    finite = all(v for cls in CLASSES for v in env.adv[cls])
    if finite:
        env.up_ready_p = 0.5
    env.upd_bound = UPD_BOUND
    far = {"P": (8, 32), "NP": (8, 8), "Cpl": (0, 0)}

    async def init2(e: Env) -> None:
        # Stored, returned and malformed before DL_Active: the entry
        # UpdateFC carries what was returned.
        for kind in ("MWr", "MRd", "CplD"):
            e.rx_dec_q.append((ptn.make_tlp(kind, 2, 7), False, False))
        e.rx_dec_q.append((ptn.make_tlp("MWr", 4, 8)[:-4], True, False))
        await e.run(40)

    for epoch in range(2):
        await bring_up(env, far, in_init2=init2)
        await rx_traffic(env, rng, 150, burst=0.3)
        # A burst per class: more than its credits or reserve hold.
        for kind in ("MWr", "MRd", "CplD", "Msg"):
            for i in range(12):
                env.rx_dec_q.append((ptn.make_tlp(kind, 8, i), False, False))
            await env.run(rng.randint(1, 30))
        if finite:
            env.up_stall = 600
        await rx_traffic(env, rng, 100, burst=0.5)
        await env.run_until(lambda: not env.rx_dec_q and not env.rx_store
                            and env.rx_cur is None, 20000, "receive decisions drained")
        await env.run(2 * 2048 + 100)     # refreshes after the returns
        if epoch == 0:
            # Link down while TLPs are stored and one is being delivered.
            if finite:
                env.up_stall = 50
            for i in range(4):
                env.rx_dec_q.append((ptn.make_tlp("MWr", 1, i), False, False))
            await env.run(20)
            env.dut_link = False
            await wait_state(env, INACTIVE, 3)
            await env.run(30)
    decisions = [d for _, d, _ in env.decided]
    overflows = decisions.count("overflow")
    assert overflows == env.ovf_seen, f"{overflows} overflows, {env.ovf_seen} pulses"
    for want in ("stored", "overflow", "malformed", "malformed_no_hdr"):
        assert want in decisions, f"no {want} decision"
    dut._log.info("seed %d, credits %s: %d decisions (%d stored, %d overflow), %d delivered; "
                  "UpdateFC latency after a return at most %d cycles", seed, env.adv,
                  len(decisions), decisions.count("stored"), overflows, len(env.delivered),
                  env.upd_lat_max)
    env.check_clean()


async def starve_run(dut, credits: dict[str, tuple[int, int]], schedule) -> Env:
    """The TLP source against the independent partner: accepted TLPs are
    forwarded, framed, to the partner, whose receive-side check judges every
    one against its advertised credits. ``schedule`` is a list of
    (cycles, upper-layer take probability) for the partner's upper layer."""
    seed = cocotb.RANDOM_SEED
    rng = random.Random(seed)
    partner = ptn.Partner("partner", credits=credits, seed=seed)
    env = Env(dut, seed, partner=partner, slot_tx=True, fwd=True, valid_p=0.9)
    await env.start()
    dut._log.info("seed %d, DUT credits %s, partner credits %s", seed, env.adv, credits)
    env.dut_link = env.partner_link = True
    await env.run_until(lambda: env.state == ACTIVE and partner.state == ptn.ACTIVE, 400,
                        "both ends in DL_Active")
    tag = 0
    for _ in range(3000):
        env.tlp_q.append(random_tlp(rng, tag))
        tag += 1
    refused = []
    for cycles, take in schedule:
        before = env.refused
        for _ in range(cycles):
            partner.upper_ready = rng.random() < take
            await env.step()
        refused.append(env.refused - before)
        dut._log.info("%d cycles, partner takes %.0f%%: %d TLPs accepted, gate refused %d "
                      "cycles", cycles, 100 * take, len(env.accepted), refused[-1])
    while len(env.tlp_q) > (env.tlp_cur is not None):
        env.tlp_q.pop()
    partner.upper_ready = True
    await env.run_until(lambda: env.tlp_cur is None and not env.fwd_q and not env.fwd_cur,
                        2000, "the last TLP forwarded")
    await env.run(300)
    got = partner.received
    assert got == env.accepted, (f"partner received {len(got)} TLPs, the DUT accepted "
                                 f"{len(env.accepted)}; first difference at "
                                 f"{next((i for i, (a, b) in enumerate(zip(got, env.accepted)) if a != b), min(len(got), len(env.accepted)))}")
    stats = partner.stats
    assert not stats.get("bad_tlp") and not stats.get("rx_duplicate"), f"partner {stats}"
    dut._log.info("partner stats %s", stats)
    env.check_clean()
    env.refused_by_phase = refused
    # The architecture model against a fresh partner: the same TLPs, the
    # same credits and upper-layer schedule; the partner judges the model.
    fresh = ptn.Partner("fresh", credits=credits, seed=seed)
    model = arch_partner_traffic(env.adv, fresh, list(env.accepted), schedule,
                                 random.Random(seed))
    assert fresh.received == env.accepted, (
        f"partner received {len(fresh.received)} of {len(env.accepted)} TLPs from the arch model")
    assert not fresh.violations, "\n".join(fresh.violations[:10])
    dut._log.info("arch model against a fresh partner: %d TLPs delivered, credit-blocked "
                  "offers %d, no partner violation", len(fresh.received),
                  model.counts["credit_blocked"])
    return env


@cocotb.test()
async def credit_starve(dut):
    """L1, seeded: credit starvation and recovery. The partner's upper layer
    takes 30% of the time, stalls for 4000 cycles, takes 30%, stalls for
    10000 cycles, then takes everything: the DUT's gate holds TLPs back
    while the partner returns no credits and resumes on its UpdateFCs. The
    partner's receive-side credit check flags no overflow, it receives
    every accepted TLP once, in order, and the credits available match the
    architecture model every cycle."""
    credits = STARVE_CREDITS[cocotb.RANDOM_SEED % len(STARVE_CREDITS)]
    env = await starve_run(dut, credits, [(2000, 0.3), (4000, 0.0), (2000, 0.3),
                                          (10000, 0.0), (3000, 1.0)])
    stall_a, stall_b = env.refused_by_phase[1], env.refused_by_phase[3]
    assert stall_a > 3000 and stall_b > 9000, (
        f"the gate refused {stall_a} and {stall_b} cycles in the 4000- and 10000-cycle stalls")
    assert env.refused_by_phase[4] < 3000, "no recovery after the stall"


@cocotb.test()
async def credits_infinite(dut):
    """L1, seeded: the partner advertises every class infinite and its
    upper layer stalls: the gate never refuses, the credits available show
    all ones, and the partner receives every TLP."""
    env = await starve_run(dut, INFINITE, [(3000, 0.0), (2000, 0.3)])
    assert env.refused == 0, f"the gate refused {env.refused} cycles with infinite credits"
