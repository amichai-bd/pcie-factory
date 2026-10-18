"""Credit-parameter limits of pcie_dll, FC_RELEASE included (wiki/src/arch/dll/HAS.md#parameters).

A rejected parameter set cannot be simulated, so this test lints the dll
with Verilator, as `./build check` lint does, from its .rtl.f: every
illegal FC_* value must stop elaboration with the shell's own
COMMON_ASSERT_PARAM message, and the legal boundaries must lint clean.
The simulated top only hosts the test.
"""

import subprocess

import cocotb

from build import sim
from build.ip import REPO_ROOT

RTL_F = REPO_ROOT / "src" / "rtl" / "dll" / "dll.rtl.f"
TOP = "pcie_dll"

# Illegal overrides and the message each must produce.
ILLEGAL = (
    ({"FC_PH": 128}, "FC_PH must be 0 or 1..127"),
    ({"FC_PD": 7}, "FC_PD must be 0 or 8..2047"),
    ({"FC_PD": 2048}, "FC_PD must be 0 or 8..2047"),
    ({"FC_NPH": 128}, "FC_NPH must be 0 or 1..127"),
    ({"FC_NPD": 1}, "FC_NPD must be 0 or 2..2047"),
    ({"FC_NPD": 2048}, "FC_NPD must be 0 or 2..2047"),
    ({"FC_CPLH": 128}, "FC_CPLH must be 0 or 1..127"),
    ({"FC_CPLD": 7}, "FC_CPLD must be 0 or 8..2047"),
    ({"FC_RELEASE": 2}, "FC_RELEASE must be 0 or 1"),
)
# Legal boundaries: the smallest finite values, all infinite, the largest;
# the release port with the smallest and with all-infinite credits.
LEGAL = (
    {"FC_PH": 1, "FC_PD": 8, "FC_NPH": 1, "FC_NPD": 2, "FC_CPLH": 1, "FC_CPLD": 8},
    {"FC_PH": 0, "FC_PD": 0, "FC_NPH": 0, "FC_NPD": 0, "FC_CPLH": 0, "FC_CPLD": 0},
    {"FC_PH": 1, "FC_PD": 8, "FC_NPH": 1, "FC_NPD": 2, "FC_CPLH": 1, "FC_CPLD": 8,
     "FC_RELEASE": 1},
    {"FC_PH": 0, "FC_PD": 0, "FC_NPH": 0, "FC_NPD": 0, "FC_CPLH": 0, "FC_CPLD": 0,
     "FC_RELEASE": 1},
    {"FC_PH": 127, "FC_PD": 2047, "FC_NPH": 127, "FC_NPD": 2047, "FC_CPLH": 127,
     "FC_CPLD": 2047},
)


def lint(params: dict[str, int]) -> subprocess.CompletedProcess:
    cmd = [str(sim.find_verilator()), "--lint-only", "-Wall", "--top-module", TOP,
           "-F", str(RTL_F)]
    cmd += [f"-G{k}={v}" for k, v in params.items()]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=120, check=False)


@cocotb.test()
async def param_limits(dut):
    """Illegal credit parameters fail elaboration with their message; the
    legal boundaries lint clean."""
    del dut
    checked = 0
    for params, message in ILLEGAL:
        out = lint(params)
        text = out.stdout + out.stderr
        assert out.returncode != 0 and "USERFATAL" in text and message in text, (
            f"{params}: expected an elaboration failure naming '{message}', got exit "
            f"{out.returncode}:\n{text}")
        checked += 1
    for params in LEGAL:
        out = lint(params)
        assert out.returncode == 0, f"{params}: legal limit fails lint:\n" + out.stdout + out.stderr
        checked += 1
    assert checked == len(ILLEGAL) + len(LEGAL)
