// pcie_rc_tl: synthesizable sources only: the shared vocabulary, the shared
// package (the tl-seq types), the IP's package, the leaf blocks, then the
// top (wiki/src/rtl/rc_tl/MAS.md#files).
-F ../common/common_macros.rtl.f
-F ../common/common_pkg.rtl.f
-F ../common/common_skid_stage.rtl.f
-F ../common/common_tlp_rx_track.rtl.f
-F ../common/ram/common_sync_ram.rtl.f
-F ../common/ram/common_sdp_ram_fifo.rtl.f
pcie_rc_tl_pkg.sv
pcie_rc_tl_hdr.sv
pcie_rc_tl_cmd.sv
pcie_rc_tl_req.sv
pcie_rc_tl_tx.sv
pcie_rc_tl_rx.sv
pcie_rc_tl_mem.sv
pcie_rc_tl_cpl.sv
pcie_rc_tl_msi.sv
pcie_rc_tl.sv
