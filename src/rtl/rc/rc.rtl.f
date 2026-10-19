// pcie_rc: synthesizable sources only. The rc_tl and link filelists join
// with the stack integration (wiki/src/rtl/rc/MAS.md); rc_seq's is reached
// now so the sub-IP is part of rc (layout: IPs and sub-IPs).
-F ../rc_seq/rc_seq.rtl.f
pcie_rc.sv
