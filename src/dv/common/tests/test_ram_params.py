"""Parameter limits of the shared RAM family (wiki/src/dv/common/ram/common_*ram*.md).

A rejected parameter set cannot be simulated, so this test lints each RAM
block with Verilator, as `./build check` lint does, from its own .rtl.f:
every illegal WIDTH, ADDR_W or LANES must stop elaboration with the block's
own COMMON_ASSERT_PARAM message, and the smallest legal geometry and one
lane per bit must lint clean. The simulated top only hosts the test.
"""

import subprocess

import cocotb

from build import sim
from build.ip import REPO_ROOT

RTL = REPO_ROOT / "src" / "rtl" / "common" / "ram"
BLOCKS = ("common_sync_ram", "common_sync_ram_held", "common_sync_ram_wt", "common_sdp_ram",
          "common_tdp_ram")

# Illegal overrides and the message each must produce.
ILLEGAL = (
    ({"WIDTH": 0}, "WIDTH must be at least 1"),
    ({"ADDR_W": 0}, "ADDR_W must be at least 1"),
    ({"LANES": 0}, "LANES must be at least 1 and divide WIDTH"),
    ({"WIDTH": 8, "LANES": 3}, "LANES must be at least 1 and divide WIDTH"),
)
# Legal limits: the smallest RAM, and a one-bit lane per bit.
LEGAL = ({"WIDTH": 1, "ADDR_W": 1, "LANES": 1}, {"WIDTH": 8, "ADDR_W": 1, "LANES": 8})


def lint(block: str, params: dict[str, int]) -> subprocess.CompletedProcess:
    cmd = [str(sim.find_verilator()), "--lint-only", "-Wall", "--top-module", block,
           "-F", str(RTL / f"{block}.rtl.f")]
    cmd += [f"-G{k}={v}" for k, v in params.items()]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=120, check=False)


@cocotb.test()
async def param_limits(dut):
    """Illegal geometries fail elaboration with the block's message; the
    legal limits lint clean."""
    del dut
    checked = 0
    for block in BLOCKS:
        for params, message in ILLEGAL:
            out = lint(block, params)
            text = out.stdout + out.stderr
            want = f"{block}: {message}"
            assert out.returncode != 0 and "USERFATAL" in text and want in text, (
                f"{block} {params}: expected an elaboration failure naming '{want}', got exit "
                f"{out.returncode}:\n{text}")
            checked += 1
        for params in LEGAL:
            out = lint(block, params)
            assert out.returncode == 0, f"{block} {params}: legal limit fails lint:\n" \
                + out.stdout + out.stderr
            checked += 1
    assert checked == len(BLOCKS) * (len(ILLEGAL) + len(LEGAL))
