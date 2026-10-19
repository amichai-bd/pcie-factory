// pcie_rc: synthesizable sources only. The sub-IPs rc_tl and rc_seq and
// the integrated IP link, each through its own filelist, then the stack
// top (wiki/src/rtl/rc/MAS.md#files).
-F ../rc_tl/rc_tl.rtl.f
-F ../rc_seq/rc_seq.rtl.f
-F ../link/link.rtl.f
pcie_rc.sv
