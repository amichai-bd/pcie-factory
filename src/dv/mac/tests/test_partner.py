"""Qualification of the link partner (wiki/src/dv/mac/DV_PLAN.md).

The full arc sweep of the architecture model: in each port role of the
device, the partner model must drive the device model across every arc of
the HAS arc list (wiki/src/arch/mac/MODEL.md#arc-sweep). It is the proof
that the partner the LTSSM tests use can reach 100% arc coverage. It runs
here, at level 2, to keep the ci-gate perf run short; pcie_mac (the shell)
is only clocked so the entry builds and runs like any other.
"""

import sys
from pathlib import Path

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles

sys.path.append(str(Path(__file__).resolve().parents[3] / "arch" / "mac"))
import pcie_mac_arch as arch  # noqa: E402
import pcie_mac_sweep as sweep  # noqa: E402


@cocotb.test()
async def partner_arc_sweep(dut):
    """L2: the partner drives all arcs of the device model in both roles."""
    cocotb.start_soon(Clock(dut.clk_i, 8, unit="ns").start())
    dut.rst_i.value = 1
    await ClockCycles(dut.clk_i, 4)
    sweep.use(arch)
    for role in arch.ROLES:
        pairs = sweep.run(role)
        missed = sweep.missed(pairs)
        dut._log.info("role %s: %d/%d arcs, %d model cycles", role,
                      len(arch.ARCS) - len(missed), len(arch.ARCS),
                      sum(p.now for p in pairs))
        assert not missed, f"role {role}: arcs not reached: {missed}"
