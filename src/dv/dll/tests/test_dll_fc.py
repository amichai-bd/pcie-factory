"""pcie_dll credit accounting tests (wiki/src/dv/dll/DV_PLAN.md, #214).

The transmit credit gate, CREDITS_CONSUMED and the credits available, the
receive-side CREDITS_ALLOCATED, CREDITS_RECEIVED and overflow check, and
UpdateFC on returned credits, on the real TLP paths, and the
credit-release port (FC_RELEASE 1). The environment
checks the gate (through the bind tap) every cycle against the
flow-control rule and the architecture model, the overflow check through
the receive reference, and every UpdateFC's value, cause and round-robin
order. Seeded tests take
cocotb.RANDOM_SEED, which the regression passes.
"""

from __future__ import annotations

import random

import cocotb
from cocotb.triggers import Timer

from tb.dll_env import (ACTIVE, CLASSES, INACTIVE, INIT1, INIT2, MODS, STATE_NAMES, Env,
                        arch_to_partner_traffic, ptn, raw_dllp)

# A credit returned in DL_Active is in an UpdateFC that starts within this
# many cycles when the phy never stalls: at most the packet on the stream,
# an Ack or Nak, and the two other classes' UpdateFCs ahead of it in round
# robin, each 3 beats and a framing cycle (FLOW_CONTROL.md, UpdateFC
# scheduling; RETRY.md, transmit priority).
UPD_BOUND = 20

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


async def auto_ack(env: Env) -> None:
    """A hand-driven far end that acknowledges every 32 cycles the newest
    TLP the DUT has sent since it entered DL_Active, so the replay buffer
    never holds the gate's TLPs back."""
    last = None
    while True:
        await Timer(32 * 8, "ns")
        sent = [p for c, p in env.tlp_packets if c >= env.active_since]
        if env.state != ACTIVE or not sent:
            last = None
            continue
        seq = ((sent[-1][0] & 0x0F) << 8) | sent[-1][1]
        if seq != last:
            env.inject(raw_dllp(ptn.dllp_body(ptn.ACK, seq)))
            last = seq


def tx_idle(env: Env) -> bool:
    return not env.tlp_q and env.tlp_cur is None


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
    """L0: the transmit credit gate against a hand-driven far end that
    acknowledges what the DUT sends. The upper layer offers random TLPs of
    every class; the gate is checked every
    cycle (idle random headers included) against the rule and the
    architecture model, and the credits available every cycle. Each round
    consumes until the gate refuses, then an UpdateFC raises the limits:
    usually by a compliant amount, sometimes beyond half the counter range
    (the gate must then refuse). A bulk phase wraps the header and data
    CREDITS_CONSUMED counters. Infinite fields never refuse. A link-down
    mid-TLP clears the counters."""
    seed = cocotb.RANDOM_SEED
    rng = random.Random(seed)
    env = Env(dut, seed, tlp_valid_p=0.7)
    await env.start()
    cocotb.start_soon(auto_ack(env))
    far = {"P": (6, 20), "NP": (3, 0), "Cpl": (0, 0)}   # NP data and Cpl infinite
    tag = 0
    for epoch in range(2):
        await bring_up(env, far)
        sh = env.shadow
        for rnd in range(30):
            for _ in range(12):
                env.send(random_tlp(rng, tag))
                tag += 1
            await env.run_until(lambda: tx_idle(env) or stalled(env), 6000,
                                "the source drained or refused")
            if tx_idle(env):
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
        await env.run_until(lambda: tx_idle(env), 6000, "the source drained")
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
                env.send(ptn.make_tlp("MWr", 32, tag & 0xFFFF))
                tag += 1
                await env.run(60)
                assert (len(env.accepted) > count) == taken, (
                    f"limit {extra_h}, {extra_d} beyond the write: taken "
                    f"{len(env.accepted) > count}, expected {taken}")
                if not taken:
                    env.inject(fc_dllp(ptn.UPDATEFC["P"], (sh.cc[0][0] + 10) % MODS[0],
                                       (sh.cc[0][1] + 100) % MODS[1]))
                    await env.run_until(lambda: tx_idle(env), 300, "the write taken")
            # Bulk: enough 128-byte writes to wrap both P counters.
            for _ in range(4):
                env.inject(fc_dllp(ptn.UPDATEFC["P"], (sh.cc[0][0] + 128) % MODS[0],
                                   (sh.cc[0][1] + 2048) % MODS[1]))
                await env.run(20)
                for _ in range(120):
                    env.send(ptn.make_tlp("MWr", 32, tag & 0xFFFF))
                    tag += 1
                await env.run_until(lambda: tx_idle(env), 20000, "bulk writes taken")
            # Link down in the middle of a TLP: everything clears.
            env.send(ptn.make_tlp("CplD", 32, 1))
            env.inject(fc_dllp(ptn.UPDATEFC["P"], (sh.cc[0][0] + 10) % MODS[0],
                               (sh.cc[0][1] + 10) % MODS[1]))
            await env.run_until(lambda: env.tlp_cur is not None and env.tlp_pos > 3, 200,
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


def rx_items(rng: random.Random, count: int, tag: int) -> list[bytes]:
    """TLPs to receive: well-formed of every class, some malformed (a byte
    count other than the header's, or under 4 bytes)."""
    items = []
    for i in range(count):
        tlp = random_tlp(rng, tag + i, big=0.1)
        r = rng.random()
        if r < 0.08:
            tlp = tlp[:-4] if len(tlp) > 12 else tlp + bytes(4)
        elif r < 0.11:
            tlp = tlp[:2]
        items.append(tlp)
    return items


async def rx_deliver(env: Env, items: list[bytes], *, gap: int = 0, rounds: int = 40) -> None:
    """Send ``items`` to the DUT in order, framed from its NEXT_RCV_SEQ, as
    a far end that replays: after each drain, whatever was not accepted (an
    overflowed TLP and the ones behind it, which arrive ahead) goes again
    from the first one not accepted."""
    pending = list(items)
    for _ in range(rounds):
        if not pending:
            return
        start = env.ref.nrs
        for i, tlp in enumerate(pending):
            env.inject(ptn.frame_tlp((start + i) % 4096, tlp), dllp=False)
            if gap:
                env.raw.extend([None] * gap)
        await env.run_until(lambda: not env.raw, 40000, "TLPs received")
        await env.run(80)
        pending = pending[(env.ref.nrs - start) % 4096:]
        if pending and env.ref.overflow == 0 and env.rx_stall_until <= env.cycle:
            raise AssertionError(f"{len(pending)} TLPs not accepted with no overflow")
    assert not pending, f"{len(pending)} TLPs never accepted"


@cocotb.test()
async def fc_rx_accounting(dut):
    """L0/L1, per parameter set: receive accounting on real TLPs from a
    hand-driven far end that replays. TLPs stored, malformed (their
    header's credits returned at once, nothing under 4 bytes) and, with
    every class finite and the upper layer stalling, overflowing (Receiver
    overflow, no Ack or Nak: the TLPs behind arrive ahead and are sent
    again); credits returned at each delivered eop. Every UpdateFC carries
    CREDITS_ALLOCATED, is due, goes in round robin, and a return is sent
    within the bound; returns in FC_INIT2 are in the entry UpdateFC;
    refreshes; link down clears the counters. With FC_RELEASE 1, posted and
    non-posted credits return on release, and 40 configuration writes pass
    an infinite non-posted data field's reserve. The reference and the
    architecture-model shadow judge every TLP."""
    seed = cocotb.RANDOM_SEED
    rng = random.Random(seed)
    env = Env(dut, seed)
    await env.start()
    finite = all(v for cls in CLASSES for v in env.adv[cls])
    env.upd_bound = UPD_BOUND
    far = {"P": (8, 32), "NP": (8, 8), "Cpl": (0, 0)}
    tag = 0

    async def init2(e: Env) -> None:
        if e.rel:
            # The phy takes nothing, so the InitFC2 group never completes and
            # the DUT stays in FC_INIT2 while it delivers and the upper layer
            # releases: those releases are in the entry UpdateFC.
            e.ready_p = 0.0
        await rx_deliver(e, [ptn.make_tlp(k, 2, 7) for k in ("MWr", "MRd", "CplD")]
                         + [ptn.make_tlp("MWr", 4, 8)[:-4]])
        if e.rel:
            await e.run_until(lambda: e.released_init2 >= 2, 400, "releases in FC_INIT2")
            assert e.state == INIT2, "left FC_INIT2 with the phy stalled"
            e.ready_p = 1.0

    for epoch in range(2):
        await bring_up(env, far, in_init2=init2)
        await rx_deliver(env, rx_items(rng, 120, tag), gap=2)
        tag += 120
        if finite:
            # A slow upper layer and a long stall: bursts overflow, then a
            # burst of each class beyond its credits.
            env.rx_ready_p = 0.3
            env.rx_stall_until = env.cycle + 600
            await rx_deliver(env, rx_items(rng, 80, tag))
            tag += 80
            for kind in ("MWr", "MRd", "CplD"):
                env.rx_stall_until = env.cycle + 400
                await rx_deliver(env, [ptn.make_tlp(kind, 32, tag + i) for i in range(20)])
                tag += 20
            env.rx_ready_p = 1.0
        for kind in ("MWr", "MRd", "CplD", "Msg"):
            await rx_deliver(env, [ptn.make_tlp(kind, 8, tag + i) for i in range(12)])
            tag += 12
        if env.rel:
            # More non-posted data than an infinite field's reserve (24
            # credits) holds: it must free at delivery, not wait for a
            # release (FLOW_CONTROL.md, credit release).
            await rx_deliver(env, [ptn.make_tlp("CfgWr", 1, tag + i) for i in range(40)])
            tag += 40
        await env.run(2 * 2048 + 100)     # refreshes after the returns
        if epoch == 0:
            # Link down while TLPs are stored and one is being delivered.
            if finite:
                env.rx_stall_until = env.cycle + 50
            for i in range(4):
                env.inject(ptn.frame_tlp((env.ref.nrs + i) % 4096,
                                         ptn.make_tlp("MWr", 8, i)), dllp=False)
            await env.run(60)
            env.dut_link = False
            await wait_state(env, INACTIVE, 3)
            env.raw.clear()
            await env.run(30)
    stats = env.check_rx()
    # The arch shadow judges every TLP as the reference does, overflows
    # included; a link-down may leave it exactly one TLP apart (env, check_shadow_rx).
    link_down_diffs = env.check_shadow_rx()
    dut._log.info("arch shadow: all %d TLP verdicts agree (%d overflow); link-down "
                  "differences %s", len(env.ref_verdicts), env.ref.overflow, link_down_diffs)
    if finite:
        assert env.ref.overflow > 0, "no overflow with every class finite and a stalling layer"
    if env.rel:
        dut._log.info("releases %s (P, NP), %d of them in FC_INIT2", env.released,
                      env.released_init2)
    dut._log.info("seed %d, credits %s: %d TLPs delivered, %d malformed, %d overflow; "
                  "UpdateFC latency after a return at most %d cycles; %s", seed, env.adv,
                  len(env.delivered), env.ref.malformed, env.ref.overflow, env.upd_lat_max,
                  stats)
    env.check_clean()


async def starve_run(dut, credits: dict[str, tuple[int, int]], schedule) -> Env:
    """The upper layer sends TLPs through the DUT to the independent
    partner, whose receive-side check judges every one against its
    advertised credits. ``schedule`` is a list of
    (cycles, upper-layer take probability) for the partner's upper layer."""
    seed = cocotb.RANDOM_SEED
    rng = random.Random(seed)
    partner = ptn.Partner("partner", credits=credits, seed=seed)
    env = Env(dut, seed, partner=partner, tlp_valid_p=0.9)
    await env.start()
    dut._log.info("seed %d, DUT credits %s, partner credits %s", seed, env.adv, credits)
    env.dut_link = env.partner_link = True
    await env.run_until(lambda: env.state == ACTIVE and partner.state == ptn.ACTIVE, 400,
                        "both ends in DL_Active")
    tag = 0
    for _ in range(3000):
        env.send(random_tlp(rng, tag))
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
    env.tlp_q.clear()
    partner.upper_ready = True
    await env.run_until(lambda: env.tlp_cur is None and len(partner.received) ==
                        len(env.accepted), 20000, "every accepted TLP at the partner")
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
    model = arch_to_partner_traffic(env.adv, fresh, list(env.accepted), schedule,
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


async def starve_rx_run(dut, schedule, *, release=None, count: int = 1500,
                        drain: int = 60000, tail=None) -> Env:
    """The independent partner sends TLPs to the DUT, gated by the DUT's
    UpdateFCs; the DUT's upper layer follows ``schedule`` (cycles, take
    probability; below 1 only with every class finite), then takes all.
    With FC_RELEASE 1, ``release`` is the releaser's schedule (cycles,
    release probability; 0 releases nothing), in parallel, then all.
    ``tail(env, partner, tlps)`` runs after that drain and appends what
    it sends to ``tlps``; the final checks cover it."""
    seed = cocotb.RANDOM_SEED
    rng = random.Random(seed)
    partner = ptn.Partner("partner", seed=seed)
    env = Env(dut, seed, partner=partner)
    await env.start()
    if callable(schedule):
        schedule = schedule(env)
    if release is not None:
        assert env.rel, "a release schedule needs FC_RELEASE 1"
        cocotb.start_soon(release_schedule(env, release))
    env.upd_bound = UPD_BOUND
    env.dut_link = env.partner_link = True
    await env.run_until(lambda: env.state == ACTIVE and partner.state == ptn.ACTIVE, 400,
                        "both ends in DL_Active")
    tlps = [random_tlp(rng, i) for i in range(count)]
    for t in tlps:
        partner.send(t)
    for cycles, take in schedule:
        env.rx_ready_p = take if take < 1.0 else 1.0
        if take == 0.0:
            env.rx_stall_until = env.cycle + cycles
            env.rx_ready_p = 1.0
        start = len(env.delivered)
        await env.run(cycles)
        dut._log.info("%d cycles, upper layer takes %.0f%%: %d TLPs delivered", cycles,
                      100 * take, len(env.delivered) - start)
    env.rx_ready_p = 1.0
    await env.run_until(lambda: len(env.delivered) == len(tlps) and not partner.rbuf, drain,
                        "every TLP delivered and acknowledged")
    if tail is not None:
        await tail(env, partner, tlps)
    await env.run(200)
    if env.rel:
        assert not env.rel_q[0] and not env.rel_q[1], "TLPs left unreleased after the drain"
    assert env.delivered == tlps, "the DUT did not deliver the partner's TLPs once, in order"
    assert env.ovf_seen == 0 and env.ref.overflow == 0, "Receiver overflow with a compliant partner"
    stats = env.check_rx()
    env.check_shadow_rx()
    dut._log.info("partner stats %s; UpdateFC latency after a return at most %d cycles; %s",
                  partner.stats, env.upd_lat_max, stats)
    env.check_clean()
    return env


# The receive-side starvation schedule: (cycles, take probability).
STARVE_RX = [(2000, 0.3), (4000, 0.0), (2000, 0.3), (10000, 0.0)]


@cocotb.test()
async def credit_starve_rx(dut):
    """L1, seeded: the credits target on the receive side, every class
    finite. The DUT's upper layer takes 30% of the time, stalls 4000
    cycles, takes 30%, stalls 10000 cycles, then takes all, while the
    partner sends as fast as the DUT's UpdateFCs allow: no Receiver
    overflow, every TLP delivered once and in order, every UpdateFC
    checked, cross-checked with the reference and the architecture model.
    300 TLPs (#435): the schedule delivers under 100, so the partner is
    still credit-bound when it ends; ``credit_starve_rx_full`` sends 1500."""
    await starve_rx_run(dut, STARVE_RX, count=300)


@cocotb.test()
async def credit_starve_rx_full(dut):
    """L2: ``credit_starve_rx`` at the full depth, 1500 TLPs."""
    await starve_rx_run(dut, STARVE_RX, count=1500)


async def release_schedule(env: Env, schedule) -> None:
    """Steer the releaser: (cycles, release probability) phases, 0 holding
    every release; then release at once."""
    for cycles, p in schedule:
        env.rel_p = p if p > 0 else 1.0
        env.rel_stall_until = env.cycle + cycles if p == 0 else 0
        await Timer(cycles * 8, "ns")
    env.rel_p, env.rel_stall_until = 1.0, 0


async def hold_every_header(env: Env, partner: ptn.Partner, tlps: list[bytes]) -> None:
    """The directed tail of ``credit_release``: the random phases reach a
    full class only when a header limit happens to stop the partner's
    in-order queue, and a posted TLP short of data credits at its head
    stops every class behind it, so some seeds never get there (#429).
    Here, with every credit back at the partner and every release held,
    the partner sends header-only TLPs in order: FC_NPH reads, FC_PH
    messages, then one TLP more of the last finite class. Only the header
    limits stop that burst, so the upper layer holds every finite P and NP
    header credit and the last TLP stays queued (the partner starved) until
    the releases resume."""
    kinds = {"NP": "MRd", "P": "Msg"}
    fin = [cls for cls in ("NP", "P") if env.adv[cls][0]]
    if not fin:
        return
    env.held_max_random = [list(h) for h in env.held_max]

    def credits_back() -> bool:
        return all(partner.avail(cls)[f] == env.adv[cls][f]
                   for cls in fin for f in (0, 1) if env.adv[cls][f])
    await env.run_until(credits_back, 4000, "every P and NP credit back at the partner")
    env.rel_stall_until = 1 << 62
    tag = len(tlps)
    burst = [ptn.make_tlp(kinds[cls], 0, tag + k) for k, cls in
             enumerate(c for c in fin for _ in range(env.adv[c][0]))]
    extra = ptn.make_tlp(kinds[fin[-1]], 0, tag + len(burst))
    for t in burst + [extra]:
        partner.send(t)
    full = len(tlps) + len(burst)

    def held_full() -> bool:
        return len(env.delivered) == full and all(
            env.held[CLASSES.index(cls)][0] == env.adv[cls][0] for cls in fin)
    await env.run_until(held_full, 4000, "every finite P and NP header credit held")
    await env.run(500)
    assert len(env.delivered) == full and partner.tx_queue == [extra], (
        f"releases held: {len(env.delivered) - len(tlps)} of the burst's {len(burst) + 1} "
        f"TLPs delivered, expected {len(burst)} (the last one starved)")
    tlps.extend(burst + [extra])
    env.rel_stall_until, env.rel_p = 0, 1.0
    await env.run_until(lambda: len(env.delivered) == len(tlps) and not partner.rbuf, 4000,
                        "the starved TLP delivered after the releases")


@cocotb.test()
async def credit_release(dut):
    """L1/L2, seeded, FC_RELEASE 1: credit starvation with delayed release.
    The partner sends as fast as the DUT's UpdateFCs allow; the DUT's upper
    layer takes every beat (and, with every class finite, also stalls the
    stream) but releases posted and non-posted credits late: in 30% of
    cycles, none for 4000 cycles, 30%, none for 10000, then all; then a
    directed burst with every release held (``hold_every_header``). The
    receiver never overflows, the upper layer never holds more than the
    DUT advertises and does hold every finite P and NP header credit (the
    partner starved), every TLP is delivered once and in order, and each
    release is in an UpdateFC within the bound, cross-checked with the
    reference and the architecture model."""
    phases = [(2000, 0.3), (4000, 0.0), (2000, 0.3), (10000, 0.0)]

    def rx(env: Env):
        # The receive stream stalls too only with every class finite.
        finite = all(v for cls in CLASSES for v in env.adv[cls])
        return phases if finite else [(sum(c for c, _ in phases), 1.0)]

    env = await starve_rx_run(dut, rx, release=phases, count=600, drain=200000,
                              tail=hold_every_header)
    finite = [CLASSES[c] for c in (0, 1) if env.adv[CLASSES[c]][0]]
    held_all = [CLASSES[c] for c in (0, 1) if env.adv[CLASSES[c]][0]
                and env.held_max[c][0] == env.adv[CLASSES[c]][0]]
    assert finite and held_all == finite, (
        f"the upper layer held all header credits of {held_all}, expected every finite "
        f"class of {finite}; held at most {env.held_max}")
    assert all(env.released), f"releases per class {env.released}"
    dut._log.info("held at most %s (P, NP; header, data) of %s, %s in the random phases; "
                  "%s releases; credits held in full: %s", env.held_max, env.adv,
                  env.held_max_random, env.released, held_all)


@cocotb.test()
async def credits_infinite_rx(dut):
    """L1, seeded: the same with every DUT class infinite and an upper
    layer that never stalls (HAS, receive): no overflow of the reserve.
    500 TLPs (#435); ``credits_infinite_rx_full`` sends 1500."""
    await starve_rx_run(dut, [(6000, 1.0)], count=500)


@cocotb.test()
async def credits_infinite_rx_full(dut):
    """L2: ``credits_infinite_rx`` at the full depth, 1500 TLPs."""
    await starve_rx_run(dut, [(6000, 1.0)], count=1500)
