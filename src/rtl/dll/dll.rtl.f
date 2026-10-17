// pcie_dll: synthesizable sources only: the shared vocabulary, the IP
// package, the blocks, then the top (wiki/src/rtl/dll/MAS.md#files).
-F ../common/common_macros.rtl.f
-F ../common/ram/common_sdp_ram.rtl.f
pcie_dll_pkg.sv
pcie_dll_dllp_rx.sv
pcie_dll_ctrl.sv
pcie_dll_fc.sv
pcie_dll_tlp_tx.sv
pcie_dll_tx.sv
pcie_dll.sv
