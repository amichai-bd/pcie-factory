"""Parameter limits of pcie_rc_tl (wiki/src/rtl/rc_tl/MAS.md#parameters).

A rejected parameter set cannot be simulated, so this test lints pcie_rc_tl
with Verilator, as `./build check` lint does, from its .rtl.f as the build
resolves it: every illegal value must stop elaboration with its
COMMON_ASSERT_PARAM message, and the legal boundaries must lint clean. The simulated top only hosts the test.
"""

import subprocess

import cocotb

from build import filelist, sim
from build.ip import REPO_ROOT

RTL_F = REPO_ROOT / "src" / "rtl" / "rc_tl" / "rc_tl.rtl.f"
TOP = "pcie_rc_tl"

# Illegal overrides and the message each must produce; one or more per
# COMMON_ASSERT_PARAM, both ends of each range, the overlap both ways.
ILLEGAL = (
    ({"FC_NPH": 0}, "FC_NPH must be 1 to 127"),
    ({"FC_NPH": 128}, "FC_NPH must be 1 to 127"),
    ({"CPL_TIMEOUT_DIV": 0}, "CPL_TIMEOUT_DIV must be 1 to 1024"),
    ({"CPL_TIMEOUT_DIV": 1025}, "CPL_TIMEOUT_DIV must be 1 to 1024"),
    ({"HOST_MEM_ADDR_W": 7}, "HOST_MEM_ADDR_W must be 8 to 16"),
    ({"HOST_MEM_ADDR_W": 17}, "HOST_MEM_ADDR_W must be 8 to 16"),
    ({"HOST_MEM_BASE": "32'h0000_0800"}, "HOST_MEM_BASE must be aligned to the host memory size"),
    ({"HOST_MEM_ADDR_W": 16, "HOST_MEM_BASE": "32'h0001_8000"},
     "HOST_MEM_BASE must be aligned to the host memory size"),
    ({"MSI_ADDR": "32'hFEE0_0800"}, "MSI_ADDR must be 4 KiB aligned"),
    ({"MSI_ADDR": "32'hFEE0_0004"}, "MSI_ADDR must be 4 KiB aligned"),
    # The MSI window on host memory, inside a larger host memory, and host
    # memory inside the MSI window.
    ({"MSI_ADDR": "32'h0000_0000"}, "the MSI window must not overlap host memory"),
    ({"HOST_MEM_ADDR_W": 16, "HOST_MEM_BASE": "32'h0001_0000", "MSI_ADDR": "32'h0001_F000"},
     "the MSI window must not overlap host memory"),
    ({"HOST_MEM_ADDR_W": 8, "HOST_MEM_BASE": "32'hFEE0_0F00"},
     "the MSI window must not overlap host memory"),
    ({"VENDOR_ID": "16'hFFFF"}, "VENDOR_ID must not be FFFF"),
    ({"DEVICE_ID": "16'hFFFF"}, "DEVICE_ID must not be FFFF"),
)
# Legal boundaries: each range's ends, regions that touch but do not
# overlap, the largest IDs.
LEGAL = (
    {"FC_NPH": 1, "CPL_TIMEOUT_DIV": 1, "HOST_MEM_ADDR_W": 8},
    {"FC_NPH": 127, "CPL_TIMEOUT_DIV": 1024, "HOST_MEM_ADDR_W": 16},
    {"HOST_MEM_ADDR_W": 16, "HOST_MEM_BASE": "32'h0001_0000", "MSI_ADDR": "32'h0002_0000"},
    {"HOST_MEM_ADDR_W": 8, "HOST_MEM_BASE": "32'h0000_1000", "MSI_ADDR": "32'h0000_0000"},
    {"HOST_MEM_ADDR_W": 12, "HOST_MEM_BASE": "32'hFFFF_F000", "MSI_ADDR": "32'hFFFF_E000"},
    {"VENDOR_ID": "16'hFFFE", "DEVICE_ID": "16'hFFFE", "REVISION_ID": "8'hFF"},
)


def lint(params: dict[str, int | str]) -> subprocess.CompletedProcess:
    # The filelist resolved as the build resolves it: a shared block reached
    # through two -F includes (common_pkg) is listed once.
    flat = filelist.resolve(RTL_F)
    cmd = [str(sim.find_verilator()), "--lint-only", "-Wall", "--top-module", TOP]
    cmd += [f"+incdir+{d}" for d in flat.incdirs]
    cmd += [f"+define+{k}" if v is None else f"+define+{k}={v}" for k, v in flat.defines.items()]
    cmd += [str(f) for f in flat.sources]
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
