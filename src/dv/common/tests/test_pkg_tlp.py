"""common_pkg TLP helper tests (wiki/src/dv/common/common_pkg.md).

The top is tb/common_pkg_tlp_fixture.sv: each helper on its own output.
Every result is compared with a reference written here from the owning
specs, not from the RTL: the kinds (wiki/src/arch/tl/TLP.md#kinds), the
receive checks (wiki/src/arch/tl/ROUTING.md#checks, steps 1, 3, 4 and 6),
the byte count rules (wiki/src/arch/tl/COMPLETIONS.md#byte-count-and-lower-address)
and the credit classes (wiki/src/arch/dll/FLOW_CONTROL.md#classes-and-credits).
The small domains are swept exhaustively; the 4 KiB check and the byte
order are swept at every boundary and on seeded random inputs.
"""

import random

import cocotb
from cocotb.triggers import Timer

from regress.funcov import Covergroup

# Kinds: Type -> the Fmt values that make it a kind (TLP.md, kinds).
KINDS = {0b00000: {0b000, 0b001, 0b010, 0b011},   # memory read, write
         0b00001: {0b000, 0b001},                 # locked memory read
         0b00010: {0b000, 0b010},                 # I/O
         0b00100: {0b000, 0b010},                 # configuration Type 0
         0b00101: {0b000, 0b010},                 # configuration Type 1
         0b01100: {0b010, 0b011},                 # FetchAdd
         0b01101: {0b010, 0b011},                 # Swap
         0b01110: {0b010, 0b011},                 # CAS
         0b01010: {0b000, 0b010},                 # completion
         0b01011: {0b000, 0b010}}                 # locked completion
KINDS.update({0b10000 | r: {0b001, 0b011} for r in range(8)})  # messages
CPL_TYPES = {0b01010, 0b01011}
TC0_CODES = set(range(0x20, 0x28)) | {0x14, 0x18, 0x19, 0x1B, 0x30, 0x31, 0x33, 0x00, 0x50}
CLS_P, CLS_NP, CLS_CPL = 0, 1, 2

COV = Covergroup("common_pkg_tlp")
COV.coverpoint("kind", bins=["known", "unknown"])
COV.coverpoint("cls", bins=["P", "NP", "CPL"])
COV.coverpoint("be_ok", bins=["ok", "bad"])
COV.coverpoint("cross_4k", bins=["inside", "ends_at_boundary", "crosses"])
COV.coverpoint("msg_tc0", bins=["tc0", "other"])


def kind_known(fmt, typ):
    return fmt in KINDS.get(typ, set())


def credit_class(with_data, typ):
    if (typ == 0 and with_data) or typ >> 3 == 0b10:
        return CLS_P
    return CLS_CPL if typ in CPL_TYPES else CLS_NP


def ceil4(n):
    return (n + 3) // 4


def enabled(be):
    return [i for i in range(4) if be >> i & 1]


def be_ok(n, fbe, lbe, addr_b2):
    if n == 1:
        return lbe == 0
    if fbe == 0 or lbe == 0:
        return False
    if n == 2 and not addr_b2:
        return True
    return fbe in (0b1111, 0b1110, 0b1100, 0b1000) and lbe in (0b0001, 0b0011, 0b0111, 0b1111)


def byte_total(n, fbe, lbe):
    if n == 1:
        e = enabled(fbe)
        return e[-1] - e[0] + 1 if e else 1
    below = enabled(fbe)[0] if fbe else 0
    above = 3 - enabled(lbe)[-1] if lbe else 3
    return 4 * n - below - above


def be_mask(be):
    return sum(0xFF << 8 * i for i in enabled(be))


class Fixture:
    def __init__(self, dut):
        self.dut = dut
        self.checks = 0

    async def drive(self, **ports):
        for name, value in ports.items():
            getattr(self.dut, f"{name}_i").value = value
        await Timer(1, unit="ns")

    def expect(self, out, want, ctx):
        got = int(getattr(self.dut, f"{out}_o").value)
        if got != want:
            raise AssertionError(f"{out}({ctx}): expected {want:#x} got {got:#x}")
        self.checks += 1


async def zero(f):
    await f.drive(fmt=0, typ=0, with_data=0, dw_len=1, fbe=0, lbe=0, addr_b2=0, addr_dw=0,
                  code=0, dw=0, n=0)


@cocotb.test()
async def contract(dut):
    """Exhaustive kinds, classes, credits, byte enables and message codes;
    the 4 KiB check at every boundary and at random; the byte order."""
    seed = cocotb.RANDOM_SEED
    rng = random.Random(seed)
    dut._log.info("seed %d", seed)
    f = Fixture(dut)
    await zero(f)

    for fmt in range(8):
        for typ in range(32):
            await f.drive(fmt=fmt, typ=typ)
            k = kind_known(fmt, typ)
            COV.sample(kind="known" if k else "unknown")
            f.expect("kind_known", k, f"fmt={fmt:03b} typ={typ:05b}")
            f.expect("is_cpl", k and typ in CPL_TYPES, f"fmt={fmt:03b} typ={typ:05b}")
            for wd in (0, 1):
                await f.drive(with_data=wd)
                c = credit_class(wd, typ)
                COV.sample(cls=["P", "NP", "CPL"][c])
                f.expect("credit_class", c, f"with_data={wd} typ={typ:05b}")

    for n in range(33):
        for wd in (0, 1):
            await f.drive(n=n, with_data=wd)
            f.expect("data_credits", ceil4(n) if wd else 0, f"with_data={wd} n={n}")
        if n:
            f.expect("dw_credits", ceil4(n), f"n={n}")

    for code in range(256):
        await f.drive(code=code)
        COV.sample(msg_tc0="tc0" if code in TC0_CODES else "other")
        f.expect("msg_tc0", code in TC0_CODES, f"code={code:#04x}")

    for n in (1, 2, 3, 4, 32, 1024):
        for fbe in range(16):
            for lbe in range(16):
                for b2 in (0, 1):
                    await f.drive(dw_len=n, fbe=fbe, lbe=lbe, addr_b2=b2)
                    ok = be_ok(n, fbe, lbe, b2)
                    COV.sample(be_ok="ok" if ok else "bad")
                    ctx = f"n={n} fbe={fbe:04b} lbe={lbe:04b} b2={b2}"
                    f.expect("be_ok", ok, ctx)
                    if ok or n == 1:
                        f.expect("byte_total", byte_total(n, fbe, lbe), ctx)
                if lbe == 0:
                    e = enabled(fbe)
                    f.expect("be_lo", e[0] if e else 0, f"be={fbe:04b}")
                    if e:
                        f.expect("be_hi", e[-1], f"be={fbe:04b}")
                    f.expect("be_mask", be_mask(fbe), f"be={fbe:04b}")

    cases = [(a, n) for n in (1, 2, 32, 1023, 1024) for a in (0, 1, 1024 - n, 1025 - n, 1023)]
    cases += [(rng.randrange(1024), rng.randrange(1, 1025)) for _ in range(500)]
    for a, n in cases:
        if not 0 <= a < 1024:
            continue
        await f.drive(addr_dw=a, dw_len=n)
        end = a + n
        COV.sample(cross_4k="crosses" if end > 1024 else
                   "ends_at_boundary" if end == 1024 else "inside")
        f.expect("cross_4k", end > 1024, f"addr_dw={a} n={n}")

    for dw in [0, 0xFFFFFFFF, 0x12345678] + [rng.getrandbits(32) for _ in range(100)]:
        await f.drive(dw=dw)
        f.expect("be_word", int.from_bytes(dw.to_bytes(4, "little"), "big"), f"dw={dw:#010x}")

    assert f.checks > 6000, f"only {f.checks} checks"
