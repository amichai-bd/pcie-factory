`default_nettype none

// Back-to-back testbench top of the core e2e (wiki/src/dv/core/DV_PLAN.md).
//
// The endpoint pcie_core (u_ep) faces the host's pcie_link (u_host,
// downstream, port_role_i 0), both on clk_i; each one's serial transmit
// drives the other's receive directly and each ser_rx_clk_i is clk_i (an
// error-free line, unless the link error test impairs it; line impairments
// are the link e2e's). The host-to-ep word goes through one register,
// tapped on mon_line_o, so the environment sees each word for a cycle
// before the ep samples it: line_sub_i replaces it with line_sub_data_i,
// and line_flip_i flips chosen bits of it. The ep ports are
// ports of this top with no prefix; the host link's upper stream, credits,
// status and Hot Reset request are ports prefixed host_. The cocotb
// environment (tb/core_e2e_env.py) puts the independent host model on the
// host link's upper stream and the application model on the ep streams.
//
// The host link advertises the link HAS defaults (P 8, 32; NP 8, 8;
// completions infinite) and returns credits at delivery (FC_RELEASE 0):
// the host model never stalls its receive stream.
//
// Taps (core MAS, tl-cfg nets): u_ep.tl_err and u_ep.tl_np_pending on
// mon_*_o, for the architecture-model shadow of cfg. Every parameter is
// echoed on a cfg_*_o output for the environment; the ID parameters are
// int unsigned here (a regression entry sets them as integers) and sized at
// the core instance.
module pcie_core_e2e_tb #(
  parameter int unsigned  FC_PH            = 8,
  parameter int unsigned  FC_PD            = 32,
  parameter int unsigned  FC_NPH           = 8,
  parameter int unsigned  FC_NPD           = 8,
  parameter int unsigned  TIMEOUT_DIV      = 1024,
  parameter int unsigned  CPL_TIMEOUT_DIV  = 1024,
  parameter int unsigned  BAR0_ADDR_W      = 12,
  parameter int unsigned  MSIX_TABLE_SIZE  = 8,
  parameter int unsigned  VENDOR_ID        = 'h0000,
  parameter int unsigned  DEVICE_ID        = 'h0000,
  parameter int unsigned  SUBSYS_VENDOR_ID = 'h0000,
  parameter int unsigned  SUBSYS_ID        = 'h0000,
  parameter int unsigned  CLASS_CODE       = 'hFF0000,
  parameter int unsigned  REVISION_ID      = 'h00
) (
  input  logic                   clk_i,
  input  logic                   rst_i,
  input  logic                   ser_tx_rxdet_i,

  // Endpoint application interface (core APP page)
  output logic                   cq_valid_o,
  input  logic                   cq_ready_i,
  output logic                   cq_sop_o,
  output logic                   cq_eop_o,
  output logic [31:0]            cq_data_o,
  output logic                   cq_write_o,
  output logic [BAR0_ADDR_W-1:0] cq_addr_o,
  output logic [10:0]            cq_len_o,
  output logic [3:0]             cq_first_be_o,
  output logic [3:0]             cq_last_be_o,
  output logic [2:0]             cq_handle_o,
  input  logic                   cc_valid_i,
  output logic                   cc_ready_o,
  input  logic                   cc_sop_i,
  input  logic                   cc_eop_i,
  input  logic [31:0]            cc_data_i,
  input  logic [2:0]             cc_handle_i,
  input  logic                   rq_valid_i,
  output logic                   rq_ready_o,
  input  logic                   rq_sop_i,
  input  logic                   rq_eop_i,
  input  logic [31:0]            rq_data_i,
  input  logic                   rq_write_i,
  input  logic [63:0]            rq_addr_i,
  input  logic [5:0]             rq_len_i,
  input  logic [3:0]             rq_first_be_i,
  input  logic [3:0]             rq_last_be_i,
  input  logic [2:0]             rq_tag_i,
  output logic                   rc_valid_o,
  input  logic                   rc_ready_i,
  output logic                   rc_sop_o,
  output logic                   rc_eop_o,
  output logic [31:0]            rc_data_o,
  output logic [2:0]             rc_tag_o,
  output logic [1:0]             rc_status_o,
  output logic                   rc_done_o,
  input  logic                   irq_valid_i,
  output logic                   irq_ready_o,
  input  logic [5:0]             irq_vector_i,
  output logic                   irq_done_o,
  output logic [1:0]             irq_status_o,
  output logic                   bus_master_en_o,
  output logic                   mem_space_en_o,
  output logic [1:0]             d_state_o,
  output logic [2:0]             max_payload_o,
  output logic [2:0]             max_read_req_o,
  output logic                   dl_up_o,
  output logic [1:0]             dl_state_o,
  output logic [6:0]             dl_err_o,
  output logic                   link_up_o,
  output logic [5:0]             ltssm_state_o,
  output logic                   rcv_err_o,
  output logic                   ser_tx_elec_idle_o,

  // Host link (pcie_link, downstream)
  input  logic                   host_rst_i,
  input  logic                   host_tlp_tx_valid_i,
  input  logic [31:0]            host_tlp_tx_data_i,
  input  logic                   host_tlp_tx_sop_i,
  input  logic                   host_tlp_tx_eop_i,
  output logic                   host_tlp_tx_ready_o,
  output logic                   host_tlp_rx_valid_o,
  output logic [31:0]            host_tlp_rx_data_o,
  output logic                   host_tlp_rx_sop_o,
  output logic                   host_tlp_rx_eop_o,
  output logic [7:0]             host_fc_ph_avail_o,
  output logic [11:0]            host_fc_pd_avail_o,
  output logic [7:0]             host_fc_nph_avail_o,
  output logic [11:0]            host_fc_npd_avail_o,
  output logic [7:0]             host_fc_cplh_avail_o,
  output logic [11:0]            host_fc_cpld_avail_o,
  output logic                   host_dl_up_o,
  output logic [6:0]             host_dl_err_o,
  output logic                   host_link_up_o,
  output logic                   host_rcv_err_o,
  input  logic                   host_hot_reset_req_i,
  input  logic [19:0]            line_flip_i,       // XORed onto the host-to-ep serial word
  input  logic                   line_sub_i,        // replace the host-to-ep word
  input  logic [19:0]            line_sub_data_i,   // with this one
  output logic [19:0]            mon_line_o,        // the host-to-ep word the ep samples next

  // Taps of the ep's tl-cfg nets (core MAS)
  output logic [8:0]             mon_tl_err_o,
  output logic                   mon_tl_np_pending_o,

  // Parameter echo
  output logic [11:0]            cfg_fc_ph_o,
  output logic [11:0]            cfg_fc_pd_o,
  output logic [11:0]            cfg_fc_nph_o,
  output logic [11:0]            cfg_fc_npd_o,
  output logic [10:0]            cfg_cpl_timeout_div_o,
  output logic [4:0]             cfg_bar0_addr_w_o,
  output logic [6:0]             cfg_msix_table_size_o,
  output logic [15:0]            cfg_vendor_id_o,
  output logic [15:0]            cfg_device_id_o,
  output logic [15:0]            cfg_subsys_vendor_id_o,
  output logic [15:0]            cfg_subsys_id_o,
  output logic [23:0]            cfg_class_code_o,
  output logic [7:0]             cfg_revision_id_o
);

  logic [19:0] ep_tx_data;
  logic        ep_tx_ei;
  logic [19:0] host_tx_data;
  logic        host_tx_ei;
  logic [19:0] line_q;          // one word of channel delay, host to ep
  logic        line_ei_q;
  logic [19:0] ep_rx_data;

  always_ff @(posedge clk_i) begin
    line_q    <= host_tx_data;
    line_ei_q <= host_tx_ei;
  end
  assign ep_rx_data = line_sub_i ? line_sub_data_i : (line_q ^ line_flip_i);
  assign mon_line_o = line_q;

  pcie_core #(
    .FC_PH(FC_PH), .FC_PD(FC_PD), .FC_NPH(FC_NPH), .FC_NPD(FC_NPD),
    .TIMEOUT_DIV(TIMEOUT_DIV), .ERR_TIMEOUT_DIV(1), .CPL_TIMEOUT_DIV(CPL_TIMEOUT_DIV),
    .BAR0_ADDR_W(BAR0_ADDR_W), .MSIX_TABLE_SIZE(MSIX_TABLE_SIZE),
    .VENDOR_ID(16'(VENDOR_ID)), .DEVICE_ID(16'(DEVICE_ID)), .SUBSYS_VENDOR_ID(16'(SUBSYS_VENDOR_ID)),
    .SUBSYS_ID(16'(SUBSYS_ID)), .CLASS_CODE(24'(CLASS_CODE)), .REVISION_ID(8'(REVISION_ID))
  ) u_ep (
    .clk_i(clk_i), .rst_i(rst_i),
    .cq_valid_o(cq_valid_o), .cq_ready_i(cq_ready_i), .cq_sop_o(cq_sop_o),
    .cq_eop_o(cq_eop_o), .cq_data_o(cq_data_o), .cq_write_o(cq_write_o),
    .cq_addr_o(cq_addr_o), .cq_len_o(cq_len_o), .cq_first_be_o(cq_first_be_o),
    .cq_last_be_o(cq_last_be_o), .cq_handle_o(cq_handle_o),
    .cc_valid_i(cc_valid_i), .cc_ready_o(cc_ready_o), .cc_sop_i(cc_sop_i),
    .cc_eop_i(cc_eop_i), .cc_data_i(cc_data_i), .cc_handle_i(cc_handle_i),
    .rq_valid_i(rq_valid_i), .rq_ready_o(rq_ready_o), .rq_sop_i(rq_sop_i),
    .rq_eop_i(rq_eop_i), .rq_data_i(rq_data_i), .rq_write_i(rq_write_i),
    .rq_addr_i(rq_addr_i), .rq_len_i(rq_len_i), .rq_first_be_i(rq_first_be_i),
    .rq_last_be_i(rq_last_be_i), .rq_tag_i(rq_tag_i),
    .rc_valid_o(rc_valid_o), .rc_ready_i(rc_ready_i), .rc_sop_o(rc_sop_o),
    .rc_eop_o(rc_eop_o), .rc_data_o(rc_data_o), .rc_tag_o(rc_tag_o),
    .rc_status_o(rc_status_o), .rc_done_o(rc_done_o),
    .irq_valid_i(irq_valid_i), .irq_ready_o(irq_ready_o), .irq_vector_i(irq_vector_i),
    .irq_done_o(irq_done_o), .irq_status_o(irq_status_o),
    .bus_master_en_o(bus_master_en_o), .mem_space_en_o(mem_space_en_o),
    .d_state_o(d_state_o), .max_payload_o(max_payload_o), .max_read_req_o(max_read_req_o),
    .dl_up_o(dl_up_o), .dl_state_o(dl_state_o), .dl_err_o(dl_err_o),
    .link_up_o(link_up_o), .ltssm_state_o(ltssm_state_o), .rcv_err_o(rcv_err_o),
    .ser_tx_data_o(ep_tx_data), .ser_tx_elec_idle_o(ep_tx_ei),
    .ser_tx_rxdet_i(ser_tx_rxdet_i),
    .ser_rx_clk_i(clk_i), .ser_rx_data_i(ep_rx_data),
    .ser_rx_elec_idle_i(line_ei_q)
  );
  assign ser_tx_elec_idle_o = ep_tx_ei;

  logic [1:0] host_dl_state;
  logic [5:0] host_ltssm;

  pcie_link #(
    .FC_PH(8), .FC_PD(32), .FC_NPH(8), .FC_NPD(8), .FC_CPLH(0), .FC_CPLD(0),
    .FC_RELEASE(0), .TIMEOUT_DIV(TIMEOUT_DIV), .ERR_TIMEOUT_DIV(1)
  ) u_host (
    .clk_i(clk_i), .rst_i(host_rst_i), .port_role_i(1'b0),
    .tlp_tx_valid_i(host_tlp_tx_valid_i), .tlp_tx_ready_o(host_tlp_tx_ready_o),
    .tlp_tx_data_i(host_tlp_tx_data_i), .tlp_tx_sop_i(host_tlp_tx_sop_i),
    .tlp_tx_eop_i(host_tlp_tx_eop_i),
    .tlp_rx_valid_o(host_tlp_rx_valid_o), .tlp_rx_ready_i(1'b1),
    .tlp_rx_data_o(host_tlp_rx_data_o), .tlp_rx_sop_o(host_tlp_rx_sop_o),
    .tlp_rx_eop_o(host_tlp_rx_eop_o),
    .fc_ph_avail_o(host_fc_ph_avail_o), .fc_pd_avail_o(host_fc_pd_avail_o),
    .fc_nph_avail_o(host_fc_nph_avail_o), .fc_npd_avail_o(host_fc_npd_avail_o),
    .fc_cplh_avail_o(host_fc_cplh_avail_o), .fc_cpld_avail_o(host_fc_cpld_avail_o),
    .fc_rel_p_i(1'b0), .fc_rel_pd_i(4'd0), .fc_rel_np_i(1'b0), .fc_rel_npd_i(4'd0),
    .dl_up_o(host_dl_up_o), .dl_state_o(host_dl_state), .dl_err_o(host_dl_err_o),
    .link_up_o(host_link_up_o), .ltssm_state_o(host_ltssm), .rcv_err_o(host_rcv_err_o),
    .retrain_req_i(1'b0), .hot_reset_req_i(host_hot_reset_req_i), .disable_req_i(1'b0),
    .loopback_req_i(1'b0),
    .ser_tx_data_o(host_tx_data), .ser_tx_elec_idle_o(host_tx_ei),
    .ser_tx_rxdet_i(1'b1),
    .ser_rx_clk_i(clk_i), .ser_rx_data_i(ep_tx_data), .ser_rx_elec_idle_i(ep_tx_ei)
  );

  assign mon_tl_err_o        = u_ep.tl_err;
  assign mon_tl_np_pending_o = u_ep.tl_np_pending;

  assign cfg_fc_ph_o            = 12'(FC_PH);
  assign cfg_fc_pd_o            = 12'(FC_PD);
  assign cfg_fc_nph_o           = 12'(FC_NPH);
  assign cfg_fc_npd_o           = 12'(FC_NPD);
  assign cfg_cpl_timeout_div_o  = 11'(CPL_TIMEOUT_DIV);
  assign cfg_bar0_addr_w_o      = 5'(BAR0_ADDR_W);
  assign cfg_msix_table_size_o  = 7'(MSIX_TABLE_SIZE);
  assign cfg_vendor_id_o        = 16'(VENDOR_ID);
  assign cfg_device_id_o        = 16'(DEVICE_ID);
  assign cfg_subsys_vendor_id_o = 16'(SUBSYS_VENDOR_ID);
  assign cfg_subsys_id_o        = 16'(SUBSYS_ID);
  assign cfg_class_code_o       = 24'(CLASS_CODE);
  assign cfg_revision_id_o      = 8'(REVISION_ID);

endmodule

`default_nettype wire
