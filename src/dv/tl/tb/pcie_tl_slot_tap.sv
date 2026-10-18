// Bind tap on pcie_tl's slot nets (wiki/src/dv/tl/DV_PLAN.md, slot tap):
// observes the MAS-named nets the receive path hands to the blocks not yet
// built (wiki/src/rtl/tl/MAS.md#slots): the completion records, the reads
// taken on cq with their handle context, the PME_TO_Ack requests and the
// completion beats for the tracker. The testbench top shows them as
// probe_*_o. It drives nothing in the DUT. Bound below, listed in tl.dv.f.
`default_nettype none

module pcie_tl_slot_tap (
  input  logic                  cpl_rec_valid_i,
  input  pcie_tl_pkg::t_cpl_rec cpl_rec_i,
  input  logic                  rd_take_i,
  input  pcie_tl_pkg::t_hd_ctx  rd_ctx_i,
  input  logic                  pme_ack_i,
  input  pcie_tl_pkg::t_rx_beat cpl_beat_i
);

  // Read by the testbench top.
  logic                  rec_valid;
  pcie_tl_pkg::t_cpl_rec rec;
  logic                  take;
  pcie_tl_pkg::t_hd_ctx  ctx;
  logic                  pme;
  pcie_tl_pkg::t_rx_beat beat;

  assign rec_valid = cpl_rec_valid_i;
  assign rec       = cpl_rec_i;
  assign take      = rd_take_i;
  assign ctx       = rd_ctx_i;
  assign pme       = pme_ack_i;
  assign beat      = cpl_beat_i;

endmodule

bind pcie_tl pcie_tl_slot_tap u_slot_tap (
  .cpl_rec_valid_i (cpl_rec_valid),
  .cpl_rec_i       (cpl_rec),
  .rd_take_i       (rd_take),
  .rd_ctx_i        (rd_ctx),
  .pme_ack_i       (pme_ack),
  .cpl_beat_i      (cpl_beat)
);

`default_nettype wire
