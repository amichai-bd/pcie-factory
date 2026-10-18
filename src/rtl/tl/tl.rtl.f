// pcie_tl: synthesizable sources only: the shared vocabulary, the shared
// RAM, the IP package, the blocks, then the top (wiki/src/rtl/tl/MAS.md#files).
-F ../common/common_macros.rtl.f
-F ../common/ram/common_sdp_ram.rtl.f
pcie_tl_pkg.sv
pcie_tl_fifo.sv
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
