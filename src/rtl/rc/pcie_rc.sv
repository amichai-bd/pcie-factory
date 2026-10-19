// PCI Express root-port stack: pcie_rc_tl and pcie_rc_seq over pcie_link
// in the downstream role. Interfaces and behavior: wiki/src/arch/rc/HAS.md
// and TL_SEQ.md; structure: wiki/src/rtl/rc/MAS.md.
//
// The stack (MAS, Microarchitecture): pcie_rc_seq (u_seq) drives
// pcie_rc_tl (u_tl) over the tl-seq nets, and u_tl sits on the upper TLP
// stream of pcie_link (u_link) in the downstream role. rc adds no logic of
// its own: the nets between the three are named in the MAS for the e2e
// testbench's taps.
`default_nettype none

`include "common_macros.svh"

module pcie_rc #(
  parameter int unsigned FC_PH           = 8,              // posted header credits advertised, 1..127
  parameter int unsigned FC_PD           = 32,             // posted data credits, 8..2047
  parameter int unsigned FC_NPH          = 8,              // non-posted header credits, 1..127
  parameter int unsigned FC_NPD          = 8,              // non-posted data credits, 2..2047
  parameter int unsigned TIMEOUT_DIV     = 1,              // 1..1024, passed to link; 1 for silicon
  parameter int unsigned ERR_TIMEOUT_DIV = 1,              // 1..1024, passed to link; 1 for silicon
  parameter int unsigned CPL_TIMEOUT_DIV = 1,              // 1..1024, divides the rc_tl completion timeout
  parameter int unsigned HOST_MEM_ADDR_W = 12,             // 8..16: host memory is 2**HOST_MEM_ADDR_W bytes
  parameter logic [31:0] HOST_MEM_BASE   = 32'h0000_0000,  // system address of host memory, size aligned
  parameter logic [31:0] MSI_ADDR        = 32'hFEE0_0000,  // system address of the 4 KiB MSI window
  parameter logic [15:0] VENDOR_ID       = 16'h0000,       // placeholder, claims no assigned vendor
  parameter logic [15:0] DEVICE_ID       = 16'h0000,       // placeholder
  parameter logic [7:0]  REVISION_ID     = 8'h00,
  parameter int unsigned PROGRAM         = 1,              // rc_seq program id; 1 is MMIO (rc_seq MAS, Programs)
  parameter int unsigned LOWER           = 0,              // passed to link: 0 phy, 1 the GPIO adapter
  parameter int unsigned GPIO_CLK_DIV    = 2               // passed to link: the adapter's CLK_DIV
) (
  // Clock and reset
  input  logic        clk_i,              // core clock, 125 MHz
  input  logic        rst_i,              // synchronous to clk_i, active high // cdc-domain: clk_i

  // Sequencer
  input  logic        start_i,            // a rising edge starts a run when none is in progress // cdc-domain: clk_i
  output logic        done_o,             // the last run has ended
  output logic        pass_o,             // with done_o: the run passed
  output logic [15:0] fail_step_o,        // with done_o and not pass_o: ROM index of the failing step

  // Data link status
  output logic        dl_up_o,
  output logic [1:0]  dl_state_o,         // 0 DL_Inactive, 1 FC_INIT1, 2 FC_INIT2, 3 DL_Active
  output logic [6:0]  dl_err_o,           // one-cycle error pulses, bit order in the dll HAS

  // Physical link status
  output logic        link_up_o,
  output logic [5:0]  ltssm_state_o,
  output logic        rcv_err_o,          // one-cycle pulse per receiver error

  // Lower 2x10-bit symbol interface, transmit (clk_i)
  output logic [19:0] ser_tx_data_o,      // [9:0] first symbol, bit 0 first on the line
  output logic        ser_tx_elec_idle_o,
  input  logic        ser_tx_rxdet_i,     // far-end receiver termination present // cdc-domain: clk_i

  // Lower 2x10-bit symbol interface, receive (ser_rx_clk_i)
  input  logic        ser_rx_clk_i,       // recovered clock, 125 MHz nominal
  input  logic [19:0] ser_rx_data_i,      // bit 0 earliest; no symbol alignment // cdc-domain: ser_rx_clk_i
  input  logic        ser_rx_elec_idle_i, // the line is in electrical idle // cdc-domain: ser_rx_clk_i

  // GPIO pins (LOWER 1; link HAS, lower layer)
  output logic        gpio_tx_clk_o,      // forwarded clock, clk_i / GPIO_CLK_DIV
  output logic        gpio_tx_ctrl_o,
  output logic [3:0]  gpio_tx_data_o,
  // The partner's forwarded clock; at LOWER 0, the checked build, it reaches nothing.
  input  logic        gpio_rx_clk_i,      // cdc-domain: clk_i
  input  logic        gpio_rx_ctrl_i,     // cdc-domain: gpio_rx_clk_i
  input  logic [3:0]  gpio_rx_data_i      // cdc-domain: gpio_rx_clk_i
);

  // HAS parameter ranges that link alone would not refuse: rc never
  // advertises infinite posted or non-posted credits (HAS, Parameters).
  `COMMON_ASSERT_PARAM(g_fc_ph_check, FC_PH >= 1 && FC_PH <= 127, "pcie_rc: FC_PH must be 1 to 127")
  `COMMON_ASSERT_PARAM(g_fc_pd_check, FC_PD >= 8 && FC_PD <= 2047, "pcie_rc: FC_PD must be 8 to 2047")
  `COMMON_ASSERT_PARAM(g_fc_nph_check, FC_NPH >= 1 && FC_NPH <= 127, "pcie_rc: FC_NPH must be 1 to 127")
  `COMMON_ASSERT_PARAM(g_fc_npd_check, FC_NPD >= 2 && FC_NPD <= 2047, "pcie_rc: FC_NPD must be 2 to 2047")

  // ------------------------------------------------ tl-seq nets (TL_SEQ.md)

  logic                cmd_valid;
  logic                cmd_ready;
  common_pkg::t_rc_cmd cmd;
  logic                rsp_valid;
  common_pkg::t_rc_rsp rsp;
  logic                msi_valid;
  logic                msi_ready;
  common_pkg::t_rc_msi msi;
  logic                hot_reset_req;

  // ------------------------------------------ rc_tl to link (dll HAS, upper)

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

  // dl_up_o and link_up_o are ports and also feed u_seq and u_tl.
  logic        dl_up;
  logic        link_up;

  assign dl_up_o   = dl_up;
  assign link_up_o = link_up;

  pcie_rc_seq #(
    .PROGRAM (PROGRAM)
  ) u_seq (
    .clk_i           (clk_i),
    .rst_i           (rst_i),
    .start_i         (start_i),
    .done_o          (done_o),
    .pass_o          (pass_o),
    .fail_step_o     (fail_step_o),
    .dl_up_i         (dl_up),
    .link_up_i       (link_up),
    .hot_reset_req_o (hot_reset_req),
    .cmd_valid_o     (cmd_valid),
    .cmd_ready_i     (cmd_ready),
    .cmd_o           (cmd),
    .rsp_valid_i     (rsp_valid),
    .rsp_i           (rsp),
    .msi_valid_i     (msi_valid),
    .msi_ready_o     (msi_ready),
    .msi_i           (msi)
  );

  pcie_rc_tl #(
    .FC_NPH          (FC_NPH),
    .CPL_TIMEOUT_DIV (CPL_TIMEOUT_DIV),
    .HOST_MEM_ADDR_W (HOST_MEM_ADDR_W),
    .HOST_MEM_BASE   (HOST_MEM_BASE),
    .MSI_ADDR        (MSI_ADDR),
    .VENDOR_ID       (VENDOR_ID),
    .DEVICE_ID       (DEVICE_ID),
    .REVISION_ID     (REVISION_ID)
  ) u_tl (
    .clk_i           (clk_i),
    .rst_i           (rst_i),
    .cmd_valid_i     (cmd_valid),
    .cmd_ready_o     (cmd_ready),
    .cmd_i           (cmd),
    .rsp_valid_o     (rsp_valid),
    .rsp_o           (rsp),
    .msi_valid_o     (msi_valid),
    .msi_ready_i     (msi_ready),
    .msi_o           (msi),
    .tlp_tx_valid_o  (tlp_tx_valid),
    .tlp_tx_ready_i  (tlp_tx_ready),
    .tlp_tx_data_o   (tlp_tx_data),
    .tlp_tx_sop_o    (tlp_tx_sop),
    .tlp_tx_eop_o    (tlp_tx_eop),
    .tlp_rx_valid_i  (tlp_rx_valid),
    .tlp_rx_ready_o  (tlp_rx_ready),
    .tlp_rx_data_i   (tlp_rx_data),
    .tlp_rx_sop_i    (tlp_rx_sop),
    .tlp_rx_eop_i    (tlp_rx_eop),
    .fc_ph_avail_i   (fc_ph_avail),
    .fc_pd_avail_i   (fc_pd_avail),
    .fc_nph_avail_i  (fc_nph_avail),
    .fc_npd_avail_i  (fc_npd_avail),
    .fc_cplh_avail_i (fc_cplh_avail),
    .fc_cpld_avail_i (fc_cpld_avail),
    .fc_rel_p_o      (fc_rel_p),
    .fc_rel_pd_o     (fc_rel_pd),
    .fc_rel_np_o     (fc_rel_np),
    .fc_rel_npd_o    (fc_rel_npd),
    .dl_up_i         (dl_up)
  );

  pcie_link #(
    .FC_PH           (FC_PH),
    .FC_PD           (FC_PD),
    .FC_NPH          (FC_NPH),
    .FC_NPD          (FC_NPD),
    .FC_CPLH         (0),               // infinite: a root port that routes no peer-to-peer (HAS, Downstream role)
    .FC_CPLD         (0),
    .FC_RELEASE      (1),               // P and NP credits return when a request leaves rc_tl
    .TIMEOUT_DIV     (TIMEOUT_DIV),
    .ERR_TIMEOUT_DIV (ERR_TIMEOUT_DIV),
    .LOWER           (LOWER),           // at 1 link sets the GPIO build's replay timeout
    .GPIO_CLK_DIV    (GPIO_CLK_DIV)
  ) u_link (
    .clk_i              (clk_i),
    .rst_i              (rst_i),
    .port_role_i        (1'b0),         // downstream port (Q3)
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
    .dl_up_o            (dl_up),
    .dl_state_o         (dl_state_o),
    .dl_err_o           (dl_err_o),
    .link_up_o          (link_up),
    .ltssm_state_o      (ltssm_state_o),
    .rcv_err_o          (rcv_err_o),
    .retrain_req_i      (1'b0),         // no retrain command (HAS, Decisions)
    .hot_reset_req_i    (hot_reset_req),
    .disable_req_i      (1'b0),         // no Disabled command
    .loopback_req_i     (1'b0),         // no loopback command
    .ser_tx_data_o      (ser_tx_data_o),
    .ser_tx_elec_idle_o (ser_tx_elec_idle_o),
    .ser_tx_rxdet_i     (ser_tx_rxdet_i),
    .ser_rx_clk_i       (ser_rx_clk_i),
    .ser_rx_data_i      (ser_rx_data_i),
    .ser_rx_elec_idle_i (ser_rx_elec_idle_i),
    .gpio_tx_clk_o      (gpio_tx_clk_o),
    .gpio_tx_ctrl_o     (gpio_tx_ctrl_o),
    .gpio_tx_data_o     (gpio_tx_data_o),
    .gpio_rx_clk_i      (gpio_rx_clk_i),
    .gpio_rx_ctrl_i     (gpio_rx_ctrl_i),
    .gpio_rx_data_i     (gpio_rx_data_i)
  );

endmodule

`default_nettype wire
