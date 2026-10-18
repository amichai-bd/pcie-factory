# pcie_core ORFS design knobs on sky130hd (wiki/bkm/pd.md,
# wiki/src/asic/core/ASIC.md). ./build asic generates the platform, sources,
# include dirs, defines and SDC_FILE from rtl/core.rtl.f (with the asic
# source backends of cfg/backends.yaml) and pcie_core.sdc, and sets
# ASIC_INPUT_DIR to this folder; never list sources here.

export DESIGN_NAME = pcie_core

# The RTL uses package types in its port lists and typed parameters; read it
# with the slang SystemVerilog frontend (built into the ORFS Yosys) instead
# of Yosys's own partial SV parser.
export SYNTH_HDL_FRONTEND = slang

# RAMs use the sky130 backend of common_sdp_ram (common_sdp_ram_sky130.sv).
# The dll replay (256 x 32) and receive (512 x 34) buffers and the tl
# requester-completion data queue (256 x 32) are at least half a macro, so
# they take four sky130_sram_1rw1r_64x256_8 OpenRAM macros from the pinned
# ORFS image's sky130ram platform folder (the dll ones as in pcie_link); the
# smaller tl queues and the lane-enabled cfg MSI-X table stay flops.
# The macro's abstract, timing and layout views:
SKY130_SRAM = $(PLATFORM_HOME)/sky130ram/sky130_sram_1rw1r_64x256_8/sky130_sram_1rw1r_64x256_8
export ADDITIONAL_LEFS = $(SKY130_SRAM).lef
export ADDITIONAL_LIBS = $(SKY130_SRAM)_TT_1p8V_25C.lib
export ADDITIONAL_GDS = $(SKY130_SRAM).gds
# Power: the platform's standard-cell grid without its macro grids
# (pdn.tcl), as for pcie_link: the macros' vdd/gnd pins are met3 pads the
# platform grid does not reach, so the macros stay off the power grid (an
# estimation limitation, wiki/src/asic/core/ASIC.md).
export PDN_TCL = $(ASIC_INPUT_DIR)/pdn.tcl

# Size the core from the instance area (macros included), with room for
# two clock trees and resizing buffers. 40% rather than pcie_link's 30%: the
# first 8 ns run (30%, 8.5 mm2 core) ran out of the 6 GB container in
# detailed route.
export CORE_UTILIZATION = 40
export PLACE_DENSITY_LB_ADDON = 0.20
# Timing repair works on ORFS's default share of the failing endpoints,
# not all of them (pcie_link's TNS_END_PERCENT 100): with every endpoint
# repaired, the first 8 ns run grew from 55k cells after synthesis to 157k
# after global route, which detailed route could not hold in 6 GB.
