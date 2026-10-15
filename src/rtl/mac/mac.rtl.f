// pcie_mac: synthesizable sources only: the shared vocabulary and the
// design package, the IP package, the blocks, then the top
// (wiki/src/rtl/mac/MAS.md).
-F ../common/common_macros.rtl.f
-F ../common/common_pkg.rtl.f
pcie_mac_pkg.sv
pcie_mac_scrambler.sv
pcie_mac_os.sv
pcie_mac_frame.sv
pcie_mac_ltssm.sv
pcie_mac.sv
