// pcie_link: synthesizable sources only. The vocabulary, the stacked IPs,
// each through its own filelist (the dll, the phy and the GPIO adapter of
// the board build), then the stack top (wiki/src/rtl/link/MAS.md).
-F ../common/common_macros.rtl.f
-F ../dll/dll.rtl.f
-F ../phy/phy.rtl.f
-F ../gpio/gpio.rtl.f
pcie_link.sv
