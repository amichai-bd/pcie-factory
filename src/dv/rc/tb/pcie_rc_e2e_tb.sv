`default_nettype none

// Back-to-back testbench top of the rc e2e (wiki/src/dv/rc/DV_PLAN.md).
//
// The root-port stack pcie_rc (u_rc) faces the endpoint pcie_core (u_ep)
// over the full phy stack, both on clk_i; each ser_rx_clk_i is clk_i. Each
// direction of the serial line goes through one register (dn: rc to ep,
// up: ep to rc), so the receiver sees a word one cycle after its
// transmitter sent it, unless the environment drives the line itself:
// with *_sub_i 1 the receiver takes *_sub_data_i and *_sub_ei_i instead.
// The environment reads each transmitter's word on mon_*_tx_o in the
// cycle it is sent and, driving it back one cycle later, keeps the same
// timing as the register; that is how its line models (the rewriter that
// replaces TLPs on the line, the link error injector) sit on the line.
//
// The ep ports keep the names of the core e2e top (tb/core_e2e_env.py of
// the core DV is reused for the application model and the cfg shadow);
// the rc ports are prefixed rp_. Taps (rc MAS, Nets): the tl-seq nets and
// rc_tl's link upper stream inside u_rc, for the host model shadow, and
// u_ep.tl_err and u_ep.tl_np_pending (core MAS) for the cfg shadow. Every
// parameter is echoed on a cfg_*_o output.
module pcie_rc_e2e_tb #(
  parameter int unsigned FC_PH           = 8,      // both ends' advertised credits
  parameter int unsigned FC_PD           = 32,
  parameter int unsigned FC_NPH          = 8,
  parameter int unsigned FC_NPD          = 8,
  parameter int unsigned TIMEOUT_DIV     = 1024,   // both links
  parameter int unsigned ERR_TIMEOUT_DIV = 1024,   // both links: a cut line goes down in 16384 cycles
  parameter int unsigned CPL_TIMEOUT_DIV = 1024,   // rc_tl's completion timeout
  parameter int unsigned EP_CPL_TIMEOUT_DIV = 1024, // the endpoint's
  parameter int unsigned PROGRAM         = 1       // rc_seq program id
) (
  input  logic        clk_i,
  input  logic        rst_i,                // the endpoint's reset
  input  logic        ser_tx_rxdet_i,       // both transmitters see a receiver

  // Endpoint application interface (core APP page)
  output logic        cq_valid_o,
  input  logic        cq_ready_i,
  output logic        cq_sop_o,
  output logic        cq_eop_o,
  output logic [31:0] cq_data_o,
  output logic        cq_write_o,
  output logic [11:0] cq_addr_o,
  output logic [10:0] cq_len_o,
  output logic [3:0]  cq_first_be_o,
  output logic [3:0]  cq_last_be_o,
  output logic [2:0]  cq_handle_o,
  input  logic        cc_valid_i,
  output logic        cc_ready_o,
  input  logic        cc_sop_i,
  input  logic        cc_eop_i,
  input  logic [31:0] cc_data_i,
  input  logic [2:0]  cc_handle_i,
  input  logic        rq_valid_i,
  output logic        rq_ready_o,
  input  logic        rq_sop_i,
  input  logic        rq_eop_i,
  input  logic [31:0] rq_data_i,
  input  logic        rq_write_i,
  input  logic [63:0] rq_addr_i,
  input  logic [5:0]  rq_len_i,
  input  logic [3:0]  rq_first_be_i,
  input  logic [3:0]  rq_last_be_i,
  input  logic [2:0]  rq_tag_i,
  output logic        rc_valid_o,
  input  logic        rc_ready_i,
  output logic        rc_sop_o,
  output logic        rc_eop_o,
  output logic [31:0] rc_data_o,
  output logic [2:0]  rc_tag_o,
  output logic [1:0]  rc_status_o,
  output logic        rc_done_o,
  input  logic        irq_valid_i,
  output logic        irq_ready_o,
  input  logic [5:0]  irq_vector_i,
  output logic        irq_done_o,
  output logic [1:0]  irq_status_o,
  output logic        bus_master_en_o,
  output logic        mem_space_en_o,
  output logic [1:0]  d_state_o,
  output logic [2:0]  max_payload_o,
  output logic [2:0]  max_read_req_o,
  output logic        dl_up_o,
  output logic [1:0]  dl_state_o,
  output logic [6:0]  dl_err_o,
  output logic        link_up_o,
  output logic [5:0]  ltssm_state_o,
  output logic        rcv_err_o,
  output logic        ser_tx_elec_idle_o,

  // Root port (rc HAS ports)
  input  logic        rp_rst_i,
  input  logic        rp_start_i,
  output logic        rp_done_o,
  output logic        rp_pass_o,
  output logic [15:0] rp_fail_step_o,
  output logic        rp_dl_up_o,
  output logic [1:0]  rp_dl_state_o,
  output logic [6:0]  rp_dl_err_o,
  output logic        rp_link_up_o,
  output logic [5:0]  rp_ltssm_state_o,
  output logic        rp_rcv_err_o,
  output logic        rp_ser_tx_elec_idle_o,

  // The line: transmitted words, and the environment's replacements
  output logic [19:0] mon_dn_tx_o,          // rc's transmit word this cycle
  output logic        mon_dn_ei_o,
  output logic [19:0] mon_up_tx_o,          // the ep's transmit word this cycle
  output logic        mon_up_ei_o,
  input  logic        dn_sub_i,             // the ep receives dn_sub_* instead
  input  logic [19:0] dn_sub_data_i,
  input  logic        dn_sub_ei_i,
  input  logic        up_sub_i,             // rc receives up_sub_* instead
  input  logic [19:0] up_sub_data_i,
  input  logic        up_sub_ei_i,

  // Taps of the rc nets (rc MAS, Nets)
  output logic        mon_cmd_valid_o,
  output logic        mon_cmd_ready_o,
  output logic [3:0]  mon_cmd_op_o,
  output logic [31:0] mon_cmd_addr_o,
  output logic [5:0]  mon_cmd_len_o,
  output logic [3:0]  mon_cmd_first_be_o,
  output logic [3:0]  mon_cmd_last_be_o,
  output logic [31:0] mon_cmd_data_o,
  output logic        mon_rsp_valid_o,
  output logic [2:0]  mon_rsp_status_o,
  output logic [31:0] mon_rsp_data_o,
  output logic        mon_rsp_last_o,
  output logic        mon_msi_valid_o,
  output logic        mon_msi_ready_o,
  output logic [9:0]  mon_msi_offset_o,
  output logic [31:0] mon_msi_data_o,
  output logic        mon_tx_valid_o,
  output logic        mon_tx_ready_o,
  output logic [31:0] mon_tx_data_o,
  output logic        mon_tx_sop_o,
  output logic        mon_tx_eop_o,
  output logic        mon_rx_valid_o,
  output logic        mon_rx_ready_o,
  output logic [31:0] mon_rx_data_o,
  output logic        mon_rx_sop_o,
  output logic        mon_rx_eop_o,
  output logic        mon_hot_reset_req_o,

  // Taps of the ep's tl-cfg nets (core MAS)
  output logic [8:0]  mon_tl_err_o,
  output logic        mon_tl_np_pending_o,

  // Parameter echo
  output logic [11:0] cfg_fc_ph_o,
  output logic [11:0] cfg_fc_pd_o,
  output logic [11:0] cfg_fc_nph_o,
  output logic [11:0] cfg_fc_npd_o,
  output logic [10:0] cfg_cpl_timeout_div_o,
  output logic [10:0] cfg_rp_cpl_timeout_div_o,
  output logic [7:0]  cfg_program_o,
  output logic [4:0]  cfg_bar0_addr_w_o,
  output logic [6:0]  cfg_msix_table_size_o,
  output logic [15:0] cfg_vendor_id_o,
  output logic [15:0] cfg_device_id_o,
  output logic [15:0] cfg_subsys_vendor_id_o,
  output logic [15:0] cfg_subsys_id_o,
  output logic [23:0] cfg_class_code_o,
  output logic [7:0]  cfg_revision_id_o
);

  logic [19:0] dn_tx, up_tx;
  logic        dn_ei, up_ei;
  logic [19:0] dn_q, up_q;           // one word of channel delay each way
  logic        dn_ei_q, up_ei_q;

  always_ff @(posedge clk_i) begin
    dn_q    <= dn_tx;
    dn_ei_q <= dn_ei;
    up_q    <= up_tx;
    up_ei_q <= up_ei;
  end

  assign mon_dn_tx_o = dn_tx;
  assign mon_dn_ei_o = dn_ei;
  assign mon_up_tx_o = up_tx;
  assign mon_up_ei_o = up_ei;

  pcie_rc #(
    .FC_PH(FC_PH), .FC_PD(FC_PD), .FC_NPH(FC_NPH), .FC_NPD(FC_NPD),
    .TIMEOUT_DIV(TIMEOUT_DIV), .ERR_TIMEOUT_DIV(ERR_TIMEOUT_DIV), .CPL_TIMEOUT_DIV(CPL_TIMEOUT_DIV),
    .PROGRAM(PROGRAM)
  ) u_rc (
    .clk_i(clk_i), .rst_i(rp_rst_i),
    .start_i(rp_start_i), .done_o(rp_done_o), .pass_o(rp_pass_o),
    .fail_step_o(rp_fail_step_o),
    .dl_up_o(rp_dl_up_o), .dl_state_o(rp_dl_state_o), .dl_err_o(rp_dl_err_o),
    .link_up_o(rp_link_up_o), .ltssm_state_o(rp_ltssm_state_o), .rcv_err_o(rp_rcv_err_o),
    .ser_tx_data_o(dn_tx), .ser_tx_elec_idle_o(dn_ei), .ser_tx_rxdet_i(ser_tx_rxdet_i),
    .ser_rx_clk_i(clk_i),
    .ser_rx_data_i(up_sub_i ? up_sub_data_i : up_q),
    .ser_rx_elec_idle_i(up_sub_i ? up_sub_ei_i : up_ei_q)
  );
  assign rp_ser_tx_elec_idle_o = dn_ei;

  pcie_core #(
    .FC_PH(FC_PH), .FC_PD(FC_PD), .FC_NPH(FC_NPH), .FC_NPD(FC_NPD),
    .TIMEOUT_DIV(TIMEOUT_DIV), .ERR_TIMEOUT_DIV(ERR_TIMEOUT_DIV), .CPL_TIMEOUT_DIV(EP_CPL_TIMEOUT_DIV)
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
    .ser_tx_data_o(up_tx), .ser_tx_elec_idle_o(up_ei), .ser_tx_rxdet_i(ser_tx_rxdet_i),
    .ser_rx_clk_i(clk_i),
    .ser_rx_data_i(dn_sub_i ? dn_sub_data_i : dn_q),
    .ser_rx_elec_idle_i(dn_sub_i ? dn_sub_ei_i : dn_ei_q)
  );
  assign ser_tx_elec_idle_o = up_ei;

  // rc taps
  assign mon_cmd_valid_o    = u_rc.cmd_valid;
  assign mon_cmd_ready_o    = u_rc.cmd_ready;
  assign mon_cmd_op_o       = u_rc.cmd.op;
  assign mon_cmd_addr_o     = u_rc.cmd.addr;
  assign mon_cmd_len_o      = u_rc.cmd.len;
  assign mon_cmd_first_be_o = u_rc.cmd.first_be;
  assign mon_cmd_last_be_o  = u_rc.cmd.last_be;
  assign mon_cmd_data_o     = u_rc.cmd.data;
  assign mon_rsp_valid_o    = u_rc.rsp_valid;
  assign mon_rsp_status_o   = u_rc.rsp.status;
  assign mon_rsp_data_o     = u_rc.rsp.data;
  assign mon_rsp_last_o     = u_rc.rsp.last;
  assign mon_msi_valid_o    = u_rc.msi_valid;
  assign mon_msi_ready_o    = u_rc.msi_ready;
  assign mon_msi_offset_o   = u_rc.msi.offset;
  assign mon_msi_data_o     = u_rc.msi.data;
  assign mon_tx_valid_o     = u_rc.tlp_tx_valid;
  assign mon_tx_ready_o     = u_rc.tlp_tx_ready;
  assign mon_tx_data_o      = u_rc.tlp_tx_data;
  assign mon_tx_sop_o       = u_rc.tlp_tx_sop;
  assign mon_tx_eop_o       = u_rc.tlp_tx_eop;
  assign mon_rx_valid_o     = u_rc.tlp_rx_valid;
  assign mon_rx_ready_o     = u_rc.tlp_rx_ready;
  assign mon_rx_data_o      = u_rc.tlp_rx_data;
  assign mon_rx_sop_o       = u_rc.tlp_rx_sop;
  assign mon_rx_eop_o       = u_rc.tlp_rx_eop;
  assign mon_hot_reset_req_o = u_rc.hot_reset_req;

  assign mon_tl_err_o        = u_ep.tl_err;
  assign mon_tl_np_pending_o = u_ep.tl_np_pending;

  // The ep runs at the core defaults, which the rc_seq programs assume.
  assign cfg_fc_ph_o              = 12'(FC_PH);
  assign cfg_fc_pd_o              = 12'(FC_PD);
  assign cfg_fc_nph_o             = 12'(FC_NPH);
  assign cfg_fc_npd_o             = 12'(FC_NPD);
  assign cfg_cpl_timeout_div_o    = 11'(EP_CPL_TIMEOUT_DIV);
  assign cfg_rp_cpl_timeout_div_o = 11'(CPL_TIMEOUT_DIV);
  assign cfg_program_o            = 8'(PROGRAM);
  assign cfg_bar0_addr_w_o        = 5'd12;
  assign cfg_msix_table_size_o    = 7'd8;
  assign cfg_vendor_id_o          = 16'h0000;
  assign cfg_device_id_o          = 16'h0000;
  assign cfg_subsys_vendor_id_o   = 16'h0000;
  assign cfg_subsys_id_o          = 16'h0000;
  assign cfg_class_code_o         = 24'hFF0000;
  assign cfg_revision_id_o        = 8'h00;

endmodule

`default_nettype wire
