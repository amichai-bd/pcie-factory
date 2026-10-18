// Bind tap on pcie_tl's slot and block nets (wiki/src/dv/tl/DV_PLAN.md,
// slot tap): observes MAS-named nets (wiki/src/rtl/tl/MAS.md#slots): the
// completion records, the reads taken on cq with their handle context, the
// PME_TO_Ack requests, and the nets for the tracker not yet built (#292):
// the completion beats, the rq reads taken and sent. The testbench top
// shows them as probe_*_o. It drives nothing in the DUT. Bound below,
// listed in tl.dv.f.
`default_nettype none

module pcie_tl_slot_tap (
  input  logic                  cpl_rec_valid_i,
  input  pcie_tl_pkg::t_cpl_rec cpl_rec_i,
  input  logic                  rd_take_i,
  input  pcie_tl_pkg::t_hd_ctx  rd_ctx_i,
  input  logic                  pme_ack_i,
  input  pcie_tl_pkg::t_rx_beat cpl_beat_i,
  input  logic                  rq_rd_take_i,
  input  pcie_tl_pkg::t_rq_ctx  rq_rd_ctx_i,
  input  logic                  rq_sent_i,
  input  logic [2:0]            rq_sent_tag_i
);

  // Read by the testbench top.
  logic                  rec_valid;
  pcie_tl_pkg::t_cpl_rec rec;
  logic                  take;
  pcie_tl_pkg::t_hd_ctx  ctx;
  logic                  pme;
  pcie_tl_pkg::t_rx_beat beat;
  logic                  rq_take;
  pcie_tl_pkg::t_rq_ctx  rq_ctx;
  logic                  rq_sent;
  logic [2:0]            rq_sent_tag;

  assign rec_valid = cpl_rec_valid_i;
  assign rec       = cpl_rec_i;
  assign take      = rd_take_i;
  assign ctx       = rd_ctx_i;
  assign pme       = pme_ack_i;
  assign beat      = cpl_beat_i;
  assign rq_take   = rq_rd_take_i;
  assign rq_ctx    = rq_rd_ctx_i;
  assign rq_sent   = rq_sent_i;
  assign rq_sent_tag = rq_sent_tag_i;

endmodule

bind pcie_tl pcie_tl_slot_tap u_slot_tap (
  .cpl_rec_valid_i (cpl_rec_valid),
  .cpl_rec_i       (cpl_rec),
  .rd_take_i       (rd_take),
  .rd_ctx_i        (rd_ctx),
  .pme_ack_i       (pme_ack),
  .cpl_beat_i      (cpl_beat),
  .rq_rd_take_i    (rq_rd_take),
  .rq_rd_ctx_i     (rq_rd_ctx),
  .rq_sent_i       (rq_sent),
  .rq_sent_tag_i   (rq_sent_tag)
);

`default_nettype wire
