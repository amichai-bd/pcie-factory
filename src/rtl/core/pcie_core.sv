// PCI Express Endpoint: pcie_tl and pcie_cfg over pcie_link.
// Interfaces and behavior: wiki/src/arch/core/HAS.md, APP.md and
// TL_CFG.md; structure: wiki/src/rtl/core/MAS.md.
//
// Thin integration (HAS, stack wiring): no logic of its own but the cfg
// reset, the OR of rst_i and data link down, and the gate that holds the
// cfg outputs to the application at their reset values in every cycle of
// that reset (cfg resets at the next edge). pcie_link carries the upper
// TLP stream, the credits and the credit release to pcie_tl port for
// port; pcie_tl and pcie_cfg meet on the tl-cfg interface (TL_CFG.md);
// the application streams go to pcie_tl, the interrupt port and the
// function status to pcie_cfg. The link control inputs are tied off for an
// upstream port that never requests Hot Reset, Disabled, Loopback or a
// retrain.
`default_nettype none

module pcie_core #(
  parameter int unsigned FC_PH            = 8,         // posted header credits advertised, 1..127
  parameter int unsigned FC_PD            = 32,        // posted data credits, 8..2047
  parameter int unsigned FC_NPH           = 8,         // non-posted header credits, 1..127
  parameter int unsigned FC_NPD           = 8,         // non-posted data credits, 2..2047
  parameter int unsigned TIMEOUT_DIV      = 1,         // 1..1024, passed to link; 1 for silicon
  parameter int unsigned ERR_TIMEOUT_DIV  = 1,         // 1..1024, passed to link; 1 for silicon
  parameter int unsigned CPL_TIMEOUT_DIV  = 1,         // 1..1024, divides the tl completion timeout; 1 for silicon
  parameter int unsigned BAR0_ADDR_W      = 12,        // 12..28: BAR0 is 2**BAR0_ADDR_W bytes
  parameter int unsigned MSIX_TABLE_SIZE  = 8,         // 1..64 MSI-X vectors
  parameter logic [15:0] VENDOR_ID        = 16'h0000,  // placeholder, claims no assigned vendor
  parameter logic [15:0] DEVICE_ID        = 16'h0000,  // placeholder
  parameter logic [15:0] SUBSYS_VENDOR_ID = 16'h0000,  // placeholder
  parameter logic [15:0] SUBSYS_ID        = 16'h0000,  // placeholder
  parameter logic [23:0] CLASS_CODE       = 24'hFF0000, // base class FFh: fits no defined class
  parameter logic [7:0]  REVISION_ID      = 8'h00
) (
  // Clock and reset
  input  logic                   clk_i,             // core clock, 125 MHz
  input  logic                   rst_i,             // synchronous to clk_i, active high // cdc-domain: clk_i

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
  output logic                   rc_done_o,         // with eop: the read is finished

  // Interrupts
  input  logic                   irq_valid_i,       // cdc-domain: clk_i
  output logic                   irq_ready_o,
  input  logic [5:0]             irq_vector_i,      // cdc-domain: clk_i
  output logic                   irq_done_o,
  output logic [1:0]             irq_status_o,      // 0 sent, 1 pending, 2 dropped

  // Function status
  output logic                   bus_master_en_o,
  output logic                   mem_space_en_o,
  output logic [1:0]             d_state_o,         // 0 D0, 3 D3hot
  output logic [2:0]             max_payload_o,     // Device Control encoding, 0 is 128 bytes
  output logic [2:0]             max_read_req_o,    // Device Control encoding, reset 2 (512 bytes)

  // Data link status
  output logic                   dl_up_o,
  output logic [1:0]             dl_state_o,        // 0 DL_Inactive, 1 FC_INIT1, 2 FC_INIT2, 3 DL_Active
  output logic [6:0]             dl_err_o,          // one-cycle error pulses, bit order in the dll HAS

  // Physical link status
  output logic                   link_up_o,
  output logic [5:0]             ltssm_state_o,
  output logic                   rcv_err_o,         // one-cycle pulse per receiver error

  // Lower 2x10-bit symbol interface, transmit (clk_i)
  output logic [19:0]            ser_tx_data_o,     // [9:0] first symbol, bit 0 first on the line
  output logic                   ser_tx_elec_idle_o,
  input  logic                   ser_tx_rxdet_i,    // far-end receiver termination present // cdc-domain: clk_i

  // Lower 2x10-bit symbol interface, receive (ser_rx_clk_i)
  input  logic                   ser_rx_clk_i,      // recovered clock, 125 MHz nominal
  input  logic [19:0]            ser_rx_data_i,     // bit 0 earliest; no symbol alignment // cdc-domain: ser_rx_clk_i
  input  logic                   ser_rx_elec_idle_i // the line is in electrical idle // cdc-domain: ser_rx_clk_i
);

  // The tl-cfg interface (TL_CFG.md): flat nets, as both IPs name these
  // ports (core HAS, decisions).
  logic        reg_req_valid;
  logic        reg_req_ready;
  logic        reg_req_bar2;
  logic        reg_req_write;
  logic [9:0]  reg_req_addr;
  logic [3:0]  reg_req_be;
  logic [31:0] reg_req_data;
  logic [7:0]  reg_req_bus;
  logic [4:0]  reg_req_dev;
  logic        reg_cpl_valid;
  logic [31:0] reg_cpl_data;
  logic [7:0]  bus_num;
  logic [4:0]  dev_num;
  logic [31:0] bar0_base;
  logic [31:0] bar2_base;
  logic        cpl_timeout_dis;
  logic        mem_space_en;
  logic        bus_master_en;
  logic [1:0]  d_state;
  logic        tl_np_pending;
  logic        slot_pwr_valid;
  logic [7:0]  slot_pwr_value;
  logic [1:0]  slot_pwr_scale;
  logic [8:0]  tl_err;
  logic        msg_valid;
  logic        msg_ready;
  logic [1:0]  msg_kind;
  logic [63:0] msg_addr;
  logic [31:0] msg_data;

  // link to tl: the upper TLP stream, the credits available, the credit
  // release and the data link status (HAS, stack wiring).
  logic        tlp_tx_valid;
  logic        tlp_tx_ready;
  logic [31:0] tlp_tx_data;
  logic        tlp_tx_sop;
  logic        tlp_tx_eop;
  logic        tlp_rx_valid;
  logic        tlp_rx_ready;
  logic [31:0] tlp_rx_data;
  logic        tlp_rx_sop;
  logic        tlp_rx_eop;
  logic [7:0]  fc_ph_avail;
  logic [11:0] fc_pd_avail;
  logic [7:0]  fc_nph_avail;
  logic [11:0] fc_npd_avail;
  logic [7:0]  fc_cplh_avail;
  logic [11:0] fc_cpld_avail;
  logic        fc_rel_p;
  logic [3:0]  fc_rel_pd;
  logic        fc_rel_np;
  logic [3:0]  fc_rel_npd;

  // cfg is held in reset while rst_i is 1 or the data link is down (HAS,
  // reset; Q10); tl takes dl_up_o itself (tl HAS) and has the same rule.
  logic        cfg_rst;
  assign cfg_rst = rst_i | ~dl_up_o;

  // The cfg outputs to the application: the interrupt port and the
  // function status (APP). cfg's reset is synchronous, so in the first
  // cycle of cfg_rst its registers still hold their old values; the HAS
  // reset values hold in every cycle where rst_i is 1 or dl_up_o is 0, so
  // each registered output is forced to its reset value while cfg_rst is 1
  // (irq_ready_o needs no gate: cfg already holds it at 0 in reset). tl
  // gates its own outputs the same way; the tl-cfg nets need no gate, tl
  // being in reset in those cycles.
  logic       cfg_irq_done;
  logic [2:0] cfg_max_payload;
  logic [2:0] cfg_max_read_req;

  assign bus_master_en_o = bus_master_en & ~cfg_rst;
  assign mem_space_en_o  = mem_space_en & ~cfg_rst;
  assign d_state_o       = cfg_rst ? 2'd0 : d_state;                 // D0
  assign irq_done_o      = cfg_irq_done & ~cfg_rst;
  assign max_payload_o   = cfg_rst ? 3'd0 : cfg_max_payload;         // 128 bytes
  assign max_read_req_o  = cfg_rst ? 3'd2 : cfg_max_read_req;        // 512 bytes

  pcie_link #(
    .FC_PH           (FC_PH),
    .FC_PD           (FC_PD),
    .FC_NPH          (FC_NPH),
    .FC_NPD          (FC_NPD),
    .FC_CPLH         (0),               // infinite: an Endpoint advertises infinite completion credits
    .FC_CPLD         (0),
    .FC_RELEASE      (1),               // P and NP credits return when a request leaves tl
    .TIMEOUT_DIV     (TIMEOUT_DIV),
    .ERR_TIMEOUT_DIV (ERR_TIMEOUT_DIV)
  ) u_link (
    .clk_i              (clk_i),
    .rst_i              (rst_i),
    .port_role_i        (1'b1),         // upstream port (Q1)
    .tlp_tx_valid_i     (tlp_tx_valid),
    .tlp_tx_ready_o     (tlp_tx_ready),
    .tlp_tx_data_i      (tlp_tx_data),
    .tlp_tx_sop_i       (tlp_tx_sop),
    .tlp_tx_eop_i       (tlp_tx_eop),
    .tlp_rx_valid_o     (tlp_rx_valid),
    .tlp_rx_ready_i     (tlp_rx_ready),
    .tlp_rx_data_o      (tlp_rx_data),
    .tlp_rx_sop_o       (tlp_rx_sop),
    .tlp_rx_eop_o       (tlp_rx_eop),
    .fc_ph_avail_o      (fc_ph_avail),
    .fc_pd_avail_o      (fc_pd_avail),
    .fc_nph_avail_o     (fc_nph_avail),
    .fc_npd_avail_o     (fc_npd_avail),
    .fc_cplh_avail_o    (fc_cplh_avail),
    .fc_cpld_avail_o    (fc_cpld_avail),
    .fc_rel_p_i         (fc_rel_p),
    .fc_rel_pd_i        (fc_rel_pd),
    .fc_rel_np_i        (fc_rel_np),
    .fc_rel_npd_i       (fc_rel_npd),
    .dl_up_o            (dl_up_o),
    .dl_state_o         (dl_state_o),
    .dl_err_o           (dl_err_o),
    .link_up_o          (link_up_o),
    .ltssm_state_o      (ltssm_state_o),
    .rcv_err_o          (rcv_err_o),
    .retrain_req_i      (1'b0),         // a downstream port's request (Link Control)
    .hot_reset_req_i    (1'b0),         // downstream role only
    .disable_req_i      (1'b0),         // downstream role only
    .loopback_req_i     (1'b0),         // Loopback master is not used
    .ser_tx_data_o      (ser_tx_data_o),
    .ser_tx_elec_idle_o (ser_tx_elec_idle_o),
    .ser_tx_rxdet_i     (ser_tx_rxdet_i),
    .ser_rx_clk_i       (ser_rx_clk_i),
    .ser_rx_data_i      (ser_rx_data_i),
    .ser_rx_elec_idle_i (ser_rx_elec_idle_i)
  );

  pcie_tl #(
    .FC_PH           (FC_PH),
    .FC_PD           (FC_PD),
    .FC_NPH          (FC_NPH),
    .FC_NPD          (FC_NPD),
    .CPL_TIMEOUT_DIV (CPL_TIMEOUT_DIV),
    .BAR0_ADDR_W     (BAR0_ADDR_W)
  ) u_tl (
    .clk_i             (clk_i),
    .rst_i             (rst_i),
    .tlp_tx_valid_o    (tlp_tx_valid),
    .tlp_tx_ready_i    (tlp_tx_ready),
    .tlp_tx_data_o     (tlp_tx_data),
    .tlp_tx_sop_o      (tlp_tx_sop),
    .tlp_tx_eop_o      (tlp_tx_eop),
    .tlp_rx_valid_i    (tlp_rx_valid),
    .tlp_rx_ready_o    (tlp_rx_ready),
    .tlp_rx_data_i     (tlp_rx_data),
    .tlp_rx_sop_i      (tlp_rx_sop),
    .tlp_rx_eop_i      (tlp_rx_eop),
    .fc_ph_avail_i     (fc_ph_avail),
    .fc_pd_avail_i     (fc_pd_avail),
    .fc_nph_avail_i    (fc_nph_avail),
    .fc_npd_avail_i    (fc_npd_avail),
    .fc_cplh_avail_i   (fc_cplh_avail),
    .fc_cpld_avail_i   (fc_cpld_avail),
    .dl_up_i           (dl_up_o),
    .fc_rel_p_o        (fc_rel_p),
    .fc_rel_pd_o       (fc_rel_pd),
    .fc_rel_np_o       (fc_rel_np),
    .fc_rel_npd_o      (fc_rel_npd),
    .reg_req_valid_o   (reg_req_valid),
    .reg_req_ready_i   (reg_req_ready),
    .reg_req_bar2_o    (reg_req_bar2),
    .reg_req_write_o   (reg_req_write),
    .reg_req_addr_o    (reg_req_addr),
    .reg_req_be_o      (reg_req_be),
    .reg_req_data_o    (reg_req_data),
    .reg_req_bus_o     (reg_req_bus),
    .reg_req_dev_o     (reg_req_dev),
    .reg_cpl_valid_i   (reg_cpl_valid),
    .reg_cpl_data_i    (reg_cpl_data),
    .bus_num_i         (bus_num),
    .dev_num_i         (dev_num),
    .mem_space_en_i    (mem_space_en),
    .bus_master_en_i   (bus_master_en),
    .d_state_i         (d_state),
    .bar0_base_i       (bar0_base),
    .bar2_base_i       (bar2_base),
    .cpl_timeout_dis_i (cpl_timeout_dis),
    .tl_err_o          (tl_err),
    .tl_np_pending_o   (tl_np_pending),
    .slot_pwr_valid_o  (slot_pwr_valid),
    .slot_pwr_value_o  (slot_pwr_value),
    .slot_pwr_scale_o  (slot_pwr_scale),
    .msg_valid_i       (msg_valid),
    .msg_ready_o       (msg_ready),
    .msg_kind_i        (msg_kind),
    .msg_addr_i        (msg_addr),
    .msg_data_i        (msg_data),
    .cq_valid_o        (cq_valid_o),
    .cq_ready_i        (cq_ready_i),
    .cq_sop_o          (cq_sop_o),
    .cq_eop_o          (cq_eop_o),
    .cq_data_o         (cq_data_o),
    .cq_write_o        (cq_write_o),
    .cq_addr_o         (cq_addr_o),
    .cq_len_o          (cq_len_o),
    .cq_first_be_o     (cq_first_be_o),
    .cq_last_be_o      (cq_last_be_o),
    .cq_handle_o       (cq_handle_o),
    .cc_valid_i        (cc_valid_i),
    .cc_ready_o        (cc_ready_o),
    .cc_sop_i          (cc_sop_i),
    .cc_eop_i          (cc_eop_i),
    .cc_data_i         (cc_data_i),
    .cc_handle_i       (cc_handle_i),
    .rq_valid_i        (rq_valid_i),
    .rq_ready_o        (rq_ready_o),
    .rq_sop_i          (rq_sop_i),
    .rq_eop_i          (rq_eop_i),
    .rq_data_i         (rq_data_i),
    .rq_write_i        (rq_write_i),
    .rq_addr_i         (rq_addr_i),
    .rq_len_i          (rq_len_i),
    .rq_first_be_i     (rq_first_be_i),
    .rq_last_be_i      (rq_last_be_i),
    .rq_tag_i          (rq_tag_i),
    .rc_valid_o        (rc_valid_o),
    .rc_ready_i        (rc_ready_i),
    .rc_sop_o          (rc_sop_o),
    .rc_eop_o          (rc_eop_o),
    .rc_data_o         (rc_data_o),
    .rc_tag_o          (rc_tag_o),
    .rc_status_o       (rc_status_o),
    .rc_done_o         (rc_done_o)
  );

  pcie_cfg #(
    .BAR0_ADDR_W      (BAR0_ADDR_W),
    .MSIX_TABLE_SIZE  (MSIX_TABLE_SIZE),
    .VENDOR_ID        (VENDOR_ID),
    .DEVICE_ID        (DEVICE_ID),
    .SUBSYS_VENDOR_ID (SUBSYS_VENDOR_ID),
    .SUBSYS_ID        (SUBSYS_ID),
    .CLASS_CODE       (CLASS_CODE),
    .REVISION_ID      (REVISION_ID)
  ) u_cfg (
    .clk_i             (clk_i),
    .rst_i             (cfg_rst),
    .reg_req_valid_i   (reg_req_valid),
    .reg_req_ready_o   (reg_req_ready),
    .reg_req_bar2_i    (reg_req_bar2),
    .reg_req_write_i   (reg_req_write),
    .reg_req_addr_i    (reg_req_addr),
    .reg_req_be_i      (reg_req_be),
    .reg_req_data_i    (reg_req_data),
    .reg_req_bus_i     (reg_req_bus),
    .reg_req_dev_i     (reg_req_dev),
    .reg_cpl_valid_o   (reg_cpl_valid),
    .reg_cpl_data_o    (reg_cpl_data),
    .bus_num_o         (bus_num),
    .dev_num_o         (dev_num),
    .bar0_base_o       (bar0_base),
    .bar2_base_o       (bar2_base),
    .cpl_timeout_dis_o (cpl_timeout_dis),
    .mem_space_en_o    (mem_space_en),
    .bus_master_en_o   (bus_master_en),
    .d_state_o         (d_state),
    .tl_np_pending_i   (tl_np_pending),
    .slot_pwr_valid_i  (slot_pwr_valid),
    .slot_pwr_value_i  (slot_pwr_value),
    .slot_pwr_scale_i  (slot_pwr_scale),
    .tl_err_i          (tl_err),
    .dl_err_i          (dl_err_o),
    .rcv_err_i         (rcv_err_o),
    .msg_valid_o       (msg_valid),
    .msg_ready_i       (msg_ready),
    .msg_kind_o        (msg_kind),
    .msg_addr_o        (msg_addr),
    .msg_data_o        (msg_data),
    .irq_valid_i       (irq_valid_i),
    .irq_ready_o       (irq_ready_o),
    .irq_vector_i      (irq_vector_i),
    .irq_done_o        (cfg_irq_done),
    .irq_status_o      (irq_status_o),
    .max_payload_o     (cfg_max_payload),
    .max_read_req_o    (cfg_max_read_req)
  );

endmodule

`default_nettype wire
