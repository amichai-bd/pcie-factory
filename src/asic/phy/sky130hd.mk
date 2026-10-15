# pcie_phy ORFS design knobs on sky130hd (wiki/bkm/pd.md,
# wiki/src/asic/phy/ASIC.md). ./build asic generates the platform, sources,
# include dirs, defines and SDC_FILE from rtl/phy.rtl.f and pcie_phy.sdc;
# never list sources here.

export DESIGN_NAME = pcie_phy

# The RTL uses package types in its port lists and typed parameters; read it
# with the slang SystemVerilog frontend (built into the ORFS Yosys) instead
# of Yosys's own partial SV parser.
export SYNTH_HDL_FRONTEND = slang

# Size the core from the cell area, with room for two clock trees and
# resizing buffers.
export CORE_UTILIZATION = 30
export PLACE_DENSITY_LB_ADDON = 0.20
export TNS_END_PERCENT = 100
