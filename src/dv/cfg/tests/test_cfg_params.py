"""Parameter limits of pcie_cfg (wiki/src/arch/cfg/HAS.md#parameters).

A rejected parameter set cannot be simulated, so this test lints pcie_cfg
with Verilator, as `./build check` lint does, from its .rtl.f: every illegal
value must stop elaboration with its COMMON_ASSERT_PARAM message, and the
legal boundaries must lint clean. The simulated top only hosts the test.
"""

import subprocess

import cocotb

from build import sim
from build.ip import REPO_ROOT

RTL_F = REPO_ROOT / "src" / "rtl" / "cfg" / "cfg.rtl.f"
TOP = "pcie_cfg"

# Illegal overrides and the message each must produce; the IDs at their
# HAS widths, as sized literals.
ILLEGAL = (
    ({"BAR0_ADDR_W": 11}, "BAR0_ADDR_W must be 12..28"),
    ({"BAR0_ADDR_W": 29}, "BAR0_ADDR_W must be 12..28"),
    ({"MSIX_TABLE_SIZE": 0}, "MSIX_TABLE_SIZE must be 1..64"),
    ({"MSIX_TABLE_SIZE": 65}, "MSIX_TABLE_SIZE must be 1..64"),
    ({"VENDOR_ID": "16'hFFFF"}, "VENDOR_ID must not be 16'hFFFF"),
    ({"DEVICE_ID": "16'hFFFF"}, "DEVICE_ID must not be 16'hFFFF"),
    ({"SUBSYS_VENDOR_ID": "16'hFFFF"}, "SUBSYS_VENDOR_ID must not be 16'hFFFF"),
    ({"SUBSYS_ID": "16'hFFFF"}, "SUBSYS_ID must not be 16'hFFFF"),
)
# Legal boundaries.
LEGAL = (
    {"BAR0_ADDR_W": 12, "MSIX_TABLE_SIZE": 1},
    {"BAR0_ADDR_W": 28, "MSIX_TABLE_SIZE": 64, "VENDOR_ID": "16'hFFFE",
     "DEVICE_ID": "16'hFFFE", "SUBSYS_VENDOR_ID": "16'hFFFE", "SUBSYS_ID": "16'hFFFE",
     "CLASS_CODE": "24'hFFFFFF", "REVISION_ID": "8'hFF"},
    {"MSIX_TABLE_SIZE": 33},
)


def lint(params: dict[str, int | str]) -> subprocess.CompletedProcess:
    cmd = [str(sim.find_verilator()), "--lint-only", "-Wall", "--top-module", TOP,
           "-F", str(RTL_F)]
    cmd += [f"-G{k}={v}" for k, v in params.items()]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=120, check=False)


@cocotb.test()
async def param_limits(dut):
    """Illegal parameters fail elaboration with their message; the legal
    boundaries lint clean."""
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
