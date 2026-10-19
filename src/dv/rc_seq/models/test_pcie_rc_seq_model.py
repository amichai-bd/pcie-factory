"""Unit tests of the pcie_rc_seq reference model and the stand-in for
rc_tl (no simulator): the step format, the programs, the interpreter's
cycle rules from the MAS, and every program passing against the stand-in
and failing at a corrupted EXPECT."""

import pytest

import pcie_rc_seq_model as m
import pcie_rc_seq_standin as sd


def test_step_fields_at_mas_positions():
    w = m.encode(op=0x1F, status=7, chk=1, cond=7, idx=31, len=63, fbe=0xF, lbe=0xF,
                 addr=m.FULL, data=m.FULL, mask=m.FULL)
    assert w == (1 << 127) - 1                       # every field below the reserved bit 127
    assert m.encode(op=1) == 1 << 122
    assert m.encode(status=1) == 1 << 119
    assert m.encode(chk=1) == 1 << 118
    assert m.encode(cond=1) == 1 << 115
    assert m.encode(idx=1) == 1 << 110
    assert m.encode(len=1) == 1 << 104
    assert m.encode(fbe=1) == 1 << 100 and m.encode(lbe=1) == 1 << 96
    assert m.encode(addr=1) == 1 << 64 and m.encode(data=1) == 1 << 32 and m.encode(mask=1) == 1
    with pytest.raises(ValueError):
        m.encode(op=0x20)


def test_constructor_defaults():
    s = m.decode(m.req(m.MEM_RD, 0x1000_0000, 32))
    assert (s.op, s.len, s.fbe, s.lbe, s.chk, s.status) == (0x12, 32, 0xF, 0xF, 1, m.OK)
    s = m.decode(m.req(m.CFG_RD, 0))
    assert (s.len, s.lbe) == (1, 0)
    assert m.cfg_addr(1, 2, 3, 0x34) == 1 << 20 | 2 << 15 | 3 << 12 | 0x34
    assert m.fail() == 0


@pytest.mark.parametrize("prog", range(len(m.PROGRAM_NAMES)))
def test_programs_fit_and_end(prog):
    rom = m.program(prog).rom()
    assert len(rom) <= m.ROM_STEPS
    ops = [m.decode(w).op for w in rom]
    assert m.DONE in ops


@pytest.mark.parametrize("prog", range(len(m.PROGRAM_NAMES)))
@pytest.mark.parametrize("seed", range(6))
def test_program_passes_on_standin(prog, seed):
    seq, _ = sd.reference_run(prog, seed)
    assert seq.passed, seq.events[-1]


@pytest.mark.parametrize("prog", range(len(m.PROGRAM_NAMES)))
def test_every_expect_can_be_made_to_fail(prog):
    """Each EXPECT of a program is reachable by one wrong bit, and the run
    then fails exactly there."""
    rom = m.program(prog).rom()
    expects = {pc for pc, w in enumerate(rom) if m.decode(w).op == m.EXPECT}
    choices = sd.expect_corruptions(prog, 3)
    assert {pc for pc, _ in choices} == expects
    for pc, kw in choices:
        seq, _ = sd.reference_run(prog, 3, **kw)
        assert not seq.passed and seq.fail_step == pc, (pc, kw, seq.events[-1])


def _wait_rom(op_step):
    return [op_step, m.done()]


def _drive(seq, cycles, **inp):
    for _ in range(cycles):
        seq.clock(m.Inputs(**inp))


@pytest.mark.parametrize("c, ok", [(0, True), (5, True), (6, False)])
def test_wait_link_timeout_bound(c, ok):
    """A wait with timeout T passes when its condition first holds at
    cycle c <= T and fails at the step otherwise (MAS, Commands)."""
    seq = m.RcSeqModel(_wait_rom(m.wait_link(5)))
    seq.clock(m.Inputs(start=1))                     # IDLE -> FETCH
    _drive(seq, 2)                                   # FETCH, EXEC
    for k in range(20):
        seq.clock(m.Inputs(dl_up=int(k >= c)))
        if not seq.running:
            break
    assert seq.done and bool(seq.passed) == ok and (ok or seq.fail_step == 0)


def test_jump_limit_and_l_reset():
    a = m.Asm()
    a.label("top")
    a(m.req(m.HOST_RD, 0))
    a.jump(m.DATA_NE, "top", limit=2, value=1, mask=1)
    a(m.done())
    seq = m.RcSeqModel(a.rom())
    seq.R[0] = 0
    seq.clock(m.Inputs(start=1))
    takes = 0
    for _ in range(200):
        i = m.Inputs()
        if seq.state == "REQ_OFFER":
            i = m.Inputs(cmd_ready=1, rsp_valid=1, rsp_last=1, rsp_data=0)
        seq.clock(i)
        if seq.events and seq.events[-1][3] == "DATA_NE taken back":
            takes = sum(1 for e in seq.events if e[3] == "DATA_NE taken back")
        if not seq.running:
            break
    assert takes == 2 and not seq.passed and seq.fail_step == 1


def test_pc_above_rom_fails_there():
    seq = m.RcSeqModel([m.jump(m.ALWAYS, 0x0123)])
    seq.clock(m.Inputs(start=1))
    _drive(seq, 4)
    assert seq.done and not seq.passed and seq.fail_step == 0x0123


@pytest.mark.parametrize("op", [m.FAIL, 0x07, 0x0F])
def test_fail_and_reserved_ops_fail(op):
    seq = m.RcSeqModel([m.encode(op=op), m.done()])
    seq.clock(m.Inputs(start=1))
    _drive(seq, 2)
    assert seq.done and not seq.passed and seq.fail_step == 0


def test_start_needs_a_rising_edge_and_reset_counts_as_low():
    seq = m.RcSeqModel([m.done()])
    seq.clock(m.Inputs(rst=1, start=1))
    seq.clock(m.Inputs(start=1))                     # held through reset: starts
    assert seq.running
    _drive(seq, 2, start=1)
    assert seq.done and seq.passed
    _drive(seq, 5, start=1)                          # held: no second run
    assert seq.done and not seq.running


def test_standin_refusals_and_capture_overflow():
    si = sd.standin(1, "m4")
    for n in range(200):
        si.drive(n, int(n < 4), 0, (0,) * 6, 0)
    assert si.dl_up
    r = si._answer(200, sd.Command(m.MEM_RD, 0x1000_0FF0, 8, 0xF, 0xF, 0))
    assert r.beats == [(m.LOCAL_UR, 0)]              # crosses 4 KiB
    r = si._answer(200, sd.Command(12, 0, 1, 0xF, 0, 0))
    assert r.beats == [(m.LOCAL_UR, 0)]              # undefined op
    for k in range(sd.CAPTURE_DEPTH + 1):
        si._msi(0, k)
    assert len(si.capture) == sd.CAPTURE_DEPTH and si.err >> sd.E_MSI_DROP & 1


def _used_fields(w: int) -> dict:
    """A step's fields its op uses (MAS, Step format), with the waits'
    timeouts, the Hot Reset hold and the loop limits left out: those are
    the package's constants (PROGRAMS, Constants), which the arch model
    sets on its own."""
    s = m.decode(w)
    d = dict(s.__dict__)
    if s.op in (m.WAIT_MSI, m.WAIT_LINK, m.HOT_RESET):
        d["data"] = d["addr"] = 0
    if s.op == m.JUMP:
        d["addr"] &= 0xFFFF
        if s.cond not in (m.DATA_EQ, m.DATA_NE):
            d["data"] = d["mask"] = d["idx"] = 0
    return d


@pytest.mark.parametrize("name", ["ENUM", "MMIO", "DMA_MSI", "ERRORS"])
def test_programs_agree_with_the_arch_model(name):
    """Second reference: the rc architecture model builds the same M4
    programs from the programs page."""
    import sys
    from pathlib import Path
    sys.path.append(str(Path(__file__).resolve().parents[3] / "arch" / "rc"))
    import pcie_rc_arch as arch
    mine, theirs = m.program(name).rom(), arch.program(name).rom()
    assert len(mine) == len(theirs)
    for pc, (a, b) in enumerate(zip(mine, theirs)):
        assert _used_fields(a) == _used_fields(b), (name, pc)
