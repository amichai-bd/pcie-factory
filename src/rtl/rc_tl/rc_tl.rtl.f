// pcie_rc_tl: synthesizable sources only: the shared vocabulary, the shared
// package (the tl-seq types), the IP's package, the leaf blocks, then the
// top (wiki/src/rtl/rc_tl/MAS.md#files). The other leaf blocks join with
// #373 and #375.
-F ../common/common_macros.rtl.f
-F ../common/common_pkg.rtl.f
pcie_rc_tl_pkg.sv
pcie_rc_tl_hdr.sv
pcie_rc_tl_cmd.sv
pcie_rc_tl.sv
