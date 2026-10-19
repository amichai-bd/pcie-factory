"""pcie_dll TLP transmit tests (wiki/src/dv/dll/DV_PLAN.md): sequence
numbers, LCRC, the replay buffer, the replay timer, REPLAY_NUM and received
Ack and Nak (wiki/src/arch/dll/RETRY.md).

Every test runs on tb/pcie_dll_tb.sv through tb/dll_env.py. The far end is
hand-driven DLLPs (directed tests) or the independent partner with its
fault hooks; the architecture-model shadow runs beside the DUT. These
tests keep the partner's credits infinite, so the credit gate never holds
a TLP back here; the gate is tested in tests/test_dll_fc.py.
"""

from __future__ import annotations

import random

import cocotb

from tb.dll_env import (ACK_BOUND, ACTIVE, CLASSES, COV_RBUF, COV_REPLAY, ERR_PROTOCOL, ERR_ROLLOVER,
                        ERR_TIMEOUT, INIT1, INIT2, SEQ_MOD, Env, arch, arch_partner_send, ptn,
                        raw_dllp)

# RETRY.md, LCRC: sequence 0 and this memory read give these LCRC bytes.
LCRC_EXAMPLE_TLP = bytes.fromhex("000000010001000f00000000")
LCRC_EXAMPLE_CRC = bytes.fromhex("e1f1e7e2")

REPLAY_TIMEOUT = 356        # cycles (RETRY.md, timers)
HOLD_FALLBACK = 1024        # cycles (RETRY.md, REPLAY_NUM)
TX_LATENCY = 4              # HAS targets: TLP eop accepted to its sop on the phy stream
RBUF_BYTES = 1024
INFINITE = {"P": (0, 0), "NP": (0, 0), "Cpl": (0, 0)}

KINDS = ("MWr", "MWr64", "MRd", "CfgWr", "Msg", "MsgD", "Cpl", "CplD")


def make_tlp(kind: str, payload_dw: int, tag: int) -> bytes:
    """A well-formed TLP; MWr64 is a memory write with a 4-DW header."""
    if kind == "MWr64":
        t = bytearray(ptn.make_tlp("MWr", payload_dw, tag))
        t[0] = 0x60
        return bytes(t[:12]) + bytes(4) + bytes(t[12:])
    return ptn.make_tlp(kind, payload_dw, tag)


def rand_tlp(rng: random.Random, tag: int) -> bytes:
    kind = rng.choice(KINDS)
    payload = rng.choice((1, 32, rng.randint(1, 32)))
    return make_tlp(kind, payload, tag & 0xFFFF)


def framed(tlp: bytes) -> int:
    """Bytes a TLP takes in the replay buffer: sequence number, TLP, LCRC."""
    return len(tlp) + 6


def ack(seq: int, nak: bool = False) -> bytes:
    return raw_dllp(ptn.dllp_body(ptn.NAK if nak else ptn.ACK, seq % SEQ_MOD))


async def wait_state(env: Env, state: int, limit: int) -> None:
    await env.run_until(lambda: env.state == state, limit, f"state {state}")


async def active_by_hand(env: Env) -> None:
    """Link up against a hand-driven far end advertising infinite credits,
    to DL_Active, past the entry UpdateFCs."""
    env.dut_link = True
    await wait_state(env, INIT1, 10)
    for cls in CLASSES:
        env.inject(raw_dllp(ptn.dllp_body(ptn.INITFC1[cls], 0, 0)))
    await wait_state(env, INIT2, 300)
    env.inject(raw_dllp(ptn.dllp_body(ptn.INITFC2["P"], 0, 0)))
    await wait_state(env, ACTIVE, 300)
    await env.run(30)


def idle(env: Env) -> bool:
    return (not env.tlp_q and env.tlp_cur is None and not env.raw and not env.in_pkt
            and not int(env.dut.pkt_tx_valid_o.value))


async def settle(env: Env, limit: int = 3000, extra: int = 8) -> None:
    """Run until nothing is offered or sent, then a few cycles more."""
    await env.run_until(lambda: idle(env), limit, "transmit idle")
    await env.run(extra)
    await env.run_until(lambda: idle(env), limit, "transmit idle")


def sent_since(env: Env, start: int) -> list[tuple[int, bool]]:
    return [(s, r) for _, s, r in env.tlp_sent[start:]]


async def partner_env(dut, seed: int, **kw) -> tuple[Env, ptn.Partner]:
    inject = kw.pop("inject", None)
    partner = ptn.Partner("partner", credits=INFINITE, inject=inject, seed=seed)
    env = Env(dut, seed, partner=partner, **kw)
    await env.start()
    env.dut_link = env.partner_link = True
    await env.run_until(lambda: env.state == ACTIVE and partner.state == ptn.ACTIVE, 400,
                        "both ends in DL_Active")
    await env.run(30)
    return env, partner


# ---------------------------------------------------------------------------


@cocotb.test()
async def lcrc_kat(dut):
    """L0: sequence numbers and the LCRC. The first TLP after link-up is
    sequence 0 and the retry page's example gives its LCRC E1 F1 E7 E2;
    then random TLPs of every shape, with gaps in the upper stream and phy
    stalls, each framed with the next sequence number and the LCRC of both
    models and zlib, sent once each in order. TX latency target: a TLP
    alone starts on the phy stream within 4 cycles of its eop."""
    rng = random.Random(cocotb.RANDOM_SEED)
    env = Env(dut, cocotb.RANDOM_SEED)
    await env.start()
    await active_by_hand(env)
    env.send(LCRC_EXAMPLE_TLP)
    await settle(env)
    seq0, pkt = env.tlp_packets[0]
    assert pkt == bytes(2) + LCRC_EXAMPLE_TLP + LCRC_EXAMPLE_CRC, (
        f"cycle {seq0}: example sent as {pkt.hex()}, expected sequence 0000 and LCRC "
        f"{LCRC_EXAMPLE_CRC.hex()}")
    # Alone on the stream: the TX latency target.
    for i in range(6):
        env.send(rand_tlp(rng, i))
        await settle(env)
    lat = env.tx_latency()
    assert len(lat) == 7 and max(lat) <= TX_LATENCY, f"TX latency {lat}, target {TX_LATENCY}"
    dut._log.info("TX latency (eop taken to sop taken), alone: %s cycles", lat)
    env.inject(ack(6))
    # Back to back, upper gaps and phy stalls; Acks keep the buffer moving.
    env.tlp_valid_p, env.ready_p = 0.6, 0.6
    tlps = [rand_tlp(rng, 100 + i) for i in range(80)]
    env.send(*tlps)
    acked = 6
    while acked != 86:
        await env.run(60)
        firsts = [s for _, s, r in env.tlp_sent if not r]
        if firsts and firsts[-1] != acked:
            acked = firsts[-1]
            env.inject(ack(acked))
    await settle(env)
    seqs = [s for _, s, _ in env.tlp_sent]
    assert seqs == list(range(87)), (
        f"{len(seqs)} sequence numbers sent: {env.tlp_sent[:3]} .. replays "
        f"{[x for x in env.tlp_sent if x[2]][:5]}, errors {env.err_counts}, "
        f"acks {env.acknak_log[:8]}")
    assert not any(r for _, _, r in env.tlp_sent), "a TLP was replayed"
    assert [p[2:-4] for _, p in env.tlp_packets] == env.accepted, "TLPs sent differ"
    assert env.err_counts[ERR_TIMEOUT] == 0
    env.check_shadow_tx()
    env.check_clean()


@cocotb.test()
async def replay_directed(dut):
    """L0: received Ack and Nak and the replay, by hand. An Ack frees TLPs;
    a Nak replays what follows its sequence number, in order and complete;
    a Nak naming ACKD_SEQ replays everything; an Ack with nothing sent
    unacknowledged stops the timer, and a Nak then replays nothing; out-of-range Acks and Naks are DL
    protocol errors that change nothing; the replay timer expires 356
    cycles after the last TLP's eop, holds while the LTSSM is out of L0 and
    restarts on forward progress; an Ack during a replay skips the TLPs it
    removes; a Nak during a replay replays again after it."""
    env = Env(dut, cocotb.RANDOM_SEED)
    await env.start()
    await active_by_hand(env)
    mrd = [make_tlp("MRd", 0, i) for i in range(16)]

    # Ack, then Nak with progress, then Nak naming ACKD_SEQ.
    env.send(*mrd[:5])
    await settle(env)
    start = len(env.tlp_sent)
    env.inject(ack(1))
    env.inject(ack(2, nak=True))
    await settle(env)
    assert sent_since(env, start) == [(3, True), (4, True)], (
        f"after Nak 2: {sent_since(env, start)}, expected 3, 4 replayed")
    start = len(env.tlp_sent)
    env.inject(ack(2, nak=True))
    await settle(env)
    assert sent_since(env, start) == [(3, True), (4, True)], "after Nak 2 again"
    # Everything acknowledged: the timer stops, nothing more is sent.
    env.inject(ack(4))
    await settle(env)
    start = len(env.tlp_sent)
    await env.run(REPLAY_TIMEOUT + 100)
    assert len(env.tlp_sent) == start and env.err_counts[ERR_TIMEOUT] == 0, (
        "a replay or timeout with everything acknowledged")
    COV_REPLAY.sample(timer="stopped_by_ack")
    # A Nak with nothing sent and unacknowledged replays nothing.
    env.inject(ack(4, nak=True))
    await settle(env)
    assert len(env.tlp_sent) == start and env.replay_runs == 2, "a replay of nothing"
    # Out of range: ahead of the newest sent, and behind ACKD_SEQ.
    env.inject(ack(10))
    env.inject(ack(1, nak=True))
    await settle(env)
    assert env.err_counts[ERR_PROTOCOL] == 2 and env.proto_expected == 2, (
        f"DL protocol errors {env.err_counts[ERR_PROTOCOL]}, expected 2")
    assert len(env.tlp_sent) == start, "an out-of-range Nak replayed"

    # Replay timeout: 356 cycles from the eop of the first TLP sent with
    # the timer stopped (later ones do not restart it).
    env.send(*mrd[5:8])
    await settle(env)
    last_eop = env.tlp_packets[-3][0] + 9          # 18 bytes: 9 beats
    start = len(env.tlp_sent)
    await env.run_until(lambda: env.err_counts[ERR_TIMEOUT] == 1, 600, "replay timeout")
    got = env.err_cycles[ERR_TIMEOUT][-1] - last_eop
    assert got == REPLAY_TIMEOUT, f"timeout {got} cycles after the eop, expected 356"
    await settle(env)
    assert sent_since(env, start) == [(5, True), (6, True), (7, True)], "timeout replay"
    env.inject(ack(7))
    await settle(env)

    # Held outside L0: 100 cycles of Recovery delay the timeout by 100.
    env.send(mrd[8])
    await settle(env, extra=2)
    last_eop = env.tlp_packets[-1][0] + 9
    await env.run(50)
    env.l0 = False
    await env.run(100)
    env.l0 = True
    await env.run_until(lambda: env.err_counts[ERR_TIMEOUT] == 2, 600, "replay timeout")
    got = env.err_cycles[ERR_TIMEOUT][-1] - last_eop
    assert got == REPLAY_TIMEOUT + 100, f"timeout {got} cycles after the eop, expected 456"
    COV_REPLAY.sample(timer="held_outside_l0")
    await settle(env)
    env.inject(ack(8))
    await settle(env)

    # Restarted by forward progress: Ack 9 of 9 and 10 restarts the timer.
    env.send(mrd[9], mrd[10])
    await settle(env)
    await env.run(200)
    env.inject(ack(9))
    await env.run_until(lambda: env.acknak_log and env.acknak_log[-1][2] == 9, 20, "Ack 9")
    acted = env.acknak_log[-1][0]
    start = len(env.tlp_sent)
    await env.run_until(lambda: env.err_counts[ERR_TIMEOUT] == 3, 600, "replay timeout")
    got = env.err_cycles[ERR_TIMEOUT][-1] - acted
    assert got == REPLAY_TIMEOUT + 1, f"timeout {got} cycles after the Ack, expected 357"
    COV_REPLAY.sample(timer="restarted_by_ack")
    await settle(env)
    assert sent_since(env, start) == [(10, True)], "replay after the restart"
    env.inject(ack(10))
    await settle(env)

    # An Ack during a replay: the TLPs it removes are skipped.
    mwr = [make_tlp("MWr", 32, 0x100 + i) for i in range(4)]       # seq 11 to 14
    env.send(*mwr)
    await settle(env)
    start = len(env.tlp_sent)
    env.inject(ack(10, nak=True))
    await env.run_until(lambda: len(env.tlp_sent) > start, 40, "replay start")
    env.inject(ack(12))
    await settle(env)
    assert sent_since(env, start) == [(11, True), (13, True), (14, True)], (
        f"replay with Ack 12 during it: {sent_since(env, start)}")
    # A Nak during a replay: a second replay after it.
    start = len(env.tlp_sent)
    env.inject(ack(12, nak=True))
    await env.run_until(lambda: len(env.tlp_sent) > start, 40, "replay start")
    env.inject(ack(12, nak=True))
    await settle(env)
    assert sent_since(env, start) == [(13, True), (14, True), (13, True), (14, True)], (
        f"Nak during a replay: {sent_since(env, start)}")
    env.inject(ack(14))
    await settle(env)
    assert env.replay_runs == 8, f"{env.replay_runs} replays, expected 8"
    env.check_shadow_tx()
    env.check_clean()


@cocotb.test()
async def rollover_directed(dut):
    """L0: REPLAY_NUM and its rollover. With no Ack, the fourth replay rolls
    REPLAY_NUM over: Rollover and retrain_req_o pulse in one cycle and no TLP
    starts until the LTSSM has left L0 and come back, or for 1024 cycles
    when it never leaves. Forward progress sets REPLAY_NUM to 0, so three
    more replays do not roll it over and the fourth does."""
    env = Env(dut, cocotb.RANDOM_SEED)
    await env.start()
    await active_by_hand(env)
    env.send(make_tlp("MRd", 0, 1), make_tlp("MWr", 4, 2))

    # The LTSSM ignores the request: the hold ends after 1024 cycles.
    await env.run_until(lambda: env.err_counts[ERR_ROLLOVER] == 1, 4 * 500, "rollover")
    roll = env.err_cycles[ERR_ROLLOVER][0]
    # The fourth replay starts held: three have sent TLPs.
    assert env.err_counts[ERR_TIMEOUT] == 4 and env.replay_runs == 3, (
        f"{env.err_counts[ERR_TIMEOUT]} timeouts and {env.replay_runs} replays sent at the "
        f"rollover")
    assert env.retrains == [roll], f"retrain_req_o at {env.retrains}, rollover at {roll}"
    start = len(env.tlp_sent)
    await env.run_until(lambda: len(env.tlp_sent) > start, HOLD_FALLBACK + 50, "replay")
    held = env.tlp_sent[start][0] - roll
    assert HOLD_FALLBACK <= held <= HOLD_FALLBACK + 2, (
        f"first TLP {held} cycles after the rollover, expected the 1024-cycle hold")
    COV_REPLAY.sample(rollover="fallback")
    dut._log.info("rollover hold without a retrain: %d cycles", held)
    await settle(env)

    # Progress after three replays sets REPLAY_NUM to 0; with a retrain
    # this time, the hold ends when the LTSSM is back in L0.
    env.retrain_cycles = 300
    await env.run_until(lambda: env.err_counts[ERR_TIMEOUT] == 7, 3 * 500, "3 timeouts")
    await settle(env)
    env.inject(ack(0))
    await env.run_until(lambda: env.err_counts[ERR_TIMEOUT] == 10, 3 * 500, "3 timeouts")
    assert env.err_counts[ERR_ROLLOVER] == 1, "rollover after 3 replays since progress"
    await env.run_until(lambda: env.err_counts[ERR_ROLLOVER] == 2, 500, "rollover")
    roll = env.err_cycles[ERR_ROLLOVER][1]
    assert env.err_counts[ERR_TIMEOUT] == 11 and env.retrains[-1] == roll
    start = len(env.tlp_sent)
    await env.run_until(lambda: len(env.tlp_sent) > start, 400, "replay after the retrain")
    held = env.tlp_sent[start][0] - roll
    assert 300 <= held <= 303, f"first TLP {held} cycles after the rollover, Recovery 300"
    COV_REPLAY.sample(rollover="retrained")
    dut._log.info("rollover hold through a 300-cycle Recovery: %d cycles", held)
    await settle(env)
    assert [s for _, s, _ in env.tlp_sent[start:]] == [1], "replay after the retrain"
    env.inject(ack(1))
    await settle(env)
    env.check_shadow_tx()
    env.check_clean()


@cocotb.test()
async def rbuf_full(dut):
    """L0: the replay buffer back-pressures the upper stream. With nothing
    sent (the phy held), six 144-byte TLPs (150 bytes each framed: a 4-DW
    header and 128 bytes of payload) fit and a
    seventh does not; a TLP that would bring the 1024-byte buffer to 1026
    bytes is refused and one that brings it to 1022 is taken; once sent, an
    Ack frees room and the refused TLP is taken. An Ack that removes the TLP
    being replayed does not free its space before that TLP's eop."""
    env = Env(dut, cocotb.RANDOM_SEED)
    await env.start()
    await active_by_hand(env)
    env.l0 = False                        # the phy takes nothing: no TLP is sent
    big = [make_tlp("MWr64", 32, i) for i in range(7)]
    env.send(*big)
    await env.run(400)
    assert len(env.accepted) == 6, f"{len(env.accepted)} 150-byte TLPs taken, expected 6"
    assert sum(framed(t) for t in env.accepted) == 900
    COV_RBUF.sample(backpressure="full_max_tlps")
    # 900 + 126 = 1026: refused; 900 + 122 = 1022: taken.
    t126, t122 = make_tlp("MWr", 27, 0x40), make_tlp("MWr", 26, 0x41)
    assert framed(t126) == 126 and framed(t122) == 122
    env.tlp_cur = None
    env.tlp_q.clear()
    env.send(t126)
    await env.run(100)
    assert len(env.accepted) == 6, "a TLP taken beyond 1024 bytes"
    COV_RBUF.sample(backpressure="refused_by_bytes")
    env.tlp_cur = None
    env.tlp_q.clear()
    env.send(t122, t126)
    await env.run(100)
    assert len(env.accepted) == 7 and env.accepted[-1] == t122, "the 1022-byte fill refused"
    # Sent, then Acks free room: the refused TLP goes in.
    env.l0 = True
    await env.run_until(lambda: env.tlp_sent, 100, "first TLP sent")
    acked = -1
    while len(env.accepted) < 8 or len(env.tlp_sent) < 8:
        await env.run(70)
        firsts = [s for _, s, r in env.tlp_sent if not r]
        if firsts and firsts[-1] != acked:
            acked = firsts[-1]
            env.inject(ack(acked))
    COV_RBUF.sample(backpressure="freed_by_ack")
    await settle(env)
    env.inject(ack(7))
    await settle(env)
    assert [s for _, s, _ in env.tlp_sent] == list(range(8)), "TLPs sent"
    assert env.accepted == big[:6] + [t122, t126]

    # An Ack that removes the TLP being replayed: its space, and that of the
    # TLPs after it, stays in use until its eop. The LTSSM shows Recovery so
    # the timer holds, while the stand-in keeps taking packets.
    env.l0, env.hold_tx_in_recovery = False, False
    full = [make_tlp("MWr", 32, 0x60 + i) for i in range(7)]        # seq 8 to 14
    assert sum(framed(t) for t in full) == 1022
    env.send(*full)
    await settle(env)
    start = len(env.tlp_sent)
    env.inject(ack(7, nak=True))
    await env.run_until(lambda: len(env.tlp_sent) > start and env.tx_beats, 60,
                        "the replay of TLP 8 on the stream")
    assert env.tlp_sent[start][1:] == (8, True)
    env.inject(ack(8))
    small = make_tlp("MRd", 0, 0x70)
    env.send(small)
    await env.run_until(lambda: len(env.accepted) == 16, 200, "the small TLP taken")
    taken = env.cycle - 1                 # the step whose edge took its sop
    rp = next((c for c, d in env.tlp_packets if c >= env.tlp_sent[start][0]), None)
    assert rp is not None, f"TLP taken at cycle {taken}, the replay of TLP 8 still on the stream"
    replayed_eop = rp + framed(full[0]) // 2 - 1
    assert taken > replayed_eop, (
        f"TLP taken at cycle {taken}, not after the eop of the acknowledged TLP on the "
        f"stream (cycle {replayed_eop})")
    COV_RBUF.sample(backpressure="held_until_replayed_eop")
    await settle(env)
    env.inject(ack(15))
    await settle(env)
    env.l0, env.hold_tx_in_recovery = True, True
    env.check_shadow_tx()
    env.check_clean()


@cocotb.test()
async def link_down_mid_tlp(dut):
    """L0: the link goes down while a TLP is on the phy stream: its
    remaining beats go out up to eop with valid held (HAS decision on a
    started packet), the TLP being taken from the upper stream is abandoned
    and ready falls; after the next link-up sequence numbers start at 0 with
    an empty replay buffer. A TLP offered but not yet taken by the phy when
    the link goes down is withdrawn."""
    env = Env(dut, cocotb.RANDOM_SEED)
    await env.start()
    await active_by_hand(env)
    env.send(make_tlp("MWr", 32, 1), make_tlp("MWr", 32, 2))
    await env.run_until(lambda: env.tx_tlp_take is not None and len(env.tx_beats) == 20, 200,
                        "a TLP 20 beats into the phy stream")
    assert env.tlp_cur is not None, "the second TLP is not being taken"
    env.dut_link = False
    await env.run_until(lambda: env.tlp_packets, 80, "the cut TLP's eop")
    beats = (framed(make_tlp("MWr", 32, 1))) // 2
    assert env.tails[-1][1] == beats - 20, (
        f"beats after link-down {env.tails[-1]}, expected {beats - 20} of {beats}")
    await env.run(20)
    assert env.state == 0 and len(env.tlp_packets) == 1
    # Relink: sequence numbers from 0, nothing replayed.
    await active_by_hand(env)
    env.send(make_tlp("MRd", 0, 3))
    await settle(env)
    assert env.tlp_packets[-1][1][:2] == bytes(2), "first TLP after the relink not sequence 0"
    assert [(s, r) for _, s, r in env.tlp_sent] == [(0, False), (0, False)]
    env.inject(ack(0))
    await settle(env)
    # Withdrawn: the phy holds ready low (Recovery) while a TLP is offered.
    env.l0 = False
    env.send(make_tlp("MRd", 0, 4))
    await env.run_until(lambda: int(dut.pkt_tx_valid_o.value) and not int(dut.pkt_tx_dllp_o.value),
                        100, "a TLP offered")
    env.dut_link = False
    await env.run(40)
    assert len(env.tlp_packets) == 2 and not env.in_pkt, "the offered TLP was sent"
    env.l0 = True
    await active_by_hand(env)
    env.send(make_tlp("MRd", 0, 5))
    await settle(env)
    assert env.tlp_packets[-1][1][:2] == bytes(2) and len(env.tlp_packets) == 3
    env.check_clean()


async def deliver(env: Env, partner: ptn.Partner, tlps: list[bytes], *, hooks=None) -> int:
    """Send ``tlps`` and run until the partner has delivered them all and
    acknowledged the last; ``hooks(env, partner)`` runs every 200 cycles.
    Returns the cycles taken."""
    start = env.cycle
    env.send(*tlps)
    limit = 200 * len(tlps) + 20000
    while len(partner.received) < len(tlps) or env.m_ackd != (len(tlps) - 1) % SEQ_MOD:
        await env.run(200)
        if hooks is not None:
            hooks(env, partner)
        assert env.cycle - start < limit, (
            f"{len(partner.received)} of {len(tlps)} TLPs delivered in {limit} cycles")
    await settle(env)
    return env.cycle - start


@cocotb.test()
async def tlp_partner(dut):
    """L1, seeded: random TLPs of every shape to the independent partner,
    with upper-stream gaps, phy stalls and partner gaps, error-free: every
    TLP delivered exactly once and in order, none replayed, no timeout; the
    replay events equal the arch shadow's."""
    seed = cocotb.RANDOM_SEED
    rng = random.Random(seed)
    env, partner = await partner_env(dut, seed, ready_p=0.8, gap_p=0.2, tlp_valid_p=0.7)
    tlps = [rand_tlp(rng, i) for i in range(300)]
    took = await deliver(env, partner, tlps)
    env.check_tlp_delivered(partner, tlps)
    assert env.err_counts[ERR_TIMEOUT] == 0 and env.replay_runs == 0
    assert not any(r for _, _, r in env.tlp_sent), "a TLP was replayed"
    pairs = env.check_shadow_tx()
    dut._log.info("seed %d: %d TLPs in %d cycles; DUT vs arch shadow %s; partner %s", seed,
                  len(tlps), took, pairs, partner.stats)
    env.check_clean()


@cocotb.test()
async def tlp_partner_errors(dut):
    """L1/L2, seeded: the same with the partner's fault hooks: good TLPs
    Nak'd, TLPs lost on the line, Acks and Naks lost, corrupted and flagged,
    forced Naks, and Ack-delay windows past the replay timeout (starting
    every fifth 200-cycle period, the rest at random; longer at a
    REPLAY_TIMEOUT above 356). A Nak in a window restarts the timer, so the
    run ends with an Ack-loss window: every partner DLLP dropped while one
    more TLP goes out, and the timer must expire. Every TLP delivered
    exactly once and in order; each replay from the oldest unacknowledged
    TLP, in order and complete, against the Acks and Naks the DUT received;
    no DL protocol error. The arch shadow's replay counts are logged: with
    errors they depend on each model's own transmit timing (DV plan,
    cross-check)."""
    seed = cocotb.RANDOM_SEED
    rng = random.Random(seed)
    inject = ptn.Inject(rx_tlp_nak=0.04, rx_tlp_drop=0.03, tx_dllp_drop=0.08,
                        tx_dllp_corrupt=0.03, tx_dllp_flag=0.02)
    env, partner = await partner_env(dut, seed, ready_p=0.8, gap_p=0.2, tlp_valid_p=0.7,
                                     inject=inject, retrain_cycles=200)
    tlps = [rand_tlp(rng, i) for i in range(300)]
    period = [0]
    # An Ack-delay window: one 200-cycle period at the default timeout, one
    # more per 200 cycles that REPLAY_TIMEOUT adds, so a window outlasts the
    # timer whatever the parameter; the delay itself is the timeout plus 94.
    span = 1 + -(-(env.replay_timeout - REPLAY_TIMEOUT) // 200)
    left = [0]

    def hooks(env: Env, p: ptn.Partner) -> None:
        period[0] += 1
        r = rng.random()
        if r < 0.15:
            p.force_nak()
        if period[0] % 5 == 0 or 0.15 <= r < 0.25:
            left[0] = span
        p.ack_delay = env.replay_timeout + 94 if left[0] else 0
        left[0] = max(0, left[0] - 1)

    took = await deliver(env, partner, tlps, hooks=hooks)
    # The Ack-loss window (#401): no Ack or Nak reaches the DUT, so the
    # timer expires REPLAY_TIMEOUT cycles after the extra TLP's eop.
    timeouts, start, drop = env.err_counts[ERR_TIMEOUT], env.cycle, partner.inject.tx_dllp_drop
    partner.ack_delay = 0
    partner.inject.tx_dllp_drop = 1.0
    extra = rand_tlp(rng, len(tlps))
    env.send(extra)
    await env.run_until(lambda: env.err_counts[ERR_TIMEOUT] > timeouts,
                        env.replay_timeout + 400, "a replay timeout with every Ack lost")
    partner.inject.tx_dllp_drop = drop
    tlps.append(extra)
    while len(partner.received) < len(tlps) or env.m_ackd != (len(tlps) - 1) % SEQ_MOD:
        await env.run(200)
        assert env.cycle - start < 20000, (
            f"{len(partner.received)} of {len(tlps)} TLPs delivered after the Ack-loss window")
    await settle(env)
    env.check_tlp_delivered(partner, tlps)
    replays = sum(r for _, _, r in env.tlp_sent)
    assert env.replay_runs and env.err_counts[ERR_TIMEOUT], (
        f"{env.replay_runs} replays, {env.err_counts[ERR_TIMEOUT]} timeouts")
    dut._log.info("seed %d: %d TLPs in %d cycles, %d replays (%d TLPs), %d timeouts, "
                  "%d rollovers; partner %s", seed, len(tlps), took, env.replay_runs, replays,
                  env.err_counts[ERR_TIMEOUT], env.err_counts[ERR_ROLLOVER], partner.stats)
    dut._log.info("DUT vs arch shadow (logged, not compared) %s", env.check_shadow_tx())
    env.xcheck.clear()
    env.check_clean()


@cocotb.test()
async def tlp_rollover(dut):
    """L1, seeded: the HAS rollover target. TLPs flow to the partner; from a
    set cycle every TLP the DUT starts is lost until the retrain the DUT
    requests: exactly 4 replay timeouts, 1 rollover, 1 retrain, at most 4
    replays, no Bad TLP at the partner, then every TLP delivered once and in
    order. The arch shadow counts the same events, and the architecture
    model run against a fresh partner in the same scenario does too."""
    seed = cocotb.RANDOM_SEED
    rng = random.Random(seed)
    env, partner = await partner_env(dut, seed, retrain_cycles=500)
    tlps = [make_tlp("MWr", rng.randint(1, 32), i) for i in range(150)]
    drop_at = 1000 + 100 * (seed % 4)
    env.send(*tlps)
    t0 = env.cycle
    await env.run(drop_at)
    env.drop_tx_tlps = True
    while len(partner.received) < len(tlps) or env.m_ackd != len(tlps) - 1:
        await env.run(200)
        assert env.cycle - t0 < 60000, f"{len(partner.received)} TLPs delivered"
    await settle(env)
    env.check_tlp_delivered(partner, tlps)
    got = {"timeouts": env.err_counts[ERR_TIMEOUT], "rollovers": env.err_counts[ERR_ROLLOVER],
           "retrains": len(env.retrains), "replays": env.replay_runs,
           "bad_tlp": partner.stats.get("bad_tlp", 0)}
    dut._log.info("seed %d: loss from cycle %d, %d TLPs lost; %s", seed, drop_at,
                  env.lost_tx_tlps, got)
    assert got["timeouts"] == 4 and got["rollovers"] == 1 and got["retrains"] == 1, got
    assert got["replays"] <= 4 and got["bad_tlp"] == 0 and env.lost_tx_tlps, got
    pairs = env.check_shadow_tx()
    assert got["replays"] == 4 and pairs["replays"][1] == 4, pairs
    fresh = ptn.Partner("fresh", credits=INFINITE, seed=seed)
    model, cycles = arch_partner_send(tlps, fresh, drop_from=drop_at, retrain_cycles=500,
                                      replay_timeout=env.replay_timeout)
    arch_got = {"timeouts": model.errors[arch.E_TIMEOUT],
                "rollovers": model.errors[arch.E_ROLLOVER],
                "retrains": model.counts["retrains"], "bad_tlp": fresh.stats.get("bad_tlp", 0)}
    dut._log.info("DUT vs arch shadow %s; arch model vs partner %s in %d cycles", pairs,
                  arch_got, cycles)
    assert arch_got == {"timeouts": 4, "rollovers": 1, "retrains": 1, "bad_tlp": 0}, arch_got
    assert fresh.received == tlps and not fresh.violations, "arch model vs partner delivery"
    env.check_clean()


async def line_use(dut, ack_delay: int) -> None:
    """HAS targets mwr_max and ack_spec_max: 500 128-byte memory writes one
    way with the partner's Acks at 32 + ``ack_delay`` cycles; TLPs occupy at
    least 99% of the phy stream's cycles (each TLP's beats and its framing
    cycle) from the first TLP taken to the last delivered."""
    seed = cocotb.RANDOM_SEED
    env, partner = await partner_env(dut, seed)
    partner.ack_delay = ack_delay
    tlps = [arch.make_tlp("mwr", 128, i) for i in range(500)]
    env.send(*tlps)
    await env.run_until(lambda: env.accepted, 50, "first TLP taken")
    start, beats0, pkts0 = env.cycle, env.tx_tlp_beats, len(env.tlp_packets)
    while len(partner.received) < len(tlps):
        await env.step()
        assert env.cycle - start < 60000, "line use run too long"
    # Each TLP's beats the phy took, and its framing cycle.
    busy = env.tx_tlp_beats - beats0 + len(env.tlp_packets) - pkts0
    span = env.cycle - start
    await env.run_until(lambda: env.m_ackd == len(tlps) - 1, 2000, "the last TLP acknowledged")
    await settle(env)
    env.check_tlp_delivered(partner, tlps)
    use = busy / span
    dut._log.info("Ack latency %d: TLPs on the line %d of %d cycles, %.2f%%; replays %d",
                  32 + ack_delay, busy, span, 100 * use, env.replay_runs)
    assert use >= 0.99 and env.replay_runs == 0, f"line use {use:.4f}, target 0.99"
    env.check_clean()


@cocotb.test()
async def mwr_max(dut):
    """L1: line use with the partner's default Ack timing."""
    await line_use(dut, 0)


@cocotb.test()
async def ack_spec_max(dut):
    """L1: line use with the partner's Acks at the spec's latest, 118 cycles:
    the 1024-byte replay buffer never stalls the line."""
    await line_use(dut, 118 - 32)


@cocotb.test()
async def seq_wrap(dut):
    """L2, seeded: 4200 small TLPs to the partner, so sequence numbers wrap
    from 4095 to 0, with Naks, lost TLPs and lost Acks around the wrap:
    every TLP delivered once and in order, replays in order across the
    wrap."""
    seed = cocotb.RANDOM_SEED
    rng = random.Random(seed)
    env, partner = await partner_env(dut, seed, ready_p=0.9)
    tlps = [make_tlp(rng.choice(("MRd", "Msg", "CfgWr")), 1, i) for i in range(4200)]
    armed = [False]

    def hooks(env: Env, p: ptn.Partner) -> None:
        if not armed[0] and len(p.received) > 4080:
            armed[0] = True
            p.inject.rx_tlp_nak_next = 2
            p.inject.rx_tlp_drop = 0.05
            p.inject.tx_dllp_drop = 0.1
        elif armed[0] and len(p.received) > 4120:
            p.inject.rx_tlp_drop = p.inject.tx_dllp_drop = 0.0

    await deliver(env, partner, tlps, hooks=hooks)
    env.check_tlp_delivered(partner, tlps)
    wrapped = [r for _, s, r in env.tlp_sent if s == 0]
    assert len(wrapped) >= 2 and env.replay_runs, f"wrap {wrapped}, {env.replay_runs} replays"
    dut._log.info("seed %d: %d TLPs, %d replays, %d timeouts", seed, len(tlps),
                  env.replay_runs, env.err_counts[ERR_TIMEOUT])
    env.check_clean()


@cocotb.test()
async def tlp_bidir(dut):
    """L1, seeded: both directions loaded. The DUT sends 300 random TLPs
    (many of maximum size) to the partner while the partner sends it 300
    within the DUT's credits: each side delivers the other's TLPs once and
    in order, and every TLP the DUT receives is covered by an Ack or Nak
    that starts within the HAS's 118 cycles of its eop, though the DUT's
    Acks wait behind its own TLPs on the stream (RETRY.md, transmit
    priority)."""
    from tests.test_dll_rx import traffic as rx_traffic_tlps
    seed = cocotb.RANDOM_SEED
    rng = random.Random(seed)
    env, partner = await partner_env(dut, seed)
    to_dut = rx_traffic_tlps(rng, env.adv, 300)
    to_partner = [make_tlp(rng.choice(("MWr", "MWr64", "CplD")), rng.choice((32, 32, 16, 1)),
                           i) for i in range(300)]
    for t in to_dut:
        partner.send(t)
    took = await deliver(env, partner, to_partner)
    await env.run_until(lambda: len(env.delivered) == len(to_dut) and not partner.rbuf, 20000,
                        "the partner's TLPs delivered and acknowledged")
    await env.run(200)
    env.check_tlp_delivered(partner, to_partner)
    assert env.delivered == to_dut, "the upper stream does not carry the partner's TLPs"
    lat = env.check_rx(ack_bound=ACK_BOUND)
    # On an idle stream an Ack starts 35 cycles after the eop (MAS, timing):
    # later ones waited behind a packet on the stream.
    idle_ack = 35
    behind = sum(1 for eop, seq, _ in env.ref.accepts
                 if next(s for s, _, _, q in env.acknaks
                         if s > eop and (q - seq) % SEQ_MOD < 2048) - eop > idle_ack)
    assert behind and lat["ack"] > idle_ack, "no Ack waited behind a TLP"
    assert env.replay_runs == 0 and env.err_counts[ERR_TIMEOUT] == 0
    dut._log.info("seed %d: %d TLPs each way in %d cycles; worst Ack %d cycles (HAS %d); "
                  "%d TLPs acknowledged after waiting for the stream; DUT vs arch shadow "
                  "(logged, not compared) %s", seed, len(to_partner), took, lat["ack"],
                  ACK_BOUND, behind, env.check_shadow_tx())
    # The shadow sends its own Acks at its own pace, so its TLPs lag the
    # DUT's and Acks can name TLPs it has not sent yet (DV plan, cross-check).
    env.xcheck.clear()
    env.check_shadow_rx()
    env.check_clean()


@cocotb.test()
async def rollover_edge(dut):
    """L0: a TLP sop waiting for the phy in the very cycle REPLAY_NUM rolls
    over is not offered then, though the phy is ready in that cycle: no TLP
    starts ahead of the rollover replay (RETRY.md, REPLAY_NUM; the same rule
    as a link-down edge). After the 1024-cycle hold the replay sends the
    oldest TLP and then the withdrawn one."""
    env = Env(dut, cocotb.RANDOM_SEED)
    await env.start()
    await active_by_hand(env)
    first = make_tlp("MRd", 0, 1)
    env.send(first)
    # Three timeouts and replays; the third replay's eop starts the timer
    # whose expiry rolls REPLAY_NUM over.
    await env.run_until(lambda: len(env.tlp_packets) == 4, 3 * 500, "the third replay sent")
    await env.run_until(lambda: not env.in_pkt, 20, "its eop")
    sop, data = env.tlp_packets[-1]
    timer_start = sop + len(data) // 2        # the edge that took its eop
    roll_cycle = timer_start + REPLAY_TIMEOUT - 1
    env.ready_p = 0.0                          # the phy takes nothing between packets ...
    env.ready_at[roll_cycle] = 1               # ... but in the rollover cycle
    late = make_tlp("MRd", 0, 2)               # sequence 1, its sop left waiting
    env.send(late)
    await env.run_until(lambda: env.err_counts[ERR_ROLLOVER] == 1, REPLAY_TIMEOUT + 50,
                        "the rollover")
    assert env.err_cycles[ERR_ROLLOVER][0] == roll_cycle + 1, (
        f"rollover pulse at {env.err_cycles[ERR_ROLLOVER][0]}, expected {roll_cycle + 1}")
    takes = [t for t, s, r in env.tlp_sent if s == 1]
    assert takes and takes[0] <= roll_cycle, "TLP 1 was not waiting at the rollover"
    env.ready_p = 1.0
    await env.run(5)
    assert len(env.tlp_packets) == 4 and not env.in_pkt, (
        "a TLP started at the rollover edge, ahead of the replay")
    start = len(env.tlp_sent)
    await env.run_until(lambda: len(env.tlp_packets) == 6, HOLD_FALLBACK + 200,
                        "the rollover replay")
    assert [(s, r) for _, s, r in env.tlp_sent[start:]] == [(0, True), (1, True)], (
        f"after the hold: {env.tlp_sent[start:]}")
    assert [d[2:-4] for _, d in env.tlp_packets[4:]] == [first, late]
    env.inject(ack(1))
    await settle(env)
    env.check_clean()


@cocotb.test()
async def timer_edge(dut):
    """L0: the replay timer is never early (RETRY.md, timers). The LTSSM
    leaves L0 for 5 cycles starting in the very cycle the timer would reach
    356: the timeout comes only after 356 cycles in L0, 361 cycles after
    the eop that started it."""
    env = Env(dut, cocotb.RANDOM_SEED)
    await env.start()
    await active_by_hand(env)
    env.send(make_tlp("MRd", 0, 1))
    await env.run_until(lambda: env.tlp_packets and not env.in_pkt, 100, "the TLP sent")
    sop, data = env.tlp_packets[-1]
    start = sop + len(data) // 2              # the edge that took its eop
    await env.run_until(lambda: env.cycle == start + REPLAY_TIMEOUT - 1, REPLAY_TIMEOUT,
                        "the cycle the timer would reach 356")
    env.l0 = False
    await env.run(5)
    env.l0 = True
    await env.run_until(lambda: env.err_counts[ERR_TIMEOUT] == 1, 50, "the timeout")
    got = env.err_cycles[ERR_TIMEOUT][0] - start
    assert got == REPLAY_TIMEOUT + 5, f"timeout {got} cycles after the eop, expected 361"
    COV_REPLAY.sample(timer="held_outside_l0")
    await settle(env)
    env.inject(ack(0))
    await settle(env)
    env.check_clean()


# A slowed lower layer (HAS, parameters): the stand-in's line delay each
# way, so a maximum TLP's Ack is acted on about 640 cycles after its eop,
# the idle round trip of the GPIO build (link HAS, lower layer): longer
# than the default 356, shorter than a REPLAY_TIMEOUT sized for it.
SLOW_DELAY = 300


@cocotb.test()
async def replay_timeout_slow(dut):
    """L1, at a REPLAY_TIMEOUT above 356 (HAS, parameters): over a lower
    layer slowed to a round trip between 356 and REPLAY_TIMEOUT, a
    maximum-payload TLP (4-DW header, 128 bytes) is acknowledged with no
    replay timeout and no replay, though its Ack comes more than 356 cycles
    after its eop. Then every DLLP of the partner is dropped while a second
    maximum TLP goes out: the timer expires exactly REPLAY_TIMEOUT cycles
    after its eop, that TLP alone is replayed, and the partner, which
    discards the duplicate, Acks it; both TLPs delivered once, in order."""
    seed = cocotb.RANDOM_SEED
    partner = ptn.Partner("partner", credits=INFINITE, seed=seed)
    env = Env(dut, seed, partner=partner, delay=SLOW_DELAY)
    await env.start()
    limit = env.replay_timeout
    assert limit > REPLAY_TIMEOUT, (
        f"REPLAY_TIMEOUT {limit}: this test needs a value above {REPLAY_TIMEOUT}")
    env.dut_link = env.partner_link = True
    await env.run_until(lambda: env.state == ACTIVE and partner.state == ptn.ACTIVE, 4000,
                        "both ends in DL_Active")
    await env.run(30)
    tlps = [make_tlp("MWr64", 32, 0x200 + i) for i in range(2)]

    # Idle link, slow round trip: no spurious replay.
    env.send(tlps[0])
    await env.run_until(lambda: env.tlp_packets and not env.in_pkt, 200, "TLP 0 sent")
    sop, data = env.tlp_packets[-1]
    assert len(data) == 150, f"maximum TLP framed as {len(data)} bytes, expected 150"
    eop = sop + len(data) // 2                # the edge that took its eop
    await env.run_until(lambda: any(s == 0 and k == "ack" for _, k, s, _ in env.acknak_log),
                        limit, "the Ack of TLP 0")
    acked = next(c for c, k, s, _ in env.acknak_log if s == 0 and k == "ack")
    trip = acked - eop
    assert REPLAY_TIMEOUT < trip < limit, (
        f"Ack acted {trip} cycles after the eop: the slowed round trip must lie between "
        f"{REPLAY_TIMEOUT} and REPLAY_TIMEOUT {limit}")
    await env.run(limit + 50)
    assert env.err_counts[ERR_TIMEOUT] == 0 and env.replay_runs == 0, (
        f"a spurious replay: {env.err_counts[ERR_TIMEOUT]} timeouts, {env.replay_runs} "
        f"replays, round trip {trip} cycles, REPLAY_TIMEOUT {limit}")
    assert [(s, r) for _, s, r in env.tlp_sent] == [(0, False)], f"sent {env.tlp_sent}"

    # The Ack withheld: a replay exactly REPLAY_TIMEOUT cycles after the eop.
    partner.inject.tx_dllp_drop = 1.0
    env.send(tlps[1])
    await env.run_until(lambda: len(env.tlp_packets) == 2 and not env.in_pkt, 200,
                        "TLP 1 sent")
    sop, data = env.tlp_packets[-1]
    eop = sop + len(data) // 2
    await env.run_until(lambda: env.err_counts[ERR_TIMEOUT] == 1, limit + 10, "the timeout")
    got = env.err_cycles[ERR_TIMEOUT][0] - eop
    assert got == limit, f"timeout {got} cycles after the eop, expected {limit}"
    partner.inject.tx_dllp_drop = 0.0
    await env.run_until(lambda: any(s == 1 and k == "ack" for _, k, s, _ in env.acknak_log),
                        limit, "the Ack of the replayed TLP 1")
    await settle(env)
    await env.run(limit + 50)
    assert [(s, r) for _, s, r in env.tlp_sent] == [(0, False), (1, False), (1, True)], (
        f"sent {env.tlp_sent}, expected TLP 1 replayed once")
    assert env.err_counts[ERR_TIMEOUT] == 1 and env.replay_runs == 1
    env.check_tlp_delivered(partner, tlps)
    pairs = env.check_shadow_tx()
    dut._log.info("REPLAY_TIMEOUT %d: round trip %d cycles; DUT vs arch shadow %s; partner %s",
                  limit, trip, pairs, partner.stats)
    env.check_clean()
