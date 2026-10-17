// Bind tap on pcie_dll's credit gate (wiki/src/dv/dll/DV_PLAN.md, gate
// tap): observes the MAS-named fc_tx_ok (wiki/src/rtl/dll/MAS.md, credit
// interface) for the testbench, which shows it as probe_tx_ok_o. It drives
// nothing in the DUT. Bound below, listed in dll.dv.f.
`default_nettype none

module pcie_dll_fc_tap (
  input  logic fc_tx_ok_i   // the credit gate, as the top names it
);

  logic tx_ok;  // read by the testbench top

  assign tx_ok = fc_tx_ok_i;

endmodule

bind pcie_dll pcie_dll_fc_tap u_fc_tap (.fc_tx_ok_i(fc_tx_ok));

`default_nettype wire
