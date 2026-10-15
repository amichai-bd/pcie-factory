"""Unit tests of the phy e2e models; no simulator needed.

Run: python3 -m pytest src/dv/phy/models
"""

import random

import pytest

import pcie_phy_stack_model as m
from pcie_phy_channel_model import Channel
from pcie_phy_pcs_model import PcsModel, code8b10b, mac_arch


def test_channel_delays_words():
    ch = Channel(random.Random(0), delay_words=3)
    words = [0x12345, 0xABCDE, 0x0F0F0, 0x55555, 0xAAAAA]
    out = [ch.step(w, False) for w in words]
    assert [w for w, _ in out[3:]] == words[:2]
    assert [ei for _, ei in out] == [True, True, True, False, False]


@pytest.mark.parametrize("direction", [1, -1])
def test_channel_slip_shifts_the_bit_stream(direction):
    """A slip in word 1 repeats or drops one bit: from word 2 on, the
    received stream is the sent one moved by that bit."""
    ch = Channel(random.Random(1), delay_words=2)
    rng = random.Random(2)
    words = [rng.getrandbits(20) for _ in range(12)]
    out = [ch.step(words[0], False)[0]]
    ch.slip(direction)
    out += [ch.step(w, False)[0] for w in words[1:]]
    sent = sum(w << (20 * i) for i, w in enumerate(words))
    got = sum(w << (20 * i) for i, w in enumerate(out))
    fill = 2 * 20                                   # the delay words
    mask = (1 << (20 * 6)) - 1
    assert (got >> (fill + 40 + direction)) & mask == (sent >> 40) & mask
    assert ch.log == [("slip", 2)]


def test_pcs_encodes_with_running_disparity_and_aligns():
    """A pcs transmitting a TS1 stream to another pcs: the receiver locks
    and delivers the same symbols."""
    tx, rx = PcsModel(), PcsModel()
    ts1 = [(1, code8b10b.K_COM), (1, code8b10b.K_PAD), (1, code8b10b.K_PAD), (0, 0x20),
           (0, 0x02), (0, 0)] + [(0, 0x4A)] * 10
    stream = ts1 * 8
    words = []
    for cyc in range(40):
        rst = cyc < 2
        if cyc < 20:   # out of reset, PowerDown P0 acknowledged
            pipe = mac_arch.PipeTx(power_down=mac_arch.P0, elec_idle=True)
        else:
            i = 2 * (cyc - 20) % len(stream)
            pipe = mac_arch.PipeTx(syms=(stream[i], stream[i + 1]), elec_idle=False,
                                   power_down=mac_arch.P0)
        words.append(tx.core_edge(rst, pipe, True))
    got = []
    for cyc, (w, ei) in enumerate(words):
        rx.rx_edge(cyc < 2, w, ei)
        r = rx._read(False) if rx.lock else None
        if r is not None and r.valid:
            got += list(r.syms)
    assert rx.stats["locks"] == 1
    assert got, "the receiver delivered nothing"
    start = got.index((1, code8b10b.K_COM))
    assert got[start:start + 16] == ts1


@pytest.mark.parametrize("a_role,ppm", [("downstream", 600), ("upstream", -600)])
def test_pair_trains_and_passes_packets(a_role, ppm):
    rng = random.Random(3)
    b = m.Bench([m.ModelPort(a_role), m.ModelPort(m.other(a_role))], rng)
    b.run(lambda x: x.both_l0(), 40_000, ppm)
    pkts = mac_arch.make_packets(rng, 20, "random", 0, 0.2)
    for p in pkts:
        for port in b.ports:
            port.send(p.dllp, p.data, p.nullify)
    got = [[], []]

    def done(x):
        for i in (0, 1):
            got[i] += x.ports[i].received()
        return all(len(g) >= len(pkts) for g in got)

    b.run(done, 40_000, ppm)
    want = [(p.dllp, p.data, p.nullify, False) for p in pkts]
    assert got == [want, want]
    assert all(p.rcv_errors == 0 and p.pcs.stats["overflow"] == 0 for p in b.ports)


def test_line_dropout_takes_the_receiver_to_recovery():
    rng = random.Random(4)
    b = m.Bench([m.ModelPort("downstream"), m.ModelPort("upstream")], rng)
    b.run(lambda x: x.both_l0(), 40_000, 300)
    codes = []
    b.hooks.append(lambda x, i: codes.append(x.ports[1].code) if i == 1 else None)
    b.channels[0].idle(48)
    b.run(lambda x: any(c in m.RECOVERY_CODES for c in codes), 5_000, 300)
    b.run(lambda x: x.both_l0(), 20_000, 300)
