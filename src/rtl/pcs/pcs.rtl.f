// pcie_pcs: synthesizable sources only. Vocabulary and crossing cells, the
// package, then the top; sub-IP filelists join as they are integrated
// (wiki/src/rtl/pcs/MAS.md).
-F ../common/common_macros.rtl.f
-F ../common/common_cdc_rst.rtl.f
-F ../common/common_cdc_sync2.rtl.f
pcie_pcs_pkg.sv
pcie_pcs.sv
