# pcie_link ORFS design knobs on sky130hd (wiki/bkm/pd.md,
# wiki/src/asic/link/ASIC.md). ./build asic generates the platform, sources,
# include dirs, defines and SDC_FILE from rtl/link.rtl.f (with the asic
# source backends of cfg/backends.yaml) and pcie_link.sdc; never list
# sources here.

export DESIGN_NAME = pcie_link

# The RTL uses package types in its port lists and typed parameters; read it
# with the slang SystemVerilog frontend (built into the ORFS Yosys) instead
# of Yosys's own partial SV parser.
export SYNTH_HDL_FRONTEND = slang

# The dll replay (256 x 32) and receive (512 x 34) buffers use the sky130
# backend of common_sdp_ram (common_sdp_ram_sky130.sv): three
# sky130_sram_1rw1r_64x256_8 OpenRAM macros from the pinned ORFS image's
# sky130ram platform folder. Their abstract, timing and layout views:
SKY130_SRAM = $(PLATFORM_HOME)/sky130ram/sky130_sram_1rw1r_64x256_8/sky130_sram_1rw1r_64x256_8
export ADDITIONAL_LEFS = $(SKY130_SRAM).lef
export ADDITIONAL_LIBS = $(SKY130_SRAM)_TT_1p8V_25C.lib
export ADDITIONAL_GDS = $(SKY130_SRAM).gds
# Power: the platform grid. The macros' vdd/gnd pins are 0.49 um met3
# pads under the array, which the platform's met4/met5 grid does not reach
# and its pin patterns do not name, so the macros stay off the power grid
# (an estimation limitation, wiki/src/asic/link/ASIC.md).

# Size the core from the instance area (macros included), with room for
# two clock trees and resizing buffers, as for pcie_phy.
export CORE_UTILIZATION = 30
export PLACE_DENSITY_LB_ADDON = 0.20
export TNS_END_PERCENT = 100
