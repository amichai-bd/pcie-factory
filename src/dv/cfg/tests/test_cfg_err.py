"""pcie_cfg error tests (wiki/src/dv/cfg/DV_PLAN.md): each event of the
HAS error table under each enable set, judged by the table below (written
from wiki/src/arch/cfg/HAS.md#errors), with the architecture-model shadow
of tb/cfg_env.py checking every cycle as well.
"""

from __future__ import annotations

import cocotb

from regress.funcov import Covergroup
from tb.cfg_env import Env

CMD, DEVCTL, PM_CSR = 0x04 // 4, 0x78 // 4, 0x44 // 4
# Command register bits.
PER, SERR = 1 << 6, 1 << 8
# Device Control bits.
CERE, NFERE, FERE, URRE = 1, 2, 4, 8
# Status register bits (DW 0x04) and Device Status bits (DW 0x78).
MDPE, RTA, RMA, SSE, DPE = 1 << 24, 1 << 28, 1 << 29, 1 << 30, 1 << 31
CED, NFED, FED, URD = 1 << 16, 1 << 17, 1 << 18, 1 << 19

# The HAS error table: event -> (Status bits, Device Status UR bit, class,
# UR-gated). Class "cor", "nf", "fatal" or None (no class, no message).
EVENTS = {
    "tl0": (0, URD, "nf", True),
    "tl1": (0, URD, "cor", True),
    "tl2": (0, 0, "fatal", False),
    "tl3": (DPE, 0, "nf", False),
    "tl4": (DPE, 0, "nf", False),       # plus MDPE under Parity Error Response
    "tl5": (0, 0, "cor", False),
    "tl6": (0, 0, "nf", False),
    "tl7": (RMA, 0, None, False),
    "tl8": (RTA, 0, None, False),
    "dl0": (0, 0, "cor", False), "dl1": (0, 0, "cor", False),
    "dl2": (0, 0, "cor", False), "dl3": (0, 0, "cor", False),
    "dl4": (0, 0, "fatal", False), "dl5": (0, 0, "fatal", False),
    "dl6": (0, 0, "fatal", False),
    "rcv": (0, 0, "cor", False),
}
CLASS_DS = {"cor": CED, "nf": NFED, "fatal": FED}
CLASS_KIND = {"cor": 1, "nf": 2, "fatal": 3}

# Enable sets: (Device Control bits, Command bits).
ENABLES = (
    (0, 0), (CERE | NFERE | FERE | URRE, SERR | PER), (CERE, 0), (NFERE, 0), (FERE, 0),
    (URRE | CERE, 0), (URRE | NFERE, 0), (0, SERR), (URRE, SERR), (URRE, 0), (0, PER),
)

COV_ERR = Covergroup("cfg_err")
COV_ERR.coverpoint("event", bins={name: name for name in EVENTS})
COV_ERR.coverpoint("outcome", bins={"message": "message", "gated": "gated",
                                    "outside_d0": "outside_d0", "no_class": "no_class"})
_NO_CLASS = [n for n, e in EVENTS.items() if e[2] is None]
COV_ERR.cross("event_x_outcome", "event", "outcome",
              unreachable=[(n, o) for n in _NO_CLASS for o in ("message", "gated", "outside_d0")]
              + [(n, "no_class") for n in EVENTS if n not in _NO_CLASS])


def pulse(name: str) -> tuple[int, int, int]:
    if name == "rcv":
        return 0, 0, 1
    bit = 1 << int(name[2:])
    return (bit, 0, 0) if name.startswith("tl") else (0, bit, 0)


def expected(name: str, dctl: int, cmd: int) -> tuple[int, int, int | None]:
    """(Status bits, Device Status bits, message kind or None) per the HAS."""
    sts, ds, cls, ur = EVENTS[name]
    if name == "tl4" and cmd & PER:
        sts |= MDPE
    if cls is None:
        return sts, ds, None
    ds |= CLASS_DS[cls]
    serr = bool(cmd & SERR)
    on = {"cor": bool(dctl & CERE), "nf": bool(dctl & NFERE) or serr,
          "fatal": bool(dctl & FERE) or serr}[cls]
    if ur and not dctl & URRE:
        on = False
    return sts, ds, CLASS_KIND[cls] if on else None


def seed() -> int:
    return int(cocotb.RANDOM_SEED or 0)


async def clear_status(env: Env) -> None:
    await env.cfg_write(CMD, 0xFF << 24, be=0x8)
    await env.cfg_write(DEVCTL, 0x0F << 16, be=0x4)


@cocotb.test()
async def error_table(dut):
    """Every event under every enable set: its Status and Device Status
    bits whatever the enables, its message decided by the class, the
    reporting enables, SERR# and the UR gate; Signaled System Error when tl
    takes ERR_NONFATAL or ERR_FATAL under SERR#; RW1C clearing by byte."""
    env = Env(dut, seed())
    await env.start()
    for dctl, cmd in ENABLES:
        await env.cfg_write(DEVCTL, dctl, be=0x1)
        await env.cfg_write(CMD, cmd, be=0x3)
        for name in EVENTS:
            sts, ds, kind = expected(name, dctl, cmd)
            n = len(env.taken)
            env.events.append(pulse(name))
            await env.run(8)
            kinds = [t.kind for t in env.taken[n:]]
            assert kinds == ([kind] if kind is not None else []), (
                f"{name} enables 0x{dctl:x}/0x{cmd:x}: messages {kinds}, expected {kind}")
            if kind in (2, 3) and cmd & SERR:
                sts |= SSE
            got_sts = (await env.cfg_read(CMD)) & 0xFF00_0000
            got_ds = (await env.cfg_read(DEVCTL)) & 0x000F_0000
            assert (got_sts, got_ds) == (sts, ds), (
                f"{name} enables 0x{dctl:x}/0x{cmd:x}: Status 0x{got_sts:08x} Device Status "
                f"0x{got_ds:08x}, expected 0x{sts:08x} 0x{ds:08x}")
            outcome = ("no_class" if EVENTS[name][2] is None else
                       "message" if kind is not None else "gated")
            COV_ERR.sample(event=name, outcome=outcome)
            await clear_status(env)
    # RW1C by byte: bytes not enabled keep their bits.
    env.events.append((1 << 3 | 1 << 7, 0, 1))
    await env.run(4)
    await env.cfg_write(CMD, 0xFFFFFFFF & ~0x546, be=0x7)
    assert (await env.cfg_read(CMD)) & 0xFF00_0000 == DPE | RMA
    await env.cfg_write(DEVCTL, 0xFFFF0000, be=0x8)
    assert (await env.cfg_read(DEVCTL)) & 0x000F_0000 == CED | NFED
    await clear_status(env)
    assert (await env.cfg_read(CMD)) & 0xFF00_0000 == 0
    assert (await env.cfg_read(DEVCTL)) & 0x000F_0000 == 0
    await env.drain()


@cocotb.test()
async def error_messages(dut):
    """Message priority (fatal, non-fatal, correctable), merging of a kind
    still due, no message outside D0 (status still set, nothing sent on the
    return) and an offered message withdrawn when the function leaves
    D0."""
    env = Env(dut, seed())
    await env.start()
    await env.cfg_write(DEVCTL, CERE | NFERE | FERE | URRE, be=0x1)
    env.msg_ready_p = 0.0
    env.events.append((1 << 6 | 1 << 2, 1, 1))
    await env.run(10)
    assert int(dut.msg_valid_o.value) == 1 and int(dut.msg_kind_o.value) == 3
    env.msg_ready_p = 1.0
    await env.run(10)
    assert [t.kind for t in env.taken] == [3, 2, 1], [t.kind for t in env.taken]
    # Outside D0: every event sets its status bits and sends no message,
    # then or on the return to D0.
    await clear_status(env)
    await env.cfg_write(CMD, SERR | PER, be=0x3)
    await env.cfg_write(PM_CSR, 3, be=0x1)
    n = len(env.taken)
    for name in EVENTS:
        sts, ds, _ = expected(name, CERE | NFERE | FERE | URRE, SERR | PER)
        env.events.append(pulse(name))
        await env.run(4)
        got_sts = (await env.cfg_read(CMD)) & 0xFF00_0000
        got_ds = (await env.cfg_read(DEVCTL)) & 0x000F_0000
        assert (got_sts, got_ds) == (sts, ds), f"{name} in D3hot: status bits"
        COV_ERR.sample(event=name,
                       outcome="no_class" if EVENTS[name][2] is None else "outside_d0")
        await clear_status(env)
    await env.cfg_write(PM_CSR, 0, be=0x1)
    await env.run(8)
    assert len(env.taken) == n, "message sent for an event outside D0"
    await env.cfg_write(CMD, 0, be=0x3)
    await clear_status(env)
    # An offer withdrawn by D3hot is discarded.
    env.msg_ready_p = 0.0
    env.events.append((1 << 2, 0, 0))
    await env.run(4)
    assert int(dut.msg_valid_o.value) == 1
    await env.cfg_write(PM_CSR, 3, be=0x1)
    assert int(dut.msg_valid_o.value) == 0
    env.msg_ready_p = 1.0
    await env.cfg_write(PM_CSR, 0, be=0x1)
    await env.run(8)
    assert len(env.taken) == n, "withdrawn error message sent"
    assert env.withdrawn >= 1
    await env.drain()
