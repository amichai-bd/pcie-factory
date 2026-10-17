# pcie_link power grid for ./build asic on sky130hd (wiki/src/asic/link/ASIC.md,
# Memories): the platform's standard-cell grid, without its macro grids.
#
# Provenance: adapted from OpenROAD-flow-scripts
# flow/platforms/sky130hd/pdn.tcl (commit d4b96380b10e, BSD-3-Clause, the
# ORFS build and run scripts license). Kept: the global connections and the
# standard-cell grid. Dropped: the two macro grids. The dll buffer SRAM
# macros' vdd/gnd pins are small met3 pads that the platform patterns do not
# name, so the macros are on no power net and pdngen cannot build a macro
# grid for them (PDN-0217); they stay unpowered, a stated limitation.

# Global connections
add_global_connection -net {VDD} -inst_pattern {.*} -pin_pattern {^VDD$} -power
add_global_connection -net {VDD} -inst_pattern {.*} -pin_pattern {^VDDPE$}
add_global_connection -net {VDD} -inst_pattern {.*} -pin_pattern {^VDDCE$}
add_global_connection -net {VDD} -inst_pattern {.*} -pin_pattern {VPWR}
add_global_connection -net {VDD} -inst_pattern {.*} -pin_pattern {VPB}
add_global_connection -net {VSS} -inst_pattern {.*} -pin_pattern {^VSS$} -ground
add_global_connection -net {VSS} -inst_pattern {.*} -pin_pattern {^VSSE$}
add_global_connection -net {VSS} -inst_pattern {.*} -pin_pattern {VGND}
add_global_connection -net {VSS} -inst_pattern {.*} -pin_pattern {VNB}
global_connect

# Voltage domain
set_voltage_domain -name {CORE} -power {VDD} -ground {VSS}

# Standard-cell grid (as the platform)
define_pdn_grid -name {grid} -voltage_domains {CORE} -pins {met5}
add_pdn_stripe -grid {grid} -layer {met1} -width {0.48} -pitch {5.44} -offset {0} -followpins
add_pdn_stripe -grid {grid} -layer {met4} -width {1.600} -pitch {27.140} -offset {13.570}
add_pdn_stripe -grid {grid} -layer {met5} -width {1.600} -pitch {27.200} -offset {13.600}
add_pdn_connect -grid {grid} -layers {met1 met4}
add_pdn_connect -grid {grid} -layers {met4 met5}
