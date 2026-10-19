// pcie_rc: synthesizable sources only. The sub-IPs rc_tl and rc_seq are
// reached here, so each is part of rc (layout: IPs and sub-IPs); the link
// filelist joins with the stack integration (wiki/src/rtl/rc/MAS.md).
-F ../rc_tl/rc_tl.rtl.f
-F ../rc_seq/rc_seq.rtl.f
pcie_rc.sv
