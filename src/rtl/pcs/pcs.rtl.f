// pcie_pcs: synthesizable sources only (wiki/src/rtl/pcs/MAS.md). The
// vocabulary, the crossing cells and the shared package, the IP package,
// the blocks, then the top.
-F ../common/common_macros.rtl.f
-F ../common/common_cdc_rst.rtl.f
-F ../common/common_cdc_sync2.rtl.f
-F ../common/common_cdc_pulse.rtl.f
-F ../common/common_cdc_fifo.rtl.f
-F ../common/common_pkg.rtl.f
pcie_pcs_pkg.sv
pcie_pcs_enc8b10b_sym.sv
pcie_pcs_enc8b10b.sv
pcie_pcs_dec8b10b.sv
pcie_pcs_align.sv
pcie_pcs_elastic.sv
pcie_pcs.sv
