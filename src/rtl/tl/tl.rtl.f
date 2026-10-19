// pcie_tl: synthesizable sources only: the shared vocabulary, the shared
// package, the shared queue, the IP package, the blocks, then the top
// (wiki/src/rtl/tl/MAS.md#files).
-F ../common/common_macros.rtl.f
-F ../common/common_pkg.rtl.f
-F ../common/ram/common_sdp_ram_fifo.rtl.f
pcie_tl_pkg.sv
pcie_tl_rx.sv
pcie_tl_rx_q.sv
pcie_tl_cq.sv
pcie_tl_reg.sv
pcie_tl_cplf.sv
pcie_tl_cpl.sv
pcie_tl_txq.sv
pcie_tl_tx.sv
pcie_tl_trk.sv
pcie_tl_rc.sv
pcie_tl.sv
