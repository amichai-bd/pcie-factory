// pcie_phy: synthesizable sources only. The two stacked IPs, each through
// its own filelist, then the stack top (wiki/src/rtl/phy/MAS.md).
-F ../mac/mac.rtl.f
-F ../pcs/pcs.rtl.f
pcie_phy.sv
