"""Intel backends of the shared RAMs, linted against the DV-only altsyncram
stub (wiki/src/dv/common/ram/common_sdp_ram.md, Intel backend rows).

Neither ./build check lint nor the portable regressions read a backend file,
so this test lints each one with Verilator -Wall, as ./build check lint does,
together with the fpga backend's dv_models from cfg/backends.yaml:

- intel_lint: every legal geometry below (both dll buffer shapes, the
  regression shape, 9-bit lanes, the smallest RAM) elaborates with exactly
  the expected messages and no other: DECLFILENAME (the backend file holds
  its portable file's module) and PINCONNECTEMPTY for the three altsyncram
  pins that do not exist in DUAL_PORT mode (data_b, q_a, eccstatus), left
  explicitly unconnected as Intel-generated wrappers leave them, plus
  byteena_a in the one-lane instance, which uses no byte enables. Verilator
  reports empty connections of both generate branches (one lane, several
  lanes) whatever LANES is, so the expected count is the same for every
  geometry. Every illegal geometry
  stops elaboration with the block's own COMMON_ASSERT_PARAM message.
- stub_rejects: the stub alone, as top, accepts the backend's shape and
  rejects one parameter at a time outside it, each with exactly one
  elaboration failure, carrying that rule's message.

The backend's cycle behavior runs through the shared contract tests
(tests.test_ram) in the regression entries with `backend: fpga`. The
simulated top only hosts this test.

Provenance: the rejection set is adapted from the reject fixtures of
nand2anything src/common/dv/memory/ (n2a_altsyncram_stub_reject_*_fixture.sv
and test_memory_backends.py, commit 8bb70d248d4755d7510fb2c0ec6487a5e05dec41),
adapted with the owner's permission: parameter overrides instead of fixture
files, reduced to the DUAL_PORT shape.
"""

import re
import subprocess
from collections import Counter

import cocotb

from build import backends, sim
from build.ip import REPO_ROOT

SRC = REPO_ROOT / "src"
RTL = SRC / "rtl" / "common" / "ram"
BACKEND = backends.load(REPO_ROOT / "cfg")["fpga"]
MODELS = backends.dv_models(BACKEND, SRC)
INCDIR = SRC / "rtl" / "common"  # common_macros.svh

# Every shared RAM that has an fpga backend file.
BLOCKS = tuple(sorted(p.stem for p in RTL.glob("common_*.sv")
                      if backends.backend_file(p, BACKEND).is_file()))

LEGAL = (
    {"WIDTH": 34, "ADDR_W": 9, "LANES": 1},  # dll receive buffer
    {"WIDTH": 32, "ADDR_W": 8, "LANES": 1},  # dll replay buffer
    {"WIDTH": 16, "ADDR_W": 3, "LANES": 2},  # the regression shape
    {"WIDTH": 36, "ADDR_W": 4, "LANES": 4},  # 9-bit lanes
    {"WIDTH": 1, "ADDR_W": 1, "LANES": 1},   # the smallest RAM
)
ILLEGAL = (
    ({"WIDTH": 0}, "WIDTH must be at least 1"),
    ({"ADDR_W": 0}, "ADDR_W must be at least 1"),
    ({"LANES": 0}, "LANES must be at least 1 and divide WIDTH"),
    ({"WIDTH": 8, "LANES": 3}, "LANES must be at least 1 and divide WIDTH"),
    ({"WIDTH": 16, "LANES": 4}, "the Intel backend needs one lane or 8- or 9-bit lanes"),
)
# The only messages a legal backend lint may print: (rule, detail).
# Counted: data_b, q_a and eccstatus in both instances, byteena_a in the
# one-lane instance only.
EXPECTED = Counter({("DECLFILENAME", None): 1, ("PINCONNECTEMPTY", "byteena_a"): 1,
                    ("PINCONNECTEMPTY", "data_b"): 2, ("PINCONNECTEMPTY", "q_a"): 2,
                    ("PINCONNECTEMPTY", "eccstatus"): 2})

# The stub as top: the shape the backends request, then one rule broken at
# a time with the message that rule must give.
STUB_BASE = {
    "operation_mode": '"DUAL_PORT"', "intended_device_family": '"Cyclone IV E"',
    "ram_block_type": '"M9K"', "width_a": 8, "widthad_a": 4, "numwords_a": 16,
    "width_b": 8, "widthad_b": 4, "numwords_b": 16, "address_reg_b": '"CLOCK0"',
    "rdcontrol_reg_b": '"CLOCK0"', "read_during_write_mode_mixed_ports": '"OLD_DATA"',
    "clock_enable_input_a": '"BYPASS"', "clock_enable_output_a": '"BYPASS"',
    "clock_enable_input_b": '"BYPASS"', "clock_enable_output_b": '"BYPASS"',
}
STUB_REJECTS = (
    ({"operation_mode": '"SINGLE_PORT"'}, "operation_mode must be DUAL_PORT"),
    ({"intended_device_family": '"MAX 10"'}, "intended_device_family must be Cyclone IV E"),
    ({"ram_block_type": '"AUTO"'}, "ram_block_type must be M9K"),
    ({"lpm_type": '"altsyncram_other"'}, "lpm_type must be altsyncram"),
    ({"numwords_a": 15, "numwords_b": 15},
     "numwords_a must be 2**widthad_a and width_a at least 1"),
    ({"width_b": 4}, "port B must match port A (no mixed widths)"),
    ({"width_byteena_a": 2, "byte_size": 4},
     "byte_size, when given, must be 8 or 9 and width_a = width_byteena_a * byte_size"),
    # The one-lane setting Quartus rejects (Error 272006): one byte enable
    # with a byte_size that is not the width.
    ({"width_a": 34, "width_b": 34, "width_byteena_a": 1, "byte_size": 8},
     "byte_size, when given, must be 8 or 9 and width_a = width_byteena_a * byte_size"),
    ({"width_byteena_b": 2}, "width_byteena_a must be at least 1 and width_byteena_b 1"),
    ({"outdata_reg_b": '"CLOCK0"'}, "outputs must be UNREGISTERED (one-edge read latency)"),
    ({"rdcontrol_reg_b": '"CLOCK1"'}, "port B must register on CLOCK0 (single clock)"),
    ({"read_during_write_mode_mixed_ports": '"DONT_CARE"'},
     "read_during_write_mode_mixed_ports must be OLD_DATA"),
    ({"clock_enable_input_b": '"NORMAL"'}, "clock enables must be BYPASS"),
    ({"init_file": '"image.mif"'}, "init_file must be UNUSED"),
    ({"power_up_uninitialized": '"TRUE"'}, "power_up_uninitialized must be FALSE"),
)

_MSG_RE = re.compile(r"^%(Warning|Error)(?:-([A-Z0-9_]+))?: (.*)$")
_PIN_RE = re.compile(r"pin connected by name with empty reference: '(\w+)'")


def lint(top: str, sources: list, params: dict) -> tuple[int, list[tuple[str, str]], str]:
    """Verilator -Wall lint; returns (exit code, [(rule, text)], output)."""
    cmd = [str(sim.find_verilator()), "--lint-only", "-Wall", "-Wno-fatal",
           f"+incdir+{INCDIR}", "--top-module", top]
    cmd += [f"-G{k}={v}" for k, v in params.items()] + [str(s) for s in sources]
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=120, check=False)
    text = out.stdout + out.stderr
    msgs = [(m.group(2) or m.group(1), m.group(3)) for m in map(_MSG_RE.match, text.splitlines())
            if m and not m.group(3).startswith("Exiting due to")]
    return out.returncode, msgs, text


def key(rule: str, text: str) -> tuple[str, str | None]:
    pin = _PIN_RE.search(text)
    return (rule, pin.group(1) if rule == "PINCONNECTEMPTY" and pin else None)


@cocotb.test()
async def intel_lint(dut):
    """Legal geometries lint with exactly the expected messages; illegal ones
    fail with the block's own message."""
    del dut
    assert BLOCKS, "no shared RAM has an fpga backend file"
    checked = 0
    for block in BLOCKS:
        sources = [backends.backend_file(RTL / f"{block}.sv", BACKEND), *MODELS]
        for params in LEGAL:
            code, msgs, text = lint(block, sources, params)
            got = Counter(key(r, t) for r, t in msgs)
            assert code == 0 and got == EXPECTED, (
                f"{block} {params}: expected exactly {dict(EXPECTED)}, got exit "
                f"{code}:\n{text}")
            checked += 1
        for params, message in ILLEGAL:
            code, msgs, text = lint(block, sources, params)
            want = f"{block}: {message}"
            assert any(r == "USERFATAL" and want in t for r, t in msgs), (
                f"{block} {params}: expected an elaboration failure naming '{want}', got exit "
                f"{code}:\n{text}")
            checked += 1
    assert checked == len(BLOCKS) * (len(LEGAL) + len(ILLEGAL))


@cocotb.test()
async def stub_rejects(dut):
    """The stub accepts the backend shape and rejects each rule alone."""
    del dut
    code, msgs, text = lint("altsyncram", MODELS, STUB_BASE)
    assert code == 0 and not msgs, f"stub rejects the backend shape:\n{text}"
    for override, message in STUB_REJECTS:
        code, msgs, text = lint("altsyncram", MODELS, {**STUB_BASE, **override})
        fatals = [t for r, t in msgs if r == "USERFATAL"]
        assert len(fatals) == 1 and fatals[0].endswith(f"altsyncram stub: {message}"), (
            f"{override}: expected one failure, '{message}', got:\n{text}")
