// pcie_rc_seq: synthesizable sources only: the shared vocabulary, the
// shared package (the tl-seq types), the result RAM, the package (step
// format and programs), the ROM, then the top
// (wiki/src/rtl/rc_seq/MAS.md#files).
-F ../common/common_macros.rtl.f
-F ../common/common_pkg.rtl.f
-F ../common/ram/common_sdp_ram.rtl.f
pcie_rc_seq_pkg.sv
pcie_rc_seq_rom.sv
pcie_rc_seq.sv
