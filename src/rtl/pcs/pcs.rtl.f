// pcie_pcs: synthesizable sources only (sub-IPs, then the top). The other
// sub-IP and common_ filelists join in wave 2 (wiki/src/rtl/pcs/MAS.md).
-F ../pcs_enc8b10b/pcs_enc8b10b.rtl.f
-F ../pcs_align/pcs_align.rtl.f
pcie_pcs.sv
