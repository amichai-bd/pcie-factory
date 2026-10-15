// pcie_pcs: synthesizable sources only. Vocabulary and crossing cells, the
// sub-IPs, the package, then the top (wiki/src/rtl/pcs/MAS.md).
-F ../common/common_macros.rtl.f
-F ../common/common_cdc_sync2.rtl.f
-F ../common/common_cdc_pulse.rtl.f
-F ../pcs_enc8b10b/pcs_enc8b10b.rtl.f
-F ../pcs_dec8b10b/pcs_dec8b10b.rtl.f
-F ../pcs_align/pcs_align.rtl.f
-F ../pcs_elastic/pcs_elastic.rtl.f
pcie_pcs_pkg.sv
pcie_pcs.sv
