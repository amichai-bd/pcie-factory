// pcie_rc: synthesizable sources only. The sub-IP rc_tl is reached here
// (layout: a sub-IP's filelist is included by its parent's); the rc_seq and
// link filelists join with the stack integration (wiki/src/rtl/rc/MAS.md).
-F ../rc_tl/rc_tl.rtl.f
pcie_rc.sv
