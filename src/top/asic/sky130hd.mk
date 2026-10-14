# pcie_top ORFS design knobs on sky130hd (wiki/bkm/pd.md). ./build asic
# generates the platform, sources, include dirs, defines and SDC_FILE from
# rtl/top.rtl.f and pcie_top.sdc; never list sources here.

export DESIGN_NAME = pcie_top

# The RTL uses a package imported in the module header and typed localparams;
# read it with the slang SystemVerilog frontend (built into the ORFS Yosys)
# instead of Yosys's own partial SV parser.
export SYNTH_HDL_FRONTEND = slang

# Small block: size the core from the cell area, with room for CTS and
# resizing buffers.
export CORE_UTILIZATION = 30
export PLACE_DENSITY_LB_ADDON = 0.20
export TNS_END_PERCENT = 100
