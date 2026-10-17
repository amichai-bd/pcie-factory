"""pcie_dll TLP receive tests (wiki/src/dv/dll/DV_PLAN.md): the receive
checks, the receive buffer and upper stream, and Ack and Nak scheduling
(wiki/src/arch/dll/RETRY.md#receiving-tlps).

Every test runs through tb/dll_env.py: the receive reference (RxRef)
judges each TLP packet by the HAS rules with the partner's helpers, the
upper layer checks every delivered TLP against it, every Ack and Nak is
checked for its sequence number, and the architecture-model shadow gets
the same packets. Seeded tests take cocotb.RANDOM_SEED.
"""

from __future__ import annotations

import random

import cocotb

from tb.dll_env import (ACK_BOUND, ACTIVE, CLASSES, INACTIVE, INIT1, INIT2, Env, arch,
                        arch_partner_traffic, ptn, raw_dllp)

# Ack timing (MAS, timing): an idle stream starts the Ack of a lone TLP
# 35 cycles after its eop from phy (judged the next cycle, the Ack timer
# reaches 32 cycles after that, one cycle to the arbiter and one to offer).
ACK_IDLE = 35
# A due Nak, or the Ack after a duplicate, goes at the first opportunity:
# 3 cycles from the eop on an idle stream, plus at most one DLLP being sent
# (3 beats and the phy's framing cycle) ahead of it.
AT_ONCE = 3 + 4


def fc_dllp(typ: int, hdr: int, data: int) -> bytes:
    return raw_dllp(ptn.dllp_body(typ, hdr, data))


def hdr(fmt_type: int, length: int, tag: int, hdr_dw: int = 3) -> bytes:
    """A TLP header: byte 0, Length in bytes 2-3, a tag in bytes 4-5."""
    h = bytearray(4 * hdr_dw)
    h[0] = fmt_type
    h[2], h[3] = (length >> 8) & 0x03, length & 0xFF
    h[4], h[5] = (tag >> 8) & 0xFF, tag & 0xFF
    return bytes(h)


def payload(dws: int, tag: int) -> bytes:
    return bytes(((tag * 7 + i) * 13) & 0xFF for i in range(4 * dws))


async def bring_up(env: Env, *, by_tlp: bytes | None = None, bad_first: bytes = b"") -> None:
    """Hand-driven link-up to DL_Active: InitFC1 of every class with
    default-like credits, then FC_INIT2 ended by an InitFC2, or by the TLP
    ``by_tlp`` (a framed packet) when given; ``bad_first``, a bad TLP, is
    received in FC_INIT2 first, and its Nak must wait for DL_Active."""
    env.dut_link = True
    await env.run_until(lambda: env.state == INIT1, 10, "FC_INIT1")
    for cls in CLASSES:
        env.inject(fc_dllp(ptn.INITFC1[cls], 0, 0))
    await env.run_until(lambda: env.state == INIT2, 300, "FC_INIT2")
    if bad_first:
        env.inject(bad_first, dllp=False)
        await drain(env, 60)
        assert env.state == INIT2 and env.ref.bad == 1, "the bad TLP in FC_INIT2"
        assert not env.acknaks, "a Nak sent in FC_INIT2"
    if by_tlp is None:
        env.inject(fc_dllp(ptn.INITFC2["P"], 0, 0))
    else:
        env.inject(by_tlp, dllp=False)
    await env.run_until(lambda: env.state == ACTIVE, 300, "DL_Active")


async def drain(env: Env, extra: int = 60) -> None:
    await env.run_until(lambda: not env.raw, 20000, "injected packets received")
    await env.run(extra)


def first_acknak_after(env: Env, cycle: int, kind: str | None = None) -> int:
    got = next((s for s, _, k, _ in env.acknaks if s > cycle and (kind is None or k == kind)),
               None)
    assert got is not None, f"no {kind or 'Ack or Nak'} sent after cycle {cycle}"
    return got


@cocotb.test()
async def rx_directed(dut):
    """L0: the receive rules by hand, against the reference, with gaps and
    back-to-back packets: TLPs discarded in DL_Inactive and FC_INIT1; a
    good TLP ends FC_INIT2 and is delivered; the Ack timer (35 cycles on
    an idle stream) and the receive latency; every class and size stored;
    duplicates (Ack at once, D = 2048 included); ahead TLPs (Bad TLP, one
    Nak per NAK_SCHEDULED, D = 1 and 2047); bad LCRC, phy-flagged, too
    short, nullified and EDB without the inverse; each malformed kind
    acknowledged and not delivered; long packets beyond the beat counter;
    the link going down while a TLP arrives and while one is delivered."""
    rng = random.Random(cocotb.RANDOM_SEED)
    env = Env(dut, cocotb.RANDOM_SEED)
    await env.start()

    def frame(seq: int, tlp: bytes, **kw) -> bytes:
        return ptn.frame_tlp(seq % 4096, tlp, **kw)

    # DL_Inactive and FC_INIT1: TLPs are discarded silently.
    env.inject(frame(0, ptn.make_tlp("MWr", 1, 1)), dllp=False)
    await env.run(40)
    env.dut_link = True
    await env.run_until(lambda: env.state == INIT1, 10, "FC_INIT1")
    env.inject(frame(0, ptn.make_tlp("MWr", 1, 2)), dllp=False)
    await drain(env)
    assert env.ref.nrs == 0 and not env.delivered, "a TLP was taken before FC_INIT2"

    # FC_INIT2: a bad TLP's Nak waits for DL_Active; a good TLP ends
    # FC_INIT2 and is delivered and acknowledged.
    first = ptn.make_tlp("CplD", 4, 3)
    corrupt0 = bytearray(frame(0, first))
    corrupt0[9] ^= 0x01
    await bring_up(env, by_tlp=frame(0, first), bad_first=bytes(corrupt0))
    await env.run(80)
    assert env.delivered == [first], "the TLP that ended FC_INIT2 was not delivered"
    assert [k for _, _, k, _ in env.acknaks][:1] == ["nak"], "no Nak after DL_Active"
    seq = 1

    # A lone TLP on an idle stream: its Ack starts ACK_IDLE cycles after
    # its eop; it reaches the upper stream within the HAS's 4 cycles.
    await env.run(200)
    env.inject(frame(seq, ptn.make_tlp("MRd", tag=4)), dllp=False)
    seq += 1
    await drain(env, 60)
    eop = env.ref.store_cycle[-1]
    got = first_acknak_after(env, eop) - eop
    assert got == ACK_IDLE, f"Ack {got} cycles after the eop, expected {ACK_IDLE}"

    # Every class and size, 3- and 4-DW headers, gaps and back to back.
    tlps = [ptn.make_tlp(k, rng.randint(1, 32), 10 + i)
            for i, k in enumerate(("MWr", "MsgD", "CplD", "CfgWr"))]
    tlps += [ptn.make_tlp(k, tag=20 + i) for i, k in enumerate(("MRd", "Msg", "Cpl"))]
    tlps += [hdr(0x60, 32, 30, 4) + payload(32, 30), hdr(0x20, 1, 31, 4),
             hdr(0x4B, 1, 32) + payload(1, 32), hdr(0x74, 8, 33, 4) + payload(8, 33)]
    for t in tlps:
        env.inject(frame(seq, t), dllp=False, gaps=rng.choice((0, 0, 1, 3)))
        seq += 1
    await drain(env)

    # Duplicates: an Ack at once, nothing delivered; D = 2048 is one too.
    for back in (1, 3, 2048):
        await env.run(100)
        env.inject(frame(seq - back, tlps[0]), dllp=False)
        await drain(env, 20)
        eop = env.ref.dups[-1]
        got = first_acknak_after(env, eop) - eop
        assert got <= AT_ONCE, f"Ack {got} cycles after a duplicate, expected at most {AT_ONCE}"

    # Ahead: Bad TLP and one Nak per NAK_SCHEDULED setting.
    async def bad_case(pkt: bytes, *, err: bool = False, nullify: bool = False,
                       naks: int) -> None:
        before = env.ref.nak_sets
        env.inject(pkt, dllp=False, err=err, nullify=nullify)
        await drain(env, 20)
        assert env.ref.nak_sets - before == naks, "reference Nak settings"

    await env.run(100)
    await bad_case(frame(seq + 1, tlps[1]), naks=1)
    eop = env.ref.bads[-1]
    got = first_acknak_after(env, eop, "nak") - eop
    assert got <= AT_ONCE, f"Nak {got} cycles after an ahead TLP, expected at most {AT_ONCE}"
    await bad_case(frame(seq + 2047, tlps[1]), naks=0)
    good = ptn.make_tlp("Cpl", tag=40)
    env.inject(frame(seq, good), dllp=False)
    seq += 1
    # Bad LCRC, phy-flagged, too short, EDB without the inverted LCRC.
    corrupt = bytearray(frame(seq, tlps[2]))
    corrupt[7] ^= 0x10
    await bad_case(bytes(corrupt), naks=1)
    await bad_case(frame(seq, tlps[2]), err=True, naks=0)
    env.inject(frame(seq, good), dllp=False)
    seq += 1
    await bad_case(frame(seq, good)[:4], naks=1)
    await bad_case(frame(seq, good)[:2], naks=0)
    # Too short even though its 4 bytes equal the LCRC of nothing (00h):
    # only the length check rejects it.
    await bad_case(bytes(4), naks=0)
    env.inject(frame(seq, good), dllp=False)
    seq += 1
    await bad_case(frame(seq, tlps[3], nullify=True), nullify=True, naks=0)   # silent
    await bad_case(frame(seq, tlps[3]), nullify=True, naks=1)                 # EDB, good LCRC
    env.inject(frame(seq, good), dllp=False)
    seq += 1

    # Malformed: acknowledged, not delivered.
    mwr4 = hdr(0x40, 4, 50)
    bad_tlps = [
        mwr4 + payload(4, 50)[:14],              # not whole DWs
        mwr4[:8],                                # shorter than a 3-DW header
        mwr4 + payload(2, 50),                   # Length 4, 2 DWs
        hdr(0x60, 33, 51, 4) + payload(33, 51),  # 132-byte payload, Length agrees
        hdr(0x40, 0, 52) + payload(1, 52),       # Length 0 is 1024 DWs
        b"",                                     # no header: 6-byte packet
        b"\x40\x00",                             # 2 bytes
        hdr(0x40, 0, 53) + payload(144, 53),     # 588 bytes: beyond the beat counter
    ]
    for t in bad_tlps:
        env.inject(frame(seq, t), dllp=False, gaps=rng.choice((0, 1)))
        seq += 1
    env.inject(frame(seq, good), dllp=False)
    seq += 1
    long_bad = bytearray(frame(seq, hdr(0x40, 0, 54) + payload(150, 54)))
    long_bad[-1] ^= 1
    await bad_case(bytes(long_bad), naks=1)
    env.inject(frame(seq, good), dllp=False)
    seq += 1
    await drain(env, 200)
    assert env.ref.nrs == seq % 4096, f"reference NEXT_RCV_SEQ {env.ref.nrs}, expected {seq}"
    lat = env.check_rx(ack_bound=ACK_BOUND)
    env.check_shadow_rx()
    env.check_clean()
    dut._log.info("worst Ack %d cycles, worst receive latency %d cycles; %d TLPs, "
                  "%d Bad TLP, %d Malformed, %d Naks", lat["ack"], lat["rx"],
                  len(env.delivered), env.ref.bad, env.ref.malformed, env.ref.nak_sets)

    # Link down while a TLP arrives, and while one is being delivered:
    # nothing more is delivered, and after the next link-up NEXT_RCV_SEQ
    # starts again from 0.
    env.inject(frame(seq, hdr(0x60, 32, 60, 4) + payload(32, 60)), dllp=False, gaps=1)
    await env.run(30)
    env.dut_link = False
    await env.run_until(lambda: env.state == INACTIVE, 3, "DL_Inactive")
    await env.run(40)
    env.raw.clear()
    count = len(env.delivered)
    await bring_up(env)
    big = hdr(0x60, 32, 61, 4) + payload(32, 61)
    env.inject(frame(0, big), dllp=False)
    await env.run_until(lambda: env.up_beats, 400, "delivery started")
    env.dut_link = False
    await env.run_until(lambda: env.state == INACTIVE, 3, "DL_Inactive")
    await env.run(40)
    assert len(env.delivered) == count, "a TLP completed across the link-down"
    env.up_beats = []
    env.check_clean()


# ---------------------------------------------------------------------------
# Against the independent partner.

# Partner error injection for the error runs: its TLPs dropped, corrupted,
# nullified and phy-flagged on the way to the DUT, and the DUT's Acks and
# Naks lost on the way back (so the partner replays and the DUT sees
# duplicates), its own DLLPs corrupted.
ERRORS = {"tx_tlp_drop": 0.02, "tx_tlp_corrupt": 0.03, "tx_tlp_nullify": 0.02,
          "tx_tlp_flag": 0.02, "rx_dllp_drop": 0.15, "tx_dllp_corrupt": 0.03}
LIGHT_ERRORS = {"tx_tlp_drop": 0.002, "tx_tlp_corrupt": 0.002, "rx_dllp_drop": 0.01}

KINDS = {"P": ("MWr", "Msg", "MsgD"), "NP": ("MRd", "CfgWr"), "Cpl": ("Cpl", "CplD")}


def traffic(rng: random.Random, adv: dict[str, tuple[int, int]], count: int,
            max_dw: int = 32) -> list[bytes]:
    """Up to ``count`` random TLPs that the DUT's advertised credits cover
    with none returned (no UpdateFC needed): a finite class gets at
    most its advertisement in all, an infinite one any number."""
    left = {c: [h or None, d or None] for c, (h, d) in adv.items()}
    out: list[bytes] = []
    for i in range(count * 4):
        if len(out) == count:
            break
        cls = rng.choice(CLASSES)
        t = ptn.make_tlp(rng.choice(KINDS[cls]), rng.randint(1, max_dw), i)
        need = ptn.tlp_credits(t)
        if any(left[cls][f] is not None and left[cls][f] < need[f] for f in (0, 1)):
            continue
        for f in (0, 1):
            if left[cls][f] is not None:
                left[cls][f] -= need[f]
        out.append(t)
    return out


def fill(rng: random.Random, adv: dict[str, tuple[int, int]]) -> list[bytes]:
    """TLPs that use every advertised credit of every (finite) class: the
    header count and the data credits split among them, each with-data
    TLP 1 to 8 credits."""
    out: list[bytes] = []
    for cls, (h, d) in adv.items():
        kind = {"P": "MWr", "NP": "CfgWr", "Cpl": "CplD"}[cls]
        nodata = {"P": "Msg", "NP": "MRd", "Cpl": "Cpl"}[cls]
        if cls == "NP":
            # CfgWr carries 1 DW: one data credit each.
            withd = min(h, d)
            out += [ptn.make_tlp(kind, 1, len(out) + i) for i in range(withd)]
            out += [ptn.make_tlp(nodata, tag=len(out) + i) for i in range(h - withd)]
            continue
        withd = min(h, d)
        credits = [1] * withd
        for _ in range(d - withd):
            j = rng.choice([j for j, c in enumerate(credits) if c < 8] or [None])
            if j is None:
                break
            credits[j] += 1
        for c in credits:
            out.append(ptn.make_tlp(kind, rng.randint(4 * c - 3, 4 * c), len(out)))
        out += [ptn.make_tlp(nodata, tag=len(out) + i) for i in range(h - withd)]
    rng.shuffle(out)
    return out


async def partner_run(dut, *, errors: dict[str, float], count: int = 300,
                      stall: bool = False, max_dw: int = 32, limit: int = 200000,
                      gated: bool = False) -> None:
    seed = cocotb.RANDOM_SEED
    rng = random.Random(seed)
    partner = ptn.Partner("partner", inject=ptn.Inject(**errors), seed=seed)
    env = Env(dut, seed, partner=partner, rx_ready_p=0.3 if stall else 1.0)
    await env.start()
    env.dut_link = env.partner_link = True
    await env.run_until(lambda: env.state == ACTIVE and partner.state == ptn.ACTIVE
                        and env.shadow.state == arch.ACTIVE, 5000, "all in DL_Active")
    if stall:
        tlps = fill(rng, env.adv)
    elif gated:
        # Beyond the advertisement: the partner sends as the DUT's UpdateFCs
        # allow (no class is limited here).
        tlps = traffic(rng, {c: (0, 0) for c in CLASSES}, count, max_dw)
    else:
        tlps = traffic(rng, env.adv, count, max_dw)
    if stall:
        # The upper layer stalls while the partner sends every credit.
        env.rx_stall_until = env.cycle + 3000
    for t in tlps:
        partner.send(t)
    dut._log.info("seed %d, DUT credits %s, %d TLPs, injection %s", seed, env.adv,
                  len(tlps), errors)
    if stall:
        await env.run_until(lambda: env.cycle >= env.rx_stall_until, 4000, "the stall's end")
        assert not env.delivered and len(env.ref.expect) == len(tlps), (
            f"{len(env.ref.expect)} of {len(tlps)} TLPs stored while the upper layer stalled")
    await env.run_until(lambda: len(env.delivered) == len(tlps) and not partner.rbuf
                        and not partner.tx_queue, limit, "every TLP delivered and acknowledged")
    await env.run(200)
    assert env.delivered == tlps, "the upper stream does not carry the partner's TLPs in order"
    lat = env.check_rx(ack_bound=ACK_BOUND)
    env.check_shadow_rx()
    env.check_clean()
    took = arch_partner_traffic(env.adv, errors, seed, tlps)
    dut._log.info("worst Ack %d cycles (HAS %d), worst receive latency %d; %d Bad TLP, "
                  "%d Naks, %d duplicates; partner Ack wait max %d; partner stats %s; arch "
                  "model against a fresh partner: all delivered in %d cycles", lat["ack"],
                  ACK_BOUND, lat["rx"], env.ref.bad, env.ref.nak_sets, len(env.ref.dups),
                  partner.ack_wait_max, partner.stats, took)


@cocotb.test()
async def rx_traffic(dut):
    """L1, seeded: 300 random TLPs from the partner, every class within the
    DUT's credits, error-free: delivered once and in order, every Ack
    within the HAS's 118 cycles of its TLP's eop, the receive latency
    within 4 cycles; shadow and arch-model-against-partner cross-checks."""
    await partner_run(dut, errors={})


@cocotb.test()
async def rx_errors(dut):
    """L1/L2, seeded: the same with the partner's TLPs dropped (ahead),
    corrupted, nullified and phy-flagged, and the DUT's Acks lost (the
    partner replays: duplicates): no bad TLP reaches the upper stream,
    every TLP delivered exactly once and in order, one Nak per
    NAK_SCHEDULED setting."""
    await partner_run(dut, errors=ERRORS)


@cocotb.test()
async def rx_errors_gated(dut):
    """L2, seeded: the error injection of ``rx_errors`` with 300 TLPs beyond
    the DUT's advertisement, so the partner sends as the DUT's UpdateFCs
    allow. At the smallest credits it is the one entry that discards TLPs
    (bad and duplicate) in the smallest receive buffer, whose rewind no
    error-free entry reaches."""
    await partner_run(dut, errors=ERRORS, gated=True)


@cocotb.test()
async def rx_stall(dut):
    """L1/L2, seeded, every class finite: the upper layer stalls while the
    partner sends TLPs that use every advertised credit, then takes 30% of
    beats: the receive buffer holds them all and delivers them in order."""
    await partner_run(dut, errors={}, stall=True)


@cocotb.test()
async def rx_seq_wrap(dut):
    """L2: 4200 short TLPs with light errors, so NEXT_RCV_SEQ wraps past
    4095 with duplicates and Naks around it."""
    await partner_run(dut, errors=LIGHT_ERRORS, count=4200, max_dw=1, limit=400000)
