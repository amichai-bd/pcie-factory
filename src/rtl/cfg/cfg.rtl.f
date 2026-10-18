// pcie_cfg: synthesizable sources only: the shared vocabulary, the RAM,
// the IP package, the blocks, then the top (wiki/src/rtl/cfg/MAS.md#files).
-F ../common/common_macros.rtl.f
-F ../common/ram/common_sdp_ram.rtl.f
pcie_cfg_pkg.sv
pcie_cfg_regs.sv
pcie_cfg_err.sv
pcie_cfg_bar2.sv
pcie_cfg_msg.sv
pcie_cfg.sv
