// pcie_core: synthesizable sources only. The three stacked IPs, each through
// its own filelist, then the stack top (wiki/src/rtl/core/MAS.md#files).
-F ../link/link.rtl.f
-F ../tl/tl.rtl.f
-F ../cfg/cfg.rtl.f
pcie_core.sv
