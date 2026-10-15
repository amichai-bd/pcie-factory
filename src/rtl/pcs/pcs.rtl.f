// pcie_pcs: synthesizable sources only. Vocabulary and crossing cells, the
// sub-IPs, the package, then the top. The receive sub-IPs join as they are
// integrated (wiki/src/rtl/pcs/MAS.md).
-F ../common/common_macros.rtl.f
-F ../common/common_cdc_rst.rtl.f
-F ../common/common_cdc_sync2.rtl.f
-F ../pcs_enc8b10b/pcs_enc8b10b.rtl.f
pcie_pcs_pkg.sv
pcie_pcs.sv
