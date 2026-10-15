// pcie_top: synthesizable sources only (shared vocabulary, sub-IP, then
// the top).
-F ../common/common_macros.rtl.f
// Level synchronizer for the board wrapper's inputs (fpga/top/).
-F ../common/common_cdc_sync2.rtl.f
-F ../top_lfsr/top_lfsr.rtl.f
pcie_top.sv
