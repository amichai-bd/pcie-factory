// PCI Express transaction layer of the Endpoint: TLP parsing and routing,
// completions, requester tags and completion timeout, TLP transmit.
// Interfaces and behavior: wiki/src/arch/tl/HAS.md and its pages;
// structure: wiki/src/rtl/tl/MAS.md.
//
// The receive path (#290) is built: the parser and checks, the receive
// queues, the cq stream, the register-access engine and the credit
// release. The transmit path, completions and messages (#291) and the
// completion tracker (#292) attach at the slots of the MAS
// (wiki/src/rtl/tl/MAS.md#slots); until then their outputs hold their
// reset values and their slot inputs are tied as the MAS states.
`default_nettype none

`include "common_macros.svh"

module pcie_tl #(
  parameter int unsigned FC_PH           = 8,   // posted header credits advertised, 1..127
  parameter int unsigned FC_PD           = 32,  // posted data credits, 8..2047
  parameter int unsigned FC_NPH          = 8,   // non-posted header credits, 1..127
  parameter int unsigned FC_NPD          = 8,   // non-posted data credits, 2..2047
  parameter int unsigned CPL_TIMEOUT_DIV = 1,   // 1..1024, divides the completion timeout; 1 for silicon
  parameter int unsigned BAR0_ADDR_W     = 12   // 12..28: BAR0 is 2**BAR0_ADDR_W bytes
) (
  // Clock and reset
  input  logic                   clk_i,             // core clock, 125 MHz
  input  logic                   rst_i,             // synchronous to clk_i, active high // cdc-domain: clk_i

  // Link: upper TLP stream, transmit
  output logic                   tlp_tx_valid_o,
  input  logic                   tlp_tx_ready_i,    // cdc-domain: clk_i
  output logic [31:0]            tlp_tx_data_o,     // one DW, [7:0] the first byte
  output logic                   tlp_tx_sop_o,
  output logic                   tlp_tx_eop_o,

  // Link: upper TLP stream, receive
  input  logic                   tlp_rx_valid_i,    // cdc-domain: clk_i
  output logic                   tlp_rx_ready_o,    // 1 whenever dl_up_i is 1
  input  logic [31:0]            tlp_rx_data_i,     // cdc-domain: clk_i
  input  logic                   tlp_rx_sop_i,      // cdc-domain: clk_i
  input  logic                   tlp_rx_eop_i,      // cdc-domain: clk_i

  // Link: credits available and data link status
  input  logic [7:0]             fc_ph_avail_i,     // all ones: infinite // cdc-domain: clk_i
  input  logic [11:0]            fc_pd_avail_i,     // cdc-domain: clk_i
  input  logic [7:0]             fc_nph_avail_i,    // cdc-domain: clk_i
  input  logic [11:0]            fc_npd_avail_i,    // cdc-domain: clk_i
  input  logic [7:0]             fc_cplh_avail_i,   // cdc-domain: clk_i
  input  logic [11:0]            fc_cpld_avail_i,   // cdc-domain: clk_i
  input  logic                   dl_up_i,           // 0: tl held in reset // cdc-domain: clk_i

  // Link: credit release (to the link credit-release inputs, FC_RELEASE 1)
  output logic                   fc_rel_p_o,        // one posted request left tl: 1 header credit
  output logic [3:0]             fc_rel_pd_o,       // and these posted data credits, 0..8
  output logic                   fc_rel_np_o,       // one non-posted request left tl: 1 header credit
  output logic [3:0]             fc_rel_npd_o,      // and these non-posted data credits, 0..8

  // cfg: register access
  output logic                   reg_req_valid_o,
  input  logic                   reg_req_ready_i,   // cdc-domain: clk_i
  output logic                   reg_req_bar2_o,    // 0 configuration, 1 BAR2
  output logic                   reg_req_write_o,
  output logic [9:0]             reg_req_addr_o,    // DW index
  output logic [3:0]             reg_req_be_o,
  output logic [31:0]            reg_req_data_o,
  output logic [7:0]             reg_req_bus_o,     // configuration write: Bus Number
  output logic [4:0]             reg_req_dev_o,     // configuration write: Device Number
  input  logic                   reg_cpl_valid_i,   // cdc-domain: clk_i
  input  logic [31:0]            reg_cpl_data_i,    // cdc-domain: clk_i

  // cfg: register state
  input  logic [7:0]             bus_num_i,         // cdc-domain: clk_i
  input  logic [4:0]             dev_num_i,         // cdc-domain: clk_i
  input  logic                   mem_space_en_i,    // cdc-domain: clk_i
  input  logic                   bus_master_en_i,   // cdc-domain: clk_i
  input  logic [1:0]             d_state_i,         // 0 D0, 3 D3hot // cdc-domain: clk_i
  input  logic [31:0]            bar0_base_i,       // cdc-domain: clk_i
  input  logic [31:0]            bar2_base_i,       // cdc-domain: clk_i
  input  logic                   cpl_timeout_dis_i, // 1: the completion timeout never fires // cdc-domain: clk_i

  // cfg: error events, slot power limit and messages
  output logic [8:0]             tl_err_o,          // one-cycle pulses, bit order in the TL_CFG page
  output logic                   tl_np_pending_o,   // an rq read awaits its final completion or timeout
  output logic                   slot_pwr_valid_o,  // one-cycle pulse: a Set_Slot_Power_Limit to capture
  output logic [7:0]             slot_pwr_value_o,  // with valid: Slot Power Limit Value
  output logic [1:0]             slot_pwr_scale_o,  // with valid: Slot Power Limit Scale
  input  logic                   msg_valid_i,       // cdc-domain: clk_i
  output logic                   msg_ready_o,
  input  logic [1:0]             msg_kind_i,        // 0 interrupt write, 1 ERR_COR, 2 ERR_NONFATAL, 3 ERR_FATAL // cdc-domain: clk_i
  input  logic [63:0]            msg_addr_i,        // cdc-domain: clk_i
  input  logic [31:0]            msg_data_i,        // cdc-domain: clk_i

  // Completer requests (host to application, BAR0)
  output logic                   cq_valid_o,
  input  logic                   cq_ready_i,        // cdc-domain: clk_i
  output logic                   cq_sop_o,
  output logic                   cq_eop_o,
  output logic [31:0]            cq_data_o,         // write data, [7:0] lowest address
  output logic                   cq_write_o,        // 1 write, 0 read
  output logic [BAR0_ADDR_W-1:0] cq_addr_o,         // byte offset of the first DW in BAR0
  output logic [10:0]            cq_len_o,          // DWs, 1..1024
  output logic [3:0]             cq_first_be_o,
  output logic [3:0]             cq_last_be_o,
  output logic [2:0]             cq_handle_o,       // read handle, 0..7

  // Completer completions (application to host)
  input  logic                   cc_valid_i,        // cdc-domain: clk_i
  output logic                   cc_ready_o,
  input  logic                   cc_sop_i,          // cdc-domain: clk_i
  input  logic                   cc_eop_i,          // cdc-domain: clk_i
  input  logic [31:0]            cc_data_i,         // cdc-domain: clk_i
  input  logic [2:0]             cc_handle_i,       // cdc-domain: clk_i

  // Requester requests (application as bus master)
  input  logic                   rq_valid_i,        // cdc-domain: clk_i
  output logic                   rq_ready_o,
  input  logic                   rq_sop_i,          // cdc-domain: clk_i
  input  logic                   rq_eop_i,          // cdc-domain: clk_i
  input  logic [31:0]            rq_data_i,         // cdc-domain: clk_i
  input  logic                   rq_write_i,        // cdc-domain: clk_i
  input  logic [63:0]            rq_addr_i,         // cdc-domain: clk_i
  input  logic [5:0]             rq_len_i,          // DWs, 1..32 // cdc-domain: clk_i
  input  logic [3:0]             rq_first_be_i,     // cdc-domain: clk_i
  input  logic [3:0]             rq_last_be_i,      // cdc-domain: clk_i
  input  logic [2:0]             rq_tag_i,          // cdc-domain: clk_i

  // Requester completions (host to application)
  output logic                   rc_valid_o,
  input  logic                   rc_ready_i,        // cdc-domain: clk_i
  output logic                   rc_sop_o,
  output logic                   rc_eop_o,
  output logic [31:0]            rc_data_o,
  output logic [2:0]             rc_tag_o,
  output logic [1:0]             rc_status_o,       // 0 success, 1 UR, 2 CA, 3 failed
  output logic                   rc_done_o          // with eop: the read is finished
);


  // Parameter ranges (HAS, parameters).
  `COMMON_ASSERT_PARAM(g_fc_ph_check, FC_PH >= 1 && FC_PH <= 127, "pcie_tl: FC_PH must be 1 to 127")
  `COMMON_ASSERT_PARAM(g_fc_pd_check, FC_PD >= 8 && FC_PD <= 2047, "pcie_tl: FC_PD must be 8 to 2047")
  `COMMON_ASSERT_PARAM(g_fc_nph_check, FC_NPH >= 1 && FC_NPH <= 127, "pcie_tl: FC_NPH must be 1 to 127")
  `COMMON_ASSERT_PARAM(g_fc_npd_check, FC_NPD >= 2 && FC_NPD <= 2047, "pcie_tl: FC_NPD must be 2 to 2047")
  `COMMON_ASSERT_PARAM(g_div_check, CPL_TIMEOUT_DIV >= 1 && CPL_TIMEOUT_DIV <= 1024,
                       "pcie_tl: CPL_TIMEOUT_DIV must be 1 to 1024")
  `COMMON_ASSERT_PARAM(g_bar0_check, BAR0_ADDR_W >= 12 && BAR0_ADDR_W <= 28,
                       "pcie_tl: BAR0_ADDR_W must be 12 to 28")

  // tl is in reset while rst_i is 1 or the data link is down (HAS, reset).
  logic rst;
  assign rst = rst_i || !dl_up_i;

  // ---------------------------------------------------------------- slots
  // The transmit path, completions and messages (#291): the completion
  // former takes cpl_rec, rd_take/rd_ctx and pme_ack, and frees handles;
  // until it lands every record is taken at once and no handle is freed.
  logic       cpl_room;
  logic       pme_room;
  logic       hd_free;
  logic [2:0] hd_free_idx;

  assign cpl_room    = 1'b1;
  assign pme_room    = 1'b1;
  assign hd_free     = 1'b0;
  assign hd_free_idx = 3'd0;

  // The completion tracker (#292): it takes cpl_beat, orders rc with
  // rc_arrive/rc_ep against p_none, p_known and the posted head's epochs,
  // and raises tl_err_o bits 2 and 4 to 8.
  logic       rc_arrive;
  logic [8:0] trk_err;

  assign rc_arrive = 1'b0;
  assign trk_err   = 9'd0;

  // ------------------------------------------------------- receive path
  logic                    pq_push;
  pcie_tl_pkg::t_pq_ent    pq_ent;
  logic                    cqe_push;
  pcie_tl_pkg::t_cq_ent    cqe_ent;
  logic                    cfge_push;
  pcie_tl_pkg::t_cfg_ent   cfge_ent;
  logic                    pd_push;
  logic [31:0]             pd_data;
  logic                    np_mal;
  logic [3:0]              np_mal_d;
  logic [3:0]              rx_err;
  logic                    rx_spl_valid;
  pcie_tl_pkg::t_slot_pwr  rx_spl;
  pcie_tl_pkg::t_rx_beat   cpl_beat;

  pcie_tl_rx #(
    .BAR0_ADDR_W (BAR0_ADDR_W)
  ) u_rx (
    .clk_i          (clk_i),
    .rst_i          (rst),
    .tlp_rx_valid_i (tlp_rx_valid_i),
    .tlp_rx_data_i  (tlp_rx_data_i),
    .tlp_rx_sop_i   (tlp_rx_sop_i),
    .tlp_rx_eop_i   (tlp_rx_eop_i),
    .mem_space_en_i (mem_space_en_i),
    .d_state_i      (d_state_i),
    .bar0_base_i    (bar0_base_i),
    .bar2_base_i    (bar2_base_i),
    .pq_push_o      (pq_push),
    .pq_ent_o       (pq_ent),
    .cq_push_o      (cqe_push),
    .cq_ent_o       (cqe_ent),
    .cfg_push_o     (cfge_push),
    .cfg_ent_o      (cfge_ent),
    .pd_push_o      (pd_push),
    .pd_data_o      (pd_data),
    .np_mal_o       (np_mal),
    .np_mal_d_o     (np_mal_d),
    .err_o          (rx_err),
    .slot_pwr_valid_o (rx_spl_valid),
    .slot_pwr_o     (rx_spl),
    .cpl_beat_o     (cpl_beat)
  );

  logic                    pq_valid;
  pcie_tl_pkg::t_pq_ent    pq_head;
  logic                    pq_pop_cq;
  logic                    pq_pop_reg;
  logic                    pd_valid;
  logic [31:0]             pd_head;
  logic                    pd_pop_cq;
  logic                    pd_pop_reg;
  logic                    pd_pop;
  logic                    rdq_valid;
  pcie_tl_pkg::t_cq_ent    rdq_head;
  logic                    rdq_ok;
  logic                    rdq_pop;
  logic                    cfgq_valid;
  pcie_tl_pkg::t_cfg_ent   cfgq_head;
  logic                    cfgq_ok;
  logic                    cfgq_pop;
  logic                    pme_ack;
  pcie_tl_pkg::t_ep        rc_ep;
  logic                    p_none;
  logic                    p_known;
  logic                    rel_p;
  logic [3:0]              rel_pd;
  logic                    rel_np;
  logic [3:0]              rel_npd;

  assign pd_pop = pd_pop_cq || pd_pop_reg;

  pcie_tl_rx_q #(
    .FC_PH  (FC_PH),
    .FC_PD  (FC_PD),
    .FC_NPH (FC_NPH)
  ) u_rx_q (
    .clk_i        (clk_i),
    .rst_i        (rst),
    .pq_push_i    (pq_push),
    .pq_ent_i     (pq_ent),
    .cq_push_i    (cqe_push),
    .cq_ent_i     (cqe_ent),
    .cfg_push_i   (cfge_push),
    .cfg_ent_i    (cfge_ent),
    .pd_push_i    (pd_push),
    .pd_data_i    (pd_data),
    .np_mal_i     (np_mal),
    .np_mal_d_i   (np_mal_d),
    .pq_valid_o   (pq_valid),
    .pq_head_o    (pq_head),
    .pq_pop_cq_i  (pq_pop_cq),
    .pq_pop_reg_i (pq_pop_reg),
    .pd_valid_o   (pd_valid),
    .pd_head_o    (pd_head),
    .pd_pop_i     (pd_pop),
    .cq_valid_o   (rdq_valid),
    .cq_head_o    (rdq_head),
    .cq_ok_o      (rdq_ok),
    .cq_pop_i     (rdq_pop),
    .cfg_valid_o  (cfgq_valid),
    .cfg_head_o   (cfgq_head),
    .cfg_ok_o     (cfgq_ok),
    .cfg_pop_i    (cfgq_pop),
    .pme_room_i   (pme_room),
    .pme_ack_o    (pme_ack),
    .rc_arrive_i  (rc_arrive),
    .rc_ep_o      (rc_ep),
    .p_none_o     (p_none),
    .p_known_o    (p_known),
    .fc_rel_p_o   (rel_p),
    .fc_rel_pd_o  (rel_pd),
    .fc_rel_np_o  (rel_np),
    .fc_rel_npd_o (rel_npd)
  );

  logic                    cq_valid;
  logic                    rd_take;
  pcie_tl_pkg::t_hd_ctx    rd_ctx;

  pcie_tl_cq #(
    .BAR0_ADDR_W (BAR0_ADDR_W)
  ) u_cq (
    .clk_i         (clk_i),
    .rst_i         (rst),
    .pq_valid_i    (pq_valid),
    .pq_head_i     (pq_head),
    .pq_pop_o      (pq_pop_cq),
    .pd_valid_i    (pd_valid),
    .pd_head_i     (pd_head),
    .pd_pop_o      (pd_pop_cq),
    .rd_valid_i    (rdq_valid),
    .rd_head_i     (rdq_head),
    .rd_ok_i       (rdq_ok),
    .rd_pop_o      (rdq_pop),
    .rd_take_o     (rd_take),
    .rd_ctx_o      (rd_ctx),
    .hd_free_i     (hd_free),
    .hd_free_idx_i (hd_free_idx),
    .cq_valid_o    (cq_valid),
    .cq_ready_i    (cq_ready_i && !rst),
    .cq_sop_o      (cq_sop_o),
    .cq_eop_o      (cq_eop_o),
    .cq_data_o     (cq_data_o),
    .cq_write_o    (cq_write_o),
    .cq_addr_o     (cq_addr_o),
    .cq_len_o      (cq_len_o),
    .cq_first_be_o (cq_first_be_o),
    .cq_last_be_o  (cq_last_be_o),
    .cq_handle_o   (cq_handle_o)
  );

  logic                    reg_req_valid;
  logic                    cpl_rec_valid;
  pcie_tl_pkg::t_cpl_rec   cpl_rec;

  pcie_tl_reg u_reg (
    .clk_i           (clk_i),
    .rst_i           (rst),
    .cfg_valid_i     (cfgq_valid),
    .cfg_head_i      (cfgq_head),
    .cfg_ok_i        (cfgq_ok),
    .cfg_pop_o       (cfgq_pop),
    .pq_valid_i      (pq_valid),
    .pq_head_i       (pq_head),
    .pq_pop_o        (pq_pop_reg),
    .pd_valid_i      (pd_valid),
    .pd_head_i       (pd_head),
    .pd_pop_o        (pd_pop_reg),
    .cpl_room_i      (cpl_room),
    .cpl_rec_valid_o (cpl_rec_valid),
    .cpl_rec_o       (cpl_rec),
    .reg_req_valid_o (reg_req_valid),
    .reg_req_ready_i (reg_req_ready_i && !rst),
    .reg_req_bar2_o  (reg_req_bar2_o),
    .reg_req_write_o (reg_req_write_o),
    .reg_req_addr_o  (reg_req_addr_o),
    .reg_req_be_o    (reg_req_be_o),
    .reg_req_data_o  (reg_req_data_o),
    .reg_req_bus_o   (reg_req_bus_o),
    .reg_req_dev_o   (reg_req_dev_o),
    .reg_cpl_valid_i (reg_cpl_valid_i && !rst),
    .reg_cpl_data_i  (reg_cpl_data_i)
  );

  // ------------------------------------------------------------- outputs
  // Every handshake output is 0 in reset (HAS, reset).
  assign tlp_rx_ready_o  = !rst;
  assign cq_valid_o      = cq_valid && !rst;
  assign reg_req_valid_o = reg_req_valid && !rst;
  assign fc_rel_p_o      = rel_p && !rst;
  assign fc_rel_pd_o     = rel_pd;
  assign fc_rel_np_o     = rel_np && !rst;
  assign fc_rel_npd_o    = rel_npd;
  assign tl_err_o        = rst ? 9'd0 : ({5'd0, rx_err} | trk_err);
  assign slot_pwr_valid_o = rx_spl_valid && !rst;
  assign slot_pwr_value_o = rx_spl.value;
  assign slot_pwr_scale_o = rx_spl.scale;

  // Transmit (#291) and tracker (#292) outputs at their reset values.
  assign tlp_tx_valid_o  = 1'b0;
  assign tlp_tx_data_o   = 32'd0;
  assign tlp_tx_sop_o    = 1'b0;
  assign tlp_tx_eop_o    = 1'b0;
  assign tl_np_pending_o = 1'b0;
  assign msg_ready_o     = 1'b0;
  assign cc_ready_o      = 1'b0;
  assign rq_ready_o      = 1'b0;
  assign rc_valid_o      = 1'b0;
  assign rc_sop_o        = 1'b0;
  assign rc_eop_o        = 1'b0;
  assign rc_data_o       = 32'd0;
  assign rc_tag_o        = 3'd0;
  assign rc_status_o     = 2'd0;
  assign rc_done_o       = 1'b0;

  // Inputs and slot nets the receive path does not use; #291 and #292
  // consume them.
  `COMMON_UNUSED_BITS(unused_slots, {cpl_rec_valid, cpl_rec, rd_take, rd_ctx, pme_ack, cpl_beat,
                                     rc_ep, p_none, p_known,
                                     tlp_tx_ready_i,
                                     fc_ph_avail_i, fc_pd_avail_i, fc_nph_avail_i, fc_npd_avail_i,
                                     fc_cplh_avail_i, fc_cpld_avail_i,
                                     bus_num_i, dev_num_i, bus_master_en_i, cpl_timeout_dis_i,
                                     msg_valid_i, msg_kind_i, msg_addr_i, msg_data_i,
                                     cc_valid_i, cc_sop_i, cc_eop_i, cc_data_i, cc_handle_i,
                                     rq_valid_i, rq_sop_i, rq_eop_i, rq_data_i, rq_write_i,
                                     rq_addr_i, rq_len_i, rq_first_be_i, rq_last_be_i, rq_tag_i,
                                     rc_ready_i})

endmodule

`default_nettype wire
