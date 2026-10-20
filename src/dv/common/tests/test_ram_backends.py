"""Intel backends of the shared RAMs, linted against the DV-only altsyncram
stub (wiki/src/dv/common/ram/common_sdp_ram.md, Intel backend rows).

Neither ./build check lint nor the portable regressions read a backend file,
so this test lints each one with Verilator -Wall, as ./build check lint does,
together with the fpga backend's dv_models from cfg/backends.yaml:

- intel_lint: every legal geometry below (both dll buffer shapes and, for
  common_sync_ram, the rc_tl host memory shape, the regression shape,
  9-bit lanes, the smallest RAM) elaborates with exactly the expected
  messages and no other: DECLFILENAME (the backend file holds its portable
  file's module) and PINCONNECTEMPTY for the altsyncram pins the block's
  mode leaves explicitly unconnected, as Intel-generated wrappers leave
  them: data_b, q_a and eccstatus in DUAL_PORT (common_sdp_ram), q_b and
  eccstatus in SINGLE_PORT (common_sync_ram), plus byteena_a in the
  one-lane instance, which uses no byte enables. Verilator reports empty
  connections of both generate branches (one lane, several lanes) whatever
  LANES is, so the expected count is the same for every geometry. Every
  illegal geometry stops elaboration with the block's own
  COMMON_ASSERT_PARAM message.
- stub_rejects: the stub alone, as top, accepts each backend's shape
  (DUAL_PORT and SINGLE_PORT) and rejects one parameter at a time outside
  it, each with exactly one elaboration failure, carrying that rule's
  message.

The sky130 (asic) backend is linted the same way against its DV-only macro
model (sky130_lint), and sky130_select proves which geometries take the
macros: linted without the model, only those fail, on the missing macro.

The backend's cycle behavior runs through the shared contract tests
(tests.test_ram) in the regression entries with `backend: fpga`. The
simulated top only hosts this test.

Provenance: the rejection set is adapted from the reject fixtures of
nand2anything src/common/dv/memory/ (n2a_altsyncram_stub_reject_*_fixture.sv
and test_memory_backends.py, commit 8bb70d248d4755d7510fb2c0ec6487a5e05dec41),
adapted with the owner's permission: parameter overrides instead of fixture
files, reduced to the DUAL_PORT and SINGLE_PORT shapes.
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

# Every block lints these; the consumers' shapes are listed per block.
LEGAL_COMMON = (
    {"WIDTH": 16, "ADDR_W": 3, "LANES": 2},  # the regression shape
    {"WIDTH": 36, "ADDR_W": 4, "LANES": 4},  # 9-bit lanes
    {"WIDTH": 1, "ADDR_W": 1, "LANES": 1},   # the smallest RAM
)
LEGAL = {
    "common_sdp_ram": (
        {"WIDTH": 34, "ADDR_W": 9, "LANES": 1},  # dll receive buffer
        {"WIDTH": 32, "ADDR_W": 8, "LANES": 1},  # dll replay buffer
        *LEGAL_COMMON),
    "common_sync_ram": (
        {"WIDTH": 32, "ADDR_W": 10, "LANES": 4},  # rc_tl host memory
        *LEGAL_COMMON),
}
ILLEGAL = (
    ({"WIDTH": 0}, "WIDTH must be at least 1"),
    ({"ADDR_W": 0}, "ADDR_W must be at least 1"),
    ({"LANES": 0}, "LANES must be at least 1 and divide WIDTH"),
    ({"WIDTH": 8, "LANES": 3}, "LANES must be at least 1 and divide WIDTH"),
    ({"WIDTH": 16, "LANES": 4}, "the Intel backend needs one lane or 8- or 9-bit lanes"),
)
# The only messages a legal backend lint may print, per block: (rule,
# detail). Counted: the mode's absent pins in both instances, byteena_a in
# the one-lane instance only.
EXPECTED = {
    "common_sdp_ram": Counter({("DECLFILENAME", None): 1, ("PINCONNECTEMPTY", "byteena_a"): 1,
                               ("PINCONNECTEMPTY", "data_b"): 2, ("PINCONNECTEMPTY", "q_a"): 2,
                               ("PINCONNECTEMPTY", "eccstatus"): 2}),
    "common_sync_ram": Counter({("DECLFILENAME", None): 1, ("PINCONNECTEMPTY", "byteena_a"): 1,
                                ("PINCONNECTEMPTY", "q_b"): 2,
                                ("PINCONNECTEMPTY", "eccstatus"): 2}),
}

# The stub as top: the shape each backend requests, then one rule broken at
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
    ({"operation_mode": '"BIDIR_DUAL_PORT"'}, "operation_mode must be DUAL_PORT or SINGLE_PORT"),
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
    ({"rdcontrol_reg_b": '"CLOCK1"'},
     "port B must register on CLOCK0 (single clock), or stay unset in SINGLE_PORT"),
    ({"read_during_write_mode_mixed_ports": '"DONT_CARE"'},
     "read_during_write_mode_mixed_ports must be OLD_DATA, or unset in SINGLE_PORT"),
    ({"read_during_write_mode_port_a": '"OLD_DATA"'},
     "read_during_write_mode_port_a must be OLD_DATA in SINGLE_PORT, unset in DUAL_PORT"),
    ({"clock_enable_input_b": '"NORMAL"'},
     "clock enables must be BYPASS (port B's unset in SINGLE_PORT)"),
    ({"init_file": '"image.mif"'}, "init_file must be UNUSED"),
    ({"power_up_uninitialized": '"TRUE"'}, "power_up_uninitialized must be FALSE"),
)
# The SINGLE_PORT shape (common_sync_ram): port A only, same-port OLD_DATA,
# 8-bit byte enables as the rc_tl host memory uses them.
STUB_SINGLE_BASE = {
    "operation_mode": '"SINGLE_PORT"', "intended_device_family": '"Cyclone IV E"',
    "ram_block_type": '"M9K"', "width_a": 32, "widthad_a": 4, "numwords_a": 16,
    "width_byteena_a": 4, "byte_size": 8, "read_during_write_mode_port_a": '"OLD_DATA"',
    "clock_enable_input_a": '"BYPASS"', "clock_enable_output_a": '"BYPASS"',
}
STUB_SINGLE_REJECTS = (
    ({"intended_device_family": '"MAX 10"'}, "intended_device_family must be Cyclone IV E"),
    ({"ram_block_type": '"AUTO"'}, "ram_block_type must be M9K"),
    ({"numwords_a": 15}, "numwords_a must be 2**widthad_a and width_a at least 1"),
    ({"width_b": 32}, "SINGLE_PORT leaves the port B geometry unset"),
    ({"numwords_b": 16}, "SINGLE_PORT leaves the port B geometry unset"),
    ({"byte_size": 9},
     "byte_size, when given, must be 8 or 9 and width_a = width_byteena_a * byte_size"),
    ({"outdata_reg_a": '"CLOCK0"'}, "outputs must be UNREGISTERED (one-edge read latency)"),
    ({"address_reg_b": '"CLOCK0"'},
     "port B must register on CLOCK0 (single clock), or stay unset in SINGLE_PORT"),
    ({"read_during_write_mode_mixed_ports": '"OLD_DATA"'},
     "read_during_write_mode_mixed_ports must be OLD_DATA, or unset in SINGLE_PORT"),
    ({"read_during_write_mode_port_a": '"NEW_DATA_NO_NBE_READ"'},
     "read_during_write_mode_port_a must be OLD_DATA in SINGLE_PORT, unset in DUAL_PORT"),
    ({"read_during_write_mode_port_a": '"DONT_CARE"'},
     "read_during_write_mode_port_a must be OLD_DATA in SINGLE_PORT, unset in DUAL_PORT"),
    ({"clock_enable_output_a": '"NORMAL"'},
     "clock enables must be BYPASS (port B's unset in SINGLE_PORT)"),
    ({"clock_enable_input_b": '"BYPASS"'},
     "clock enables must be BYPASS (port B's unset in SINGLE_PORT)"),
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
        for params in LEGAL[block]:
            code, msgs, text = lint(block, sources, params)
            got = Counter(key(r, t) for r, t in msgs)
            assert code == 0 and got == EXPECTED[block], (
                f"{block} {params}: expected exactly {dict(EXPECTED[block])}, got exit "
                f"{code}:\n{text}")
            checked += 1
        for params, message in ILLEGAL:
            code, msgs, text = lint(block, sources, params)
            want = f"{block}: {message}"
            assert any(r == "USERFATAL" and want in t for r, t in msgs), (
                f"{block} {params}: expected an elaboration failure naming '{want}', got exit "
                f"{code}:\n{text}")
            checked += 1
    assert set(BLOCKS) == set(LEGAL), f"backends {BLOCKS} need their legal shapes listed"
    assert checked == sum(len(LEGAL[b]) + len(ILLEGAL) for b in BLOCKS)


@cocotb.test()
async def stub_rejects(dut):
    """The stub accepts each backend shape and rejects each rule alone."""
    del dut
    for base, rejects in ((STUB_BASE, STUB_REJECTS), (STUB_SINGLE_BASE, STUB_SINGLE_REJECTS)):
        code, msgs, text = lint("altsyncram", MODELS, base)
        assert code == 0 and not msgs, f"stub rejects the backend shape {base}:\n{text}"
        for override, message in rejects:
            code, msgs, text = lint("altsyncram", MODELS, {**base, **override})
            fatals = [t for r, t in msgs if r == "USERFATAL"]
            assert len(fatals) == 1 and fatals[0].endswith(f"altsyncram stub: {message}"), (
                f"{override}: expected one failure, '{message}', got:\n{text}")


# The sky130 (asic) backends: lint against the DV-only macro model.
SKY130 = backends.load(REPO_ROOT / "cfg")["asic"]
SKY130_MODELS = backends.dv_models(SKY130, SRC)
SKY130_BLOCKS = tuple(sorted(p.stem for p in RTL.glob("common_*.sv")
                             if backends.backend_file(p, SKY130).is_file()))
SKY130_LEGAL = (
    {"WIDTH": 34, "ADDR_W": 9, "LANES": 1},   # dll receive buffer: two macro rows
    {"WIDTH": 32, "ADDR_W": 8, "LANES": 1},   # dll replay buffer: one macro
    {"WIDTH": 72, "ADDR_W": 7, "LANES": 1},   # the macro regression shape: two across, low words
    {"WIDTH": 70, "ADDR_W": 10, "LANES": 1},  # two macros across, four rows
    {"WIDTH": 16, "ADDR_W": 3, "LANES": 2},   # the flop regression shape
    {"WIDTH": 96, "ADDR_W": 3, "LANES": 12},  # a lane-enabled table: flops
    {"WIDTH": 1, "ADDR_W": 1, "LANES": 1},    # the smallest RAM: flops
)
SKY130_ILLEGAL = (
    ({"WIDTH": 0}, "WIDTH must be at least 1"),
    ({"ADDR_W": 0}, "ADDR_W must be at least 1"),
    ({"LANES": 0}, "LANES must be at least 1 and divide WIDTH"),
    ({"WIDTH": 8, "LANES": 3}, "LANES must be at least 1 and divide WIDTH"),
)
# Macro selection: a one-lane RAM of at least 8192 bits uses macros, every
# other RAM the portable array. Each shape: (params, uses macros).
SKY130_SELECT = (
    ({"WIDTH": 32, "ADDR_W": 8, "LANES": 1}, True),    # exactly 8192 bits
    ({"WIDTH": 72, "ADDR_W": 7, "LANES": 1}, True),    # 9216 bits
    ({"WIDTH": 34, "ADDR_W": 9, "LANES": 1}, True),
    ({"WIDTH": 63, "ADDR_W": 7, "LANES": 1}, False),   # 8064 bits, just below
    ({"WIDTH": 32, "ADDR_W": 7, "LANES": 1}, False),   # 4096 bits
    ({"WIDTH": 64, "ADDR_W": 8, "LANES": 2}, False),   # large, but two lanes
    ({"WIDTH": 16, "ADDR_W": 3, "LANES": 1}, False),
)
_MODMISSING_RE = re.compile(r"Cannot find file containing module: 'sky130_sram_1rw1r_64x256_8'")


@cocotb.test()
async def sky130_lint(dut):
    """Legal geometries lint with only DECLFILENAME (the backend file holds
    its portable file's module); illegal ones fail with the block's own
    message."""
    del dut
    assert SKY130_BLOCKS, "no shared RAM has an asic backend file"
    checked = 0
    for block in SKY130_BLOCKS:
        sources = [backends.backend_file(RTL / f"{block}.sv", SKY130), *SKY130_MODELS]
        for params in SKY130_LEGAL:
            code, msgs, text = lint(block, sources, params)
            assert code == 0 and [r for r, _ in msgs] == ["DECLFILENAME"], (
                f"{block} {params}: expected exactly DECLFILENAME, got exit {code}:\n{text}")
            checked += 1
        for params, message in SKY130_ILLEGAL:
            code, msgs, text = lint(block, sources, {"LANES": 1, **params})
            want = f"{block}: {message}"
            assert any(r == "USERFATAL" and want in t for r, t in msgs), (
                f"{block} {params}: expected an elaboration failure naming '{want}', got exit "
                f"{code}:\n{text}")
            checked += 1
    assert checked == len(SKY130_BLOCKS) * (len(SKY130_LEGAL) + len(SKY130_ILLEGAL))


@cocotb.test()
async def sky130_select(dut):
    """Macros only where the selection rule says: without the macro model,
    a macro shape fails elaboration naming the missing macro, and every
    other shape lints with only DECLFILENAME."""
    del dut
    for params, macros in SKY130_SELECT:
        src = backends.backend_file(RTL / "common_sdp_ram.sv", SKY130)
        code, msgs, text = lint("common_sdp_ram", [src], params)
        missing = any(r == "MODMISSING" and _MODMISSING_RE.search(t) for r, t in msgs)
        if macros:
            assert code != 0 and missing, f"{params}: expected macros, got exit {code}:\n{text}"
        else:
            assert code == 0 and [r for r, _ in msgs] == ["DECLFILENAME"], (
                f"{params}: expected the portable array, got exit {code}:\n{text}")
