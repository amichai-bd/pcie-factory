"""Shared RAM family tests (wiki/src/dv/common/ram/common_*ram*.md).

One module serves every RAM top: the toplevel name selects the kind and
its ports, and WIDTH, ADDR_W and LANES come from the parameters. Inputs
change at falling clk_i edges; after every rising edge each read port is
compared with models/common_ram_model.py on the bits the model knows, so a
read that lands one edge early or late, a wrong collision rule, a lost
lane or a missing hold fails at that cycle.

Provenance: the directed collision, hold and true dual-port sequences are
adapted from nand2anything src/common/dv/memory/test_sync_ram*.py,
test_sdp_ram*.py and test_tdp_ram.py (commit 085ef4b), adapted with the
owner's permission.
"""

import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, ReadOnly, RisingEdge

from models.common_ram_model import KINDS, Port, RamModel
from regress.funcov import Covergroup

CLK_NS = 10

# Functional coverage (DV plans, Strategy): per block, the port operation
# at each edge (a write counts only when it writes a lane: write enable
# and a nonzero lane mask), the lane enables of each write-enabled edge
# (none included), the lanes of each real write crossed with its operation,
# and the address extremes. Port a's address is binned (the read address
# of sdp_ram); the lanes are port a's when it is write-enabled, else b's.
OPS = {
    "sync_ram": ["write", "read"],
    "sync_ram_wt": ["write", "read"],
    "sync_ram_held": ["write_read", "write_hold", "read", "hold"],
    "sdp_ram": ["collide", "write_read", "write_hold", "read", "hold"],
    "tdp_ram": ["a_wr_b_rd_same", "b_wr_a_rd_same", "both_wr", "both_wr_same_word", "one_wr",
                "read"],
}
# The operations that write, crossed with the lanes of the write.
COLLIDE_OPS = {"sync_ram": {"write"}, "sync_ram_wt": {"write"}, "sync_ram_held": {"write_read"},
               "sdp_ram": {"collide"},
               "tdp_ram": {"a_wr_b_rd_same", "b_wr_a_rd_same", "both_wr_same_word"}}
WRITE_OPS = {k: [op for op in ops if op not in ("read", "hold")] for k, ops in OPS.items()}
COV = {}
for _kind, _ops in OPS.items():
    _cg = Covergroup(f"common_{_kind}")
    _cg.coverpoint("op", bins=_ops)
    _cg.coverpoint("wr_op", bins=WRITE_OPS[_kind])
    _cg.coverpoint("lanes", bins=["none", "partial", "all"])
    _cg.coverpoint("wr_lanes", bins=["partial", "all"])
    _cg.coverpoint("addr", bins=["first", "last", "other"])
    _cg.cross("wr_op_x_lanes", "wr_op", "wr_lanes")
    COV[_kind] = _cg


class Ram:
    """Drives one RAM top, keeps the model and checks every read port."""

    def __init__(self, dut):
        self.dut = dut
        name = dut._name
        self.kind = name.removeprefix("common_")
        if self.kind not in KINDS:
            raise ValueError(f"not a RAM top: {name}")
        self.width = int(dut.WIDTH.value)
        self.addr_w = int(dut.ADDR_W.value)
        self.lanes = int(dut.LANES.value)
        self.depth = 2 ** self.addr_w
        self.all_lanes = (1 << self.lanes) - 1
        self.model = RamModel(self.kind, self.width, self.addr_w, self.lanes)
        self.cycle = 0
        self.checked_bits = 0  # known bits compared, for the vacuity guard
        self.collisions = 0    # same-word read and write at one edge
        self.held = 0          # edges with the read enable low
        self.tdp = self.kind == "tdp_ram"

    async def start(self):
        self._drive(Port(), Port())
        cocotb.start_soon(Clock(self.dut.clk_i, CLK_NS, unit="ns").start())
        await FallingEdge(self.dut.clk_i)

    def _drive(self, a: Port, b: Port | None) -> None:
        d = self.dut
        if self.tdp:
            for p, x in (("a", a), ("b", b or Port())):
                getattr(d, f"{p}_wr_en_i").value = x.wr_en
                getattr(d, f"{p}_wr_lanes_i").value = x.lanes
                getattr(d, f"{p}_addr_i").value = x.addr
                getattr(d, f"{p}_wr_data_i").value = x.data
            return
        d.wr_en_i.value = a.wr_en
        d.wr_lanes_i.value = a.lanes
        d.wr_data_i.value = a.data
        if self.kind == "sdp_ram":
            d.wr_addr_i.value = a.wr_addr
            d.rd_addr_i.value = a.addr
        else:
            d.addr_i.value = a.addr
        if self.kind in ("sdp_ram", "sync_ram_held"):
            d.rd_en_i.value = a.rd_en

    def _outputs(self) -> list[int]:
        d = self.dut
        if self.tdp:
            return [int(d.a_rd_data_o.value), int(d.b_rd_data_o.value)]
        return [int(d.rd_data_o.value)]

    def _writes(self, p: Port) -> bool:
        """The port really writes: write enable and at least one lane."""
        return bool(p.wr_en and p.lanes & self.all_lanes)

    def _op(self, a: Port, b: Port) -> str:
        k = self.kind
        wa, wb = self._writes(a), self._writes(b)
        if k in ("sync_ram", "sync_ram_wt"):
            return "write" if wa else "read"
        if k == "sync_ram_held":
            return ("write_" if wa else "") + ("read" if a.rd_en else "hold")
        if k == "sdp_ram":
            if wa and a.rd_en and a.wr_addr == a.addr:
                return "collide"
            return ("write_" if wa else "") + ("read" if a.rd_en else "hold")
        if wa and wb:
            return "both_wr_same_word" if a.addr == b.addr else "both_wr"
        if wa and a.addr == b.addr:
            return "a_wr_b_rd_same"
        if wb and a.addr == b.addr:
            return "b_wr_a_rd_same"
        return "one_wr" if wa or wb else "read"

    def _lanes_bin(self, lanes: int) -> str:
        return "none" if lanes == 0 else "all" if lanes == self.all_lanes else "partial"

    def _addr_bin(self, addr: int) -> str:
        return "first" if addr == 0 else "last" if addr == self.depth - 1 else "other"

    def _sample(self, a: Port, b: Port) -> None:
        op = self._op(a, b)
        cg = COV[self.kind]
        wr = a if a.wr_en else b if self.tdp and b.wr_en else None
        values = {"op": op, "addr": self._addr_bin(a.addr)}
        if wr is not None:
            values["lanes"] = self._lanes_bin(wr.lanes & self.all_lanes)
        if op in WRITE_OPS[self.kind]:
            # Both ports writing: the lanes written in all (disjoint on one
            # word, so "all" there means the two writes fill the word).
            lanes = ((a.lanes | b.lanes) if op.startswith("both_wr")
                     else a.lanes if self._writes(a) else b.lanes)
            values["wr_op"] = op
            values["wr_lanes"] = self._lanes_bin(lanes & self.all_lanes)
        cg.sample(**values)
        if op in COLLIDE_OPS[self.kind]:
            self.collisions += 1
        if self.kind in ("sdp_ram", "sync_ram_held") and not a.rd_en:
            self.held += 1

    async def step(self, a: Port, b: Port | None = None) -> list[int]:
        """Drive at the falling edge, take the rising edge, check outputs."""
        b = b if b is not None else Port()
        self._drive(a, b)
        self._sample(a, b)
        await RisingEdge(self.dut.clk_i)
        self.model.edge(a, b if self.tdp else None)
        await ReadOnly()
        got = self._outputs()
        for port, ((bits, known), value) in enumerate(zip(self.model.outputs(), got)):
            if value & known != bits & known:
                raise AssertionError(
                    f"{self.kind} cycle {self.cycle}: port {'ab'[port]} rd_data expected "
                    f"{bits & known:#x} (known mask {known:#x}) got {value & known:#x}; "
                    f"inputs a={a} b={b if self.tdp else '-'}")
            self.checked_bits += bin(known).count("1")
        self.cycle += 1
        await FallingEdge(self.dut.clk_i)
        return got

    def word(self, addr: int, salt: int) -> int:
        """A per-address, per-pass data pattern that differs in every lane."""
        rng = random.Random(addr * 7919 + salt)
        return rng.getrandbits(self.width)


async def directed(r: Ram) -> None:
    """Fill, read back, collisions, lanes, enables, holds and the dual-port
    cases, each checked against the model at every edge."""
    tdp = r.tdp
    full = r.all_lanes
    rd_ports = 2 if tdp else 1

    # Fill every word, then read each back: one-edge latency, distinct data.
    for addr in range(r.depth):
        p = Port(wr_en=1, lanes=full, addr=addr, wr_addr=addr, data=r.word(addr, 1), rd_en=0)
        await r.step(p, Port(addr=addr) if tdp else None)
    for addr in range(r.depth):
        back = r.depth - 1 - addr
        got = await r.step(Port(addr=addr), Port(addr=back) if tdp else None)
        want = [r.word(addr, 1), r.word(back, 1)][:rd_ports]
        assert got == want, f"read-back of {addr}: expected {want} got {got}"

    # Same-edge write and read of one word: old data (new for write-through).
    addr = r.depth - 1
    new = r.word(addr, 2)
    got = await r.step(Port(wr_en=1, lanes=full, addr=addr, wr_addr=addr, data=new),
                       Port(addr=addr) if tdp else None)
    want = new if r.kind == "sync_ram_wt" else r.word(addr, 1)
    assert got[-1] == want, f"collision: expected {want:#x} got {got[-1]:#x}"
    got = await r.step(Port(addr=addr), Port(addr=addr) if tdp else None)
    assert got[0] == new, f"after collision: expected {new:#x} got {got[0]:#x}"

    # Write enable low and lane enables none: nothing is written.
    await r.step(Port(wr_en=0, lanes=full, addr=0, wr_addr=0, data=~r.word(0, 1) & r.model.mask))
    await r.step(Port(wr_en=1, lanes=0, addr=0, wr_addr=0, data=~r.word(0, 1) & r.model.mask))
    got = await r.step(Port(addr=0))
    assert got[0] == r.word(0, 1), "a disabled write changed word 0"

    # One lane at a time: each write changes only its lane.
    for lane in range(r.lanes):
        await r.step(Port(wr_en=1, lanes=1 << lane, addr=1 % r.depth, wr_addr=1 % r.depth,
                          data=r.word(lane, 3)))
    await r.step(Port(addr=1 % r.depth))

    # Read enable low (held and simple dual-port): the output holds while
    # the address and the word change.
    if r.kind in ("sync_ram_held", "sdp_ram"):
        await r.step(Port(addr=0))
        held = (await r.step(Port(addr=0)))[0]
        for i in range(4):
            got = await r.step(Port(wr_en=1, lanes=full, addr=i % r.depth, wr_addr=0,
                                    data=r.word(i, 4), rd_en=0))
            assert got[0] == held, f"hold: output changed to {got[0]:#x} from {held:#x}"
        await r.step(Port(addr=0))

    # Simple dual-port: write one word while reading another.
    if r.kind == "sdp_ram":
        for i in range(r.depth):
            await r.step(Port(wr_en=1, lanes=full, wr_addr=i, addr=(i + 1) % r.depth,
                              data=r.word(i, 5)))

    # True dual-port: each port reads what the other wrote, both write
    # different words, disjoint lanes of one word.
    if tdp:
        last = r.depth - 1
        await r.step(Port(wr_en=1, lanes=full, addr=0, data=r.word(0, 6)), Port(addr=0))
        await r.step(Port(addr=last), Port(wr_en=1, lanes=full, addr=last, data=r.word(last, 6)))
        await r.step(Port(wr_en=1, lanes=full, addr=0, data=r.word(0, 7)),
                     Port(wr_en=1, lanes=full, addr=last, data=r.word(last, 7)))
        if r.lanes > 1:
            await r.step(Port(wr_en=1, lanes=1, addr=0, data=r.word(1, 8)),
                         Port(wr_en=1, lanes=full & ~1, addr=0, data=r.word(2, 8)))
        await r.step(Port(addr=0), Port(addr=last))
        await r.step(Port(addr=last), Port(addr=0))


def random_ports(r: Ram, rng: random.Random) -> tuple[Port, Port]:
    """Random legal stimulus, biased to share addresses."""
    def one() -> Port:
        lanes = rng.choice([r.all_lanes, r.all_lanes, 0, rng.getrandbits(r.lanes)])
        return Port(wr_en=int(rng.random() < 0.5), lanes=lanes, addr=rng.randrange(r.depth),
                    wr_addr=rng.randrange(r.depth), data=rng.getrandbits(r.width),
                    rd_en=int(rng.random() < 0.7))
    a, b = one(), one()
    if rng.random() < 0.3:
        a.wr_addr = a.addr
    if rng.random() < 0.3:
        b.addr = a.addr
    if r.tdp and a.wr_en and b.wr_en and a.addr == b.addr:
        b.lanes &= ~a.lanes  # both writing one lane of one word is outside the contract
    return a, b


async def random_run(dut, cycles: int) -> None:
    seed = cocotb.RANDOM_SEED
    rng = random.Random(seed)
    dut._log.info("seed %d", seed)
    r = Ram(dut)
    await r.start()
    for _ in range(cycles):
        a, b = random_ports(r, rng)
        await r.step(a, b)
    # Vacuity guard: most compared bits were known, and the collision and
    # hold cases happened.
    assert r.checked_bits > cycles * r.width // 2, f"only {r.checked_bits} known bits compared"
    assert r.collisions > cycles // 20, f"only {r.collisions} same-word collisions"
    if r.kind in ("sdp_ram", "sync_ram_held"):
        assert r.held > cycles // 10, f"only {r.held} held edges"


@cocotb.test()
async def contract(dut):
    """Directed: fill and read back, collision rule, disabled writes, lanes,
    holds and the dual-port cases."""
    r = Ram(dut)
    await r.start()
    await directed(r)
    assert r.checked_bits > 0 and r.collisions > 0


@cocotb.test()
async def random_ops(dut):
    """Random legal traffic against the model."""
    await random_run(dut, 2000)


@cocotb.test()
async def long_ops(dut):
    """Long random traffic against the model."""
    await random_run(dut, 20000)
