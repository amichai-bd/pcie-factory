# pcie_link ORFS design knobs on sky130hd (wiki/bkm/pd.md,
# wiki/src/asic/link/ASIC.md). ./build asic generates the platform, sources,
# include dirs, defines and SDC_FILE from rtl/link.rtl.f and pcie_link.sdc;
# never list sources here.

export DESIGN_NAME = pcie_link

# The RTL uses package types in its port lists and typed parameters; read it
# with the slang SystemVerilog frontend (built into the ORFS Yosys) instead
# of Yosys's own partial SV parser.
export SYNTH_HDL_FRONTEND = slang

# Size the core from the cell area, with room for two clock trees and
# resizing buffers, as for pcie_phy. The dll replay and receive buffers are
# behavioral common_sdp_ram instances, mapped to flops (no memory macro is
# specified), and dominate the cell area. ORFS stops synthesis on any
# inferred memory above SYNTH_MEMORY_MAX_BITS (4096 by default); the largest
# here is the receive buffer, 512 x 34 = 17408 bits at the default credits
# (the replay buffer is 256 x 32 = 8192), so the limit is raised to exactly
# that, and a larger memory still fails the flow.
export SYNTH_MEMORY_MAX_BITS = 17408
export CORE_UTILIZATION = 30
export PLACE_DENSITY_LB_ADDON = 0.20
export TNS_END_PERCENT = 100
