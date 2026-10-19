"""Back-to-back e2e of pcie_rc against pcie_core over the full phy stack
(wiki/src/dv/rc/DV_PLAN.md).

rc_seq's program (the entry's PROGRAM) alone drives the host side; the
host model shadow checks both directions of rc's link and every result
rc_tl gives rc_seq; the endpoint side keeps the core e2e's application
model, its checks and the cfg shadow (tb/rc_e2e_env.py). Every test runs
the program to done_o, which must pass, drains, and passes the
environment's final checks; the tests add what each acceptance case
needs on the line.
"""

import cocotb

from tb.rc_e2e_env import (BAR0, COV_ERR, COV_LINE, INJ_CA, INJ_FATAL, INJ_MALFORMED,
                           INJ_POISONED, INJ_UNEXPECTED, RUN_FLAG, CheckError, Cut, Env,
                           InjectorLine, core, ht, inj, lm)

# Cycle bounds of each program's run at the tb defaults (TIMEOUT_DIV 1024,
# CPL_TIMEOUT_DIV 1024), with margin over the measured runs.
LIMIT = {"short": 150_000, "errors": 400_000}


def seed() -> int:
    return cocotb.RANDOM_SEED


async def _run(env: Env, limit: int, expected=()) -> dict:
    env.start()
    await env.run_program(limit)
    await env.drain()
    stats = env.finish(expected)
    for b in range(13):
        if any(st == 0 and op == 8 and d >> b & 1 for _, op, st, d, _ in env.mon.results):
            COV_ERR.sample(bit=b)
    return stats


class Reads:
    """The rc requests on rc's transmit stream, by tag: (address, DWs)."""

    def __init__(self, env: Env):
        self.by_tag: dict[int, tuple[int, int]] = {}
        env.mon.on_tx.append(self._tx)

    def _tx(self, cycle: int, raw: bytes) -> None:
        t = ht.Tlp.decode(raw)
        if t.kind == "MRd":
            self.by_tag[t.tag] = (t.address, t.length_dw)

    def of(self, tlp: bytes):
        """(completion, address, DWs) when ``tlp`` is a completion for a
        memory read rc sent, else None."""
        try:
            c = ht.Tlp.decode(tlp)
        except ht.TlpError:
            return None
        if not c.is_completion or c.tag not in self.by_tag:
            return None
        return (c,) + self.by_tag[c.tag]


def _engage(env: Env, select_up=None, select_dn=None) -> tuple[lm.TlpSide, lm.TlpSide]:
    """Rewriters on both directions from reset: ep to rc replaces TLPs by
    ``select_up``, rc to ep by ``select_dn`` and carries rc's Acks back to
    the ep's sequence numbers."""
    up = lm.TlpSide(select_up)
    dn = lm.TlpSide(select_dn, ack_shift=up.shift)
    up.ack_shift = dn.shift
    env.up.model = lm.LineRewriter(up.on_packet)
    env.dn.model = lm.LineRewriter(dn.on_packet)
    return up, dn


def _cpl(c: ht.Tlp, data: bytes, byte_count: int, lower: int, **kw) -> bytes:
    return ht.completion(c.completer_id, c.requester_id, c.tag, kw.pop("status", ht.CPL_SC),
                         byte_count, lower, data, tc=c.tc, attr=c.attr, **kw).encode()


# ------------------------------------------------------------------- L1

@cocotb.test()
async def e2e_enum(dut):
    """Goal item 4, PROGRAM 0 (ENUM): the Type 1 header, the endpoint's IDs,
    the capability walk, every BAR sized and assigned, Memory Space and Bus
    Master Enable, MSI. The shadow learnt the function and its BARs from
    the configuration traffic alone."""
    env = Env(dut, seed())
    await _run(env, LIMIT["short"])
    sh = env.mon.shadow
    dev = sh.devices.get(0x0100)
    assert dev is not None and dev.msi is not None, "the shadow learnt no function or no MSI"
    assert sorted(dev.bars) == [0, 2] and dev.bars[0].base == BAR0, (
        f"BARs the shadow learnt: {[(i, hex(b.base)) for i, b in dev.bars.items()]}")
    assert env.cfg.compared == 18, f"cfg shadow compared {env.cfg.compared} of the 18 reads"


@cocotb.test()
async def e2e_mmio(dut):
    """Goal item 5, PROGRAM 1 (MMIO): BAR0 writes and reads with their
    data; the 32-DW read's completion and the 4-DW read's (which crosses a
    64-byte boundary) are each replaced on the line by two completions
    split on the 64-byte boundary (Byte Count and Lower Address of each);
    rc_seq checks the data and the shadow the reassembly."""
    env = Env(dut, seed())
    reads = Reads(env)
    done = []

    def select(seq, tlp):
        hit = reads.of(tlp)
        if hit is None:
            return None
        c, addr, n = hit
        if (addr, n) not in ((BAR0, 32), (BAR0 + 0x38, 4)) or (addr, n) in done or \
                c.status != ht.CPL_SC or len(c.data) != 4 * n:
            return None
        done.append((addr, n))
        cut = (((addr >> 6) + 1) << 6) - addr          # bytes up to the next 64-byte boundary
        first, rest = c.data[:cut], c.data[cut:]
        COV_LINE.sample(rewrite="split", splits=2)
        return [_cpl(c, first, 4 * n, addr & 0x7F), _cpl(c, rest, 4 * n - cut, (addr + cut) & 0x7F)]

    up, dn = _engage(env, select)
    stats = await _run(env, LIMIT["short"])
    assert sorted(done) == [(BAR0, 32), (BAR0 + 0x38, 4)], f"splits made {done}"
    assert up.shift.extra and dn.acks_translated, "no renumbering or Ack carried back"
    assert stats["shadow"]["rx_completions"] >= 2


@cocotb.test()
async def e2e_dma_msi(dut):
    """Goal item 6, PROGRAM 2 (DMA_MSI): the endpoint's bus-master read of
    host memory and write back, then its MSI, captured and checked by
    rc_seq; the shadow checks rc's completion data and the capture."""
    env = Env(dut, seed())
    stats = await _run(env, LIMIT["short"])
    assert stats["copies"] == ["sent"], f"DMA copies {stats['copies']}"
    assert stats["msis"] == 1 and stats["shadow"]["tx_completions"] >= 1


@cocotb.test()
async def e2e_inject(dut):
    """Goal item 7, PROGRAM 5 (INJECT): errors the endpoint never sends,
    injected on the line: a Completer Abort, a poisoned and a malformed
    (Byte Count) completion to BAR0 reads, a stale copy of a completion
    (unexpected), and a write made malformed toward the endpoint, which
    reports ERR_FATAL. rc_seq checks each result and the record (bits 1,
    4, 5, 6 and 9); the shadow reports each endpoint-side rule the line
    broke and nothing else."""
    env = Env(dut, seed(), shadow_kw={"strict": False})
    reads = Reads(env)
    made = []

    def select_up(seq, tlp):
        hit = reads.of(tlp)
        if hit is None:
            return None
        c, addr, n = hit
        off = addr - BAR0
        if n != 1 or off in made or c.status != ht.CPL_SC:
            return None
        if off == INJ_CA:
            new = [_cpl(c, b"", 4, addr & 0x7F, status=ht.CPL_CA)]
            COV_LINE.sample(rewrite="ca")
        elif off == INJ_POISONED:
            new = [_cpl(c, c.data, 4, addr & 0x7F, ep=1)]
            COV_LINE.sample(rewrite="poisoned")
        elif off == INJ_MALFORMED:
            new = [_cpl(c, c.data, 8, addr & 0x7F)]
            COV_LINE.sample(rewrite="malformed_cpl")
        elif off == INJ_UNEXPECTED:
            stale = ht.completion(c.completer_id, c.requester_id, (c.tag - 1) % 32, ht.CPL_SC,
                                  4, addr & 0x7F, c.data).encode()
            new = [tlp, stale]
            COV_LINE.sample(rewrite="stale_copy")
        else:
            return None
        made.append(off)
        return new

    def select_dn(seq, tlp):
        t = ht.Tlp.decode(tlp)
        if t.kind != "MWr" or t.address != BAR0 + INJ_FATAL or INJ_FATAL in made:
            return None
        made.append(INJ_FATAL)
        COV_LINE.sample(rewrite="malformed_req")
        return [tlp[:7] + bytes([tlp[7] | 0xF0]) + tlp[8:]]    # Last DW BE on 1 DW

    _engage(env, select_up, select_dn)
    await _run(env, LIMIT["short"], expected=(
        r"endpoint: mem_rd tag \d+: poisoned completion",
        r"endpoint: mem_rd tag \d+: Byte Count 8, expected 4",
        r"endpoint: unexpected completion"))
    assert sorted(made) == sorted([INJ_CA, INJ_POISONED, INJ_MALFORMED, INJ_UNEXPECTED,
                                   INJ_FATAL]), f"injected {made}"
    assert env.tl_err_count[2] == 1, f"endpoint tl_err malformed {env.tl_err_count}"
    assert env.cfg.received_msgs[core.cfga.MSG_ERR_FATAL] >= 1, "no ERR_FATAL from the endpoint"


# ------------------------------------------------------------------- L2

async def _errors(dut, seed_):
    env = Env(dut, seed_)
    rng = env.rng
    state = {"armed": False, "polls": rng.randint(1, 3), "cut": False}

    def on_cmd(op, addr, data):
        if op == 7 and addr == RUN_FLAG and data == 2:      # HOST_WR flag = 2: R12 next
            state["armed"] = True

    def on_result(op, st, data, last):
        if state["armed"] and op == 2 and last:
            state["polls"] -= 1
            if state["polls"] == 0:
                state["armed"] = False
                state["cut"] = True
                env.up.model = env.dn.model = Cut()
                env.log.info("line cut at cycle %d (link down under traffic)", env.cycle)
                COV_LINE.sample(event="link_down")

    env.mon.on_cmd.append(on_cmd)
    env.mon.on_result.append(on_result)
    env.allow_link_errors = True        # the cut and the Hot Reset make receiver errors

    async def restore():
        while not state["cut"]:
            await env.cycles(16)
        await env.until(lambda: not env.rd["rp_dl_up_o"]() and not env.dl_up, 100_000,
                        "both ends DL_Down on the cut line")
        env.up.model = env.dn.model = None
        env.log.info("line restored at cycle %d", env.cycle)

    cocotb.start_soon(restore())
    stats = await _run(env, LIMIT["errors"])
    COV_LINE.sample(event="hot_reset")
    assert stats["link_downs"][0] == 2, f"rc link downs {stats['link_downs']}: Hot Reset and cut"
    copies = stats["copies"]
    assert copies[0] == "read failed" and copies[1:] == ["sent"] * 5, f"DMA copies {copies}"
    assert stats["msis"] == 4, f"captures taken {stats['msis']}"


@cocotb.test()
async def e2e_errors(dut):
    """Goal item 7, PROGRAM 3 (ERRORS): a posted and a non-posted
    Unsupported Request at the endpoint (UR completion, ERR_COR and
    ERR_NONFATAL), the outbound refusals, an endpoint read outside host
    memory (bit 11), the MSI past a full capture (bit 12, certain at depth
    4), the completion timeout on the never-answered DW, a Hot Reset and a
    link down (the line cut while rc_seq polls BAR0), each followed by
    re-enumeration and traffic."""
    await _errors(dut, seed())


@cocotb.test()
async def e2e_link_errors(dut):
    """Goal item 7, PROGRAM 1 (MMIO) with the link DV's error injector on
    both directions of the line: TLP LCRC errors, corrupted and dropped
    TLPs and DLLPs; the links replay, the run passes and the shadow sees
    every TLP once, in order (no TLP lost)."""
    env = Env(dut, seed())
    rng = env.rng
    injs = {}
    for name in ("up", "dn"):
        injs[name] = inj.Injector(rng, look_words=4)
        getattr(env, name).model = InjectorLine(injs[name])
    env.allow_link_errors = True

    async def arm():
        await env.until(lambda: env.dl_up and env.rd["rp_dl_up_o"](), 100_000, "link up")
        for i in injs.values():
            i.rates.update({"lcrc": 0.04, "corrupt_tlp": 0.02, "drop_tlp": 0.02,
                            "corrupt_dllp": 0.02, "drop_dllp": 0.02})

    cocotb.start_soon(arm())
    await _run(env, LIMIT["short"])
    counts = {n: dict(i.counts) for n, i in injs.items()}
    env.log.info("injected: %s; link error pulses %d", counts, len(env.link_err_log))
    tlp_hits = sum(c[k] for c in counts.values() for k in ("lcrc", "corrupt_tlp", "drop_tlp"))
    assert tlp_hits >= 3, f"too few TLP errors injected: {counts}"
    assert any(dl or rdl for _, dl, _, rdl, _ in env.link_err_log), "no dl_err_o pulse"
    COV_LINE.sample(event="replay")
    if env.rp_link_downs:
        raise CheckError(f"rc link went down {env.rp_link_downs} times under link errors")
