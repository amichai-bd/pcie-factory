// Simulation-only sources: the endpoint the e2e runs against (pcie_core,
// through its own filelist; the diamond with link counts once), then the
// back-to-back e2e top. The smoke test drives pcie_rc directly.
-F ../../rtl/core/core.rtl.f
tb/pcie_rc_e2e_tb.sv
