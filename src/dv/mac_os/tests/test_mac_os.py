"""pcie_mac_os tests (wiki/src/dv/mac_os/DV_PLAN.md).

Every cycle the inputs are driven at the falling edge of clk_i, the
combinational outputs are read 1 ns later and compared with the cycle model
(models/pcie_mac_os_model.py, written from the MAS), and the model then
steps. Round trips loop the transmit word back into the receive port
through a symbol line, at receive phase 0 (COM in [7:0]) or 1 (COM in
[15:8]).
"""

import random
from collections import deque

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, Timer

from models import pcie_mac_os_model as m
from regress.funcov import Covergroup

IDLE = (0, 0x00)
KINDS = m.KINDS

# ------------------------------------------------------------- coverage

TX_COV = Covergroup("mac_os_tx")
TX_COV.coverpoint("kind", bins={"ts1": "ts1", "ts2": "ts2", "skp_req": "skp_req",
                                "skp_sched": "skp_sched", "eios": "eios", "fts": "fts",
                                "compliance": "compliance"})
_NUM_BINS = {"pad": "pad", "zero": 0, "mid": range(1, 255), "max": 255}
TX_COV.coverpoint("link", bins=dict(_NUM_BINS))
TX_COV.coverpoint("lane", bins=dict(_NUM_BINS))
TX_COV.coverpoint("ctl", bins=list(range(8)))
TX_COV.coverpoint("n_fts", bins={"min": 1, "default": 32, "max": 255})
TX_COV.coverpoint("ts_kind", bins={"ts1": "ts1", "ts2": "ts2"})
TX_COV.cross("ts_kind_x_ctl", "ts_kind", "ctl")
# A scheduled SKP that leaves another one due: SKP back to back after a
# long stretch without a boundary.
TX_COV.coverpoint("skp_backlog", bins={"single": "single", "back_to_back": "back_to_back"})

RX_COV = Covergroup("mac_os_rx")
RX_COV.coverpoint("event", bins={e: e for e in
                                 ("ts1", "ts2", "ts1_inv", "ts2_inv", "skp", "eios", "fts")})
RX_COV.coverpoint("phase", bins={"com_lo": 0, "com_hi": 1})
RX_COV.cross("event_x_phase", "event", "phase")
RX_COV.coverpoint("bad", bins={r: r for r in ("cut_by_com", "id_mismatch", "id_k", "id_unknown")})
_FIELD_BINS = {"pad": "pad", "num": "num", "other_k": "other_k"}
RX_COV.coverpoint("link", bins=dict(_FIELD_BINS))
RX_COV.coverpoint("lane", bins=dict(_FIELD_BINS))
RX_COV.coverpoint("link_num", bins={"zero": 0, "mid": range(1, 255), "max": 255})
RX_COV.coverpoint("n_fts", bins={"zero": 0, "mid": range(1, 255), "max": 255})
RX_COV.coverpoint("ctl", bins={
    "none": 0,
    "hot_reset": lambda v: bool(v & 1), "disable": lambda v: bool(v & 2),
    "loopback": lambda v: bool(v & 4), "disable_scrambling": lambda v: bool(v & 8),
    "compliance_receive": lambda v: bool(v & 16)})


def _field_kind(f):
    return "pad" if f["pad"] else ("num" if f["num"] else "other_k")


def sample_tx(exp, fields, n_fts, credit):
    if not exp["start"]:
        return
    kind = exp["kind"]
    if kind == "ts":
        name = "ts2" if fields["ts2"] else "ts1"
        TX_COV.sample(kind=name, ts_kind=name,
                      link="pad" if fields["link"] is None else fields["link"],
                      lane="pad" if fields["lane"] is None else fields["lane"],
                      ctl=fields["ctl"], n_fts=n_fts)
    elif kind == "skp":
        if exp["ack"]:
            TX_COV.sample(kind="skp_req")
        else:
            TX_COV.sample(kind="skp_sched", skp_backlog=(
                "back_to_back" if credit >= m.SKP_INTERVAL else "single"))
    else:
        TX_COV.sample(kind=kind)


def sample_rx(ev):
    if ev["ts_bad"]:
        RX_COV.sample(bad=ev["bad_reason"])
    if ev["slot"] is None:
        return
    phase = ev["slot"] ^ 1  # the COM is an odd number of symbols earlier
    if ev["ts"]:
        f = ev["fields"]
        name = ("ts2" if f["ts2"] else "ts1") + ("_inv" if f["inv"] else "")
        kw = dict(event=name, phase=phase, link=_field_kind(f["link"]),
                  lane=_field_kind(f["lane"]), n_fts=f["n_fts"], ctl=f["ctl"])
        if f["link"]["num"]:
            kw["link_num"] = f["link"]["byte"]
        RX_COV.sample(**kw)
    else:
        name = next(k for k in ("skp", "eios", "fts") if ev[k])
        RX_COV.sample(event=name, phase=phase)


# ---------------------------------------------------------------- bench

def word_bits(word):
    (k0, b0), (k1, b1) = word
    return (b1 << 8) | b0, (k1 << 1) | k0


class Line:
    """Symbols in flight from the transmit word back to the receive port."""

    def __init__(self, phase=0):
        self.q = deque([IDLE] * phase)

    def push(self, syms):
        self.q.extend(syms)

    def pop_word(self):
        return tuple(self.q.popleft() if self.q else IDLE for _ in range(2))


class Bench:
    def __init__(self, dut):
        self.dut = dut
        self.n_fts = int(dut.N_FTS.value)
        self.tx = m.Tx(self.n_fts)
        self.rx = m.Rx()
        self.cycle = 0
        self.counts = {"rx_" + k: 0 for k in ("ts", "ts_bad", "skp", "eios", "fts")}
        self.counts.update({"tx_" + k: 0 for k in KINDS})

    async def start(self):
        dut = self.dut
        cocotb.start_soon(Clock(dut.clk_i, 8, unit="ns").start())
        dut.rst_i.value = 1
        for name in ("tx_ts_req_i", "tx_skp_req_i", "tx_eios_req_i", "tx_fts_req_i",
                     "tx_compliance_req_i", "tx_ts2_i", "tx_link_pad_i", "tx_link_i",
                     "tx_lane_pad_i", "tx_lane_i", "tx_ctl_i", "tx_skp_ok_i", "tx_count_i",
                     "rx_data_i", "rx_datak_i", "rx_valid_i", "rx_elec_idle_i"):
            getattr(dut, name).value = 0
        for _ in range(3):
            await FallingEdge(dut.clk_i)
        dut.rst_i.value = 0

    async def reset(self, cycles=2):
        self.dut.rst_i.value = 1
        for _ in range(cycles):
            await FallingEdge(self.dut.clk_i)
        self.dut.rst_i.value = 0
        self.tx.reset()
        self.rx.reset()

    async def step(self, req=None, fields=None, skp_ok=False, count=False,
                   rx_word=(IDLE, IDLE), rx_valid=True, rx_ei=False, rng=None):
        """Drive one cycle, check every output against the model, step it."""
        dut = self.dut
        fields = fields or {"ts2": False, "link": None, "lane": None, "ctl": 0}
        junk = rng.randrange(256) if rng else 0xA5
        for kind in KINDS:
            getattr(dut, f"tx_{kind}_req_i").value = int(req == kind)
        dut.tx_ts2_i.value = int(fields["ts2"])
        dut.tx_link_pad_i.value = int(fields["link"] is None)
        dut.tx_link_i.value = junk if fields["link"] is None else fields["link"]
        dut.tx_lane_pad_i.value = int(fields["lane"] is None)
        dut.tx_lane_i.value = junk if fields["lane"] is None else fields["lane"]
        dut.tx_ctl_i.value = fields["ctl"]
        dut.tx_skp_ok_i.value = int(skp_ok)
        dut.tx_count_i.value = int(count)
        data, datak = word_bits(rx_word)
        dut.rx_data_i.value = data
        dut.rx_datak_i.value = datak
        dut.rx_valid_i.value = int(rx_valid)
        dut.rx_elec_idle_i.value = int(rx_ei)
        await Timer(1, unit="ns")
        exp = self.tx.step(req, fields, skp_ok, count)
        ev = self.rx.step(rx_word, rx_valid, rx_ei)
        self._check_tx(exp, req)
        self._check_rx(ev, rx_word)
        sample_tx(exp, fields, self.n_fts, self.tx.credit)
        sample_rx(ev)
        if exp["start"]:
            self.counts["tx_" + exp["kind"]] += 1
        for k in ("ts", "ts_bad", "skp", "eios", "fts"):
            self.counts["rx_" + k] += int(ev[k])
        self.cycle += 1
        await FallingEdge(dut.clk_i)
        return exp, ev

    def _cmp(self, what, got, want, ctx):
        assert got == want, (f"cycle {self.cycle}: {what}: expected {want!r} got {got!r} ({ctx})")

    def _check_tx(self, exp, req):
        dut = self.dut
        ctx = f"request {req}, model kind {exp['kind']}"
        data, datak = word_bits(exp["word"])
        self._cmp("tx_valid_o", int(dut.tx_valid_o.value), int(exp["valid"]), ctx)
        self._cmp("tx_data_o", hex(int(dut.tx_data_o.value)), hex(data), ctx)
        self._cmp("tx_datak_o", int(dut.tx_datak_o.value), datak, ctx)
        self._cmp("tx_ack_o", int(dut.tx_ack_o.value), int(exp["ack"]), ctx)
        self._cmp("tx_last_o", int(dut.tx_last_o.value), int(exp["last"]), ctx)
        self._cmp("tx_compliance_o", int(dut.tx_compliance_o.value), int(exp["compliance"]), ctx)
        self._cmp("tx_skp_due_o", int(dut.tx_skp_due_o.value), int(exp["skp_due"]), ctx)

    def _check_rx(self, ev, word):
        dut = self.dut
        ctx = f"rx word {word}"
        os_sym = int(ev["os_sym"][0]) | (int(ev["os_sym"][1]) << 1)
        self._cmp("rx_os_sym_o", int(dut.rx_os_sym_o.value), os_sym, ctx)
        for k in ("ts", "ts_bad", "skp", "eios", "fts"):
            self._cmp(f"rx_{k}_o", int(getattr(dut, f"rx_{k}_o").value), int(ev[k]), ctx)
        f = ev["fields"]
        if f is None:
            return
        want = {"rx_ts2_o": int(f["ts2"]), "rx_ts_inv_o": int(f["inv"]),
                "rx_n_fts_o": f["n_fts"], "rx_ctl_o": f["ctl"]}
        for side in ("link", "lane"):
            want[f"rx_{side}_pad_o"] = int(f[side]["pad"])
            want[f"rx_{side}_num_o"] = int(f[side]["num"])
            want[f"rx_{side}_o"] = f[side]["byte"]
        for name, v in want.items():
            self._cmp(name, int(getattr(dut, name).value), v, ctx)

    async def loop(self, line, req=None, fields=None, skp_ok=False, count=False,
                   rx_valid=True, rx_ei=False, rng=None):
        """One cycle with the receive word taken from ``line`` and the
        transmit word (or idle when the block does not drive) pushed to it."""
        exp, ev = await self.step(req, fields, skp_ok, count, line.pop_word(),
                                  rx_valid, rx_ei, rng)
        line.push(exp["word"] if exp["valid"] else (IDLE, IDLE))
        return exp, ev


def rand_fields(rng):
    return {"ts2": rng.random() < 0.5,
            "link": None if rng.random() < 0.2 else rng.randrange(256),
            "lane": None if rng.random() < 0.2 else rng.randrange(256),
            "ctl": rng.randrange(8)}


async def send_one(bench, line, kind, fields=None):
    """Request one set and hold the request until its ack; return the
    events seen while it and the line drain."""
    evs = []
    while True:
        exp, ev = await bench.loop(line, req=kind, fields=fields)
        evs.append(ev)
        if exp["ack"]:
            break
    for _ in range(12):
        _, ev = await bench.loop(line)
        evs.append(ev)
    return evs


# ---------------------------------------------------------------- tests

@cocotb.test()
async def smoke(dut):
    """L0: each ordered-set type sent and detected at both receive phases,
    with the decoded TS fields; a requested set leaves no gap."""
    bench = Bench(dut)
    await bench.start()
    fields = {"ts2": True, "link": 7, "lane": None, "ctl": 5}
    for phase in (0, 1):
        for kind in KINDS:
            line = Line(phase)
            evs = await send_one(bench, line, kind, fields)
            if kind == "compliance":
                # Not decoded: COM D21.5 starts a TS that the next COM cuts.
                assert not any(e["ts"] or e["skp"] or e["eios"] or e["fts"]
                               for e in evs), "compliance pattern decoded as a set"
                assert any(e["ts_bad"] for e in evs), "compliance unit not seen as a cut TS"
                continue
            key = kind
            hits = [e for e in evs if e[key]]
            assert len(hits) == 1, f"{kind} at phase {phase}: {len(hits)} detections"
            if kind == "ts":
                f = hits[0]["fields"]
                assert (f["ts2"], f["link"]["byte"], f["lane"]["pad"], f["ctl"],
                        f["n_fts"]) == (True, 7, True, 5, bench.n_fts), f
        await bench.reset()
    # Back to back: a held request sends a TS every 8 cycles.
    acks = []
    for i in range(40):
        exp, _ = await bench.step(req="ts", fields=fields)
        if exp["ack"]:
            acks.append(i)
    assert [b - a for a, b in zip(acks, acks[1:])] == [8] * (len(acks) - 1), acks


@cocotb.test()
async def ts_fields(dut):
    """L1: every TS field value round trips (link and lane 0 to 255 and
    PAD, every transmit control bit set), at both phases; every received
    N_FTS and control byte, and link/lane K symbols, decode."""
    rng = random.Random(cocotb.RANDOM_SEED)
    dut._log.info("random seed %d", cocotb.RANDOM_SEED)
    bench = Bench(dut)
    await bench.start()
    seen = 0
    for phase in (0, 1):
        line = Line(phase)
        values = [None] + list(range(256))
        lanes = list(values)
        rng.shuffle(lanes)
        for i, link in enumerate(values):
            fields = {"ts2": bool(i & 1), "link": link, "lane": lanes[i], "ctl": i & 7}
            evs = await send_one(bench, line, "ts", fields)
            hits = [e["fields"] for e in evs if e["ts"]]
            assert len(hits) == 1, (fields, len(hits))
            f = hits[0]
            assert f["link"]["pad"] == (link is None) and (link is None or f["link"]["byte"] == link)
            seen += 1
        await bench.reset()
    # Receive side: TS built by the reference with any N_FTS and control
    # byte, and K symbols other than PAD in the number fields.
    line = Line(0)
    for n_fts in range(256):
        for k_link in (False, True):
            syms = m.ts_symbols(1, rng.random() < 0.5, rng.randrange(256), 0, 0)
            syms[3] = (0, n_fts)
            syms[5] = (0, rng.randrange(256))
            if k_link:
                syms[1] = (1, rng.choice([0x1C, 0x3C, 0xFB, 0x5C, 0xFD]))
                syms[2] = (1, rng.choice([0x7C, 0xFE]))
            line.push(syms + [IDLE] * rng.randrange(2))
            for _ in range(9):
                _, ev = await bench.step(rx_word=line.pop_word())
                if ev["ts"]:
                    seen += 1
    dut._log.info("TS decoded: %d", seen)
    assert seen >= 2 * 257 + 400


@cocotb.test()
async def malformed(dut):
    """L1: malformed and partial sets are rejected at both phases: each
    identifier symbol changed (other data, K), a K or unknown identifier, a
    TS cut by a COM after 1 to 15 symbols, and noise after COM; ordered-set
    bodies end on the first other symbol."""
    rng = random.Random(cocotb.RANDOM_SEED)
    bench = Bench(dut)
    await bench.start()
    good = m.ts_symbols(32, False, 0, 0, 0)
    cases = []
    for i in range(6, 16):
        for sym in ((0, 0x4B), (1, 0x4A), (0, 0x45)):
            s = list(good)
            s[i] = sym
            cases.append(("id_mismatch", s))
    for ident in ((1, 0x4A), (0, 0x00), (0, 0x4B), (1, 0xF7)):
        cases.append(("id", good[:6] + [ident] * 10))
    for n in range(2, 16):
        cases.append(("cut", good[:n] + [m.COM, m.SKP]))
    for phase in (0, 1):
        for name, syms in cases:
            line = Line(phase)
            line.push(syms + [IDLE] * 4)
            evs = []
            for _ in range(len(syms) // 2 + 4):
                _, ev = await bench.step(rx_word=line.pop_word())
                evs.append(ev)
            assert not any(e["ts"] for e in evs), f"{name} decoded: {syms}"
            assert sum(e["ts_bad"] for e in evs) == 1, f"{name}: no ts_bad: {syms}"
            if name == "cut":
                assert sum(e["skp"] for e in evs) == 1, "SKP after the cut not seen"
            await bench.reset(1)
    # Bodies: COM SKP SKP STP ... the STP is not an ordered-set symbol.
    for body in ([m.COM, m.SKP, m.SKP, m.SKP, m.SKP, (1, 0xFB)],
                 [m.COM, m.IDL, m.FTS, (0, 0x00)], [m.COM, m.FTS, (1, 0xFD)]):
        line = Line(rng.randrange(2))
        line.push(body + [IDLE] * 3)
        for _ in range(5):
            await bench.step(rx_word=line.pop_word())
    # A TS across receive gaps (valid low, electrical idle) still decodes.
    line = Line(1)
    line.push(good + [IDLE] * 2)
    got = 0
    for i in range(16):
        gap = i in (3, 4, 6)
        word = line.pop_word() if not gap else ((1, 0xBC), (1, 0xBC))
        _, ev = await bench.step(rx_word=word, rx_valid=not (gap and i != 6), rx_ei=(i == 6))
        got += ev["ts"]
    assert got == 1, "TS across gaps not decoded"
    dut._log.info("counts %s", bench.counts)


async def random_run(dut, cycles):
    rng = random.Random(cocotb.RANDOM_SEED)
    dut._log.info("random seed %d, %d cycles", cocotb.RANDOM_SEED, cycles)
    bench = Bench(dut)
    await bench.start()
    line = Line(0)
    req, fields, hold, skp_block = None, rand_fields(rng), 0, 0
    noise = [m.COM, m.SKP, m.IDL, m.FTS, m.PAD, (1, 0xFB), (1, 0xFD), (0, 0x4A),
             (0, 0x45), (0, 0xB5), (0, 0xBA), IDLE, (0, 0x02)]
    for _ in range(cycles):
        if hold == 0:
            # A requested SKP restarts the SKP count, so it stays rare (one
            # per L0s exit) to let the schedule run its full interval.
            r = rng.random()
            req = (None if r < 0.3 else "ts" if r < 0.75 else "skp" if r < 0.76
                   else rng.choice(["eios", "fts", "compliance"]))
            fields, hold = rand_fields(rng), rng.randrange(1, 40)
            if req == "skp":
                hold = 1
        hold -= 1
        if skp_block == 0 and rng.random() < 0.0005:
            skp_block = rng.randrange(600, 1600)  # a long packet or FTS run
        skp_block = max(0, skp_block - 1)
        skp_ok = skp_block == 0 and rng.random() < 0.85
        count = (not bench.tx.drives(req, skp_ok)) and rng.random() < 0.7
        # Line impairments: corrupt or drop a symbol, insert one (phase
        # change), a stretch of noise, a TS with any fields, a gap.
        r = rng.random()
        if r < 0.01 and line.q:
            line.q[rng.randrange(len(line.q))] = rng.choice(noise)
        elif r < 0.015 and line.q:
            del line.q[rng.randrange(len(line.q))]
        elif r < 0.02:
            line.q.appendleft(rng.choice(noise))
        elif r < 0.025:
            line.push([rng.choice(noise) for _ in range(rng.randrange(1, 8))])
        elif r < 0.03:
            syms = m.ts_symbols(rng.randrange(1, 256), rng.random() < 0.5,
                                rng.choice([None, rng.randrange(256)]),
                                rng.choice([None, rng.randrange(256)]), rng.randrange(8))
            syms[5] = (0, rng.randrange(256))
            if rng.random() < 0.3:
                syms[6:] = [(0, syms[6][1] ^ 0xFF)] * 10
            line.push(syms)
        gap = rng.random() < 0.02
        if gap:
            await bench.step(req, fields, skp_ok, count,
                             rx_word=tuple(rng.choice(noise) for _ in range(2)),
                             rx_valid=rng.random() < 0.5, rx_ei=True, rng=rng)
        else:
            await bench.loop(line, req, fields, skp_ok, count, rng=rng)
        if len(line.q) > 64:
            line.q.clear()
        if rng.random() < 0.0005:
            await bench.reset(1)
            line = Line(rng.randrange(2))
    dut._log.info("counts %s", bench.counts)
    assert all(bench.counts.values()), f"vacuous run: {bench.counts}"


@cocotb.test()
async def random_vs_model(dut):
    """L1: random requests, schedule inputs and an impaired loopback line
    against the model, every output every cycle."""
    await random_run(dut, 20000)


@cocotb.test()
async def long_random(dut):
    """L2: the same over 200000 cycles (about 170 SKP intervals)."""
    await random_run(dut, 200000)
