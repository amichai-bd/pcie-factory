// Gen1 x1 link stack: pcie_dll over pcie_phy.
// Interfaces and behavior: wiki/src/arch/link/HAS.md; structure:
// wiki/src/rtl/link/MAS.md.
//
// Thin integration (HAS, stack wiring): no logic of its own but one OR.
// The upper TLP stream, the credits and the data link status go straight
// to pcie_dll, the link control and the lower symbol interface straight to
// pcie_phy, and the two IPs meet on the packet stream. The phy's nullify
// input and power-state request are tied off (the dll never nullifies; the
// link holds the phy at L0), and the phy retrain request is the OR of the
// port and the dll's REPLAY_NUM rollover pulse.
`default_nettype none

module pcie_link #(
  parameter int unsigned FC_PH           = 8,  // posted header credits advertised; 0 infinite, else 1..127
  parameter int unsigned FC_PD           = 32, // posted data credits; 0 infinite, else 8..2047
  parameter int unsigned FC_NPH          = 8,  // non-posted header credits; 0 infinite, else 1..127
  parameter int unsigned FC_NPD          = 8,  // non-posted data credits; 0 infinite, else 2..2047
  parameter int unsigned FC_CPLH         = 0,  // completion header credits; 0 infinite (default), else 1..127
  parameter int unsigned FC_CPLD         = 0,  // completion data credits; 0 infinite (default), else 8..2047
  parameter int unsigned TIMEOUT_DIV     = 1,  // 1..1024: divides Detect.Quiet and Hot Reset waits; 1 for silicon
  parameter int unsigned ERR_TIMEOUT_DIV = 1   // 1..1024: divides error-path timeouts (floored); 1 for silicon
) (
  // Clocks, reset and strap
  input  logic        clk_i,            // core clock, 125 MHz
  input  logic        rst_i,            // synchronous to clk_i, active high // cdc-domain: clk_i
  input  logic        port_role_i,      // 0 downstream, 1 upstream; static outside reset // cdc-domain: clk_i

  // Upper TLP stream, transmit (transaction layer to link)
  input  logic        tlp_tx_valid_i,   // a transmit beat is offered // cdc-domain: clk_i
  output logic        tlp_tx_ready_o,
  input  logic [31:0] tlp_tx_data_i,    // one DW, [7:0] is the earlier byte // cdc-domain: clk_i
  input  logic        tlp_tx_sop_i,     // first beat of a TLP // cdc-domain: clk_i
  input  logic        tlp_tx_eop_i,     // last beat of a TLP // cdc-domain: clk_i

  // Upper TLP stream, receive (link to transaction layer)
  output logic        tlp_rx_valid_o,
  input  logic        tlp_rx_ready_i,   // the upper layer takes the beat // cdc-domain: clk_i
  output logic [31:0] tlp_rx_data_o,    // one DW, [7:0] is the earlier byte
  output logic        tlp_rx_sop_o,
  output logic        tlp_rx_eop_o,

  // Transmit credits available at the partner; all ones is infinite
  output logic [7:0]  fc_ph_avail_o,
  output logic [11:0] fc_pd_avail_o,
  output logic [7:0]  fc_nph_avail_o,
  output logic [11:0] fc_npd_avail_o,
  output logic [7:0]  fc_cplh_avail_o,
  output logic [11:0] fc_cpld_avail_o,

  // Data link status
  output logic        dl_up_o,
  output logic [1:0]  dl_state_o,       // 0 DL_Inactive, 1 FC_INIT1, 2 FC_INIT2, 3 DL_Active
  output logic [6:0]  dl_err_o,         // one-cycle error pulses, bit order in the dll HAS

  // Physical link control and status
  output logic        link_up_o,
  output logic [5:0]  ltssm_state_o,
  output logic        rcv_err_o,        // one-cycle pulse per receiver error
  input  logic        retrain_req_i,    // pulse: L0 to Recovery // cdc-domain: clk_i
  input  logic        hot_reset_req_i,  // level, downstream role only // cdc-domain: clk_i
  input  logic        disable_req_i,    // level, downstream role only // cdc-domain: clk_i
  input  logic        loopback_req_i,   // level: Loopback master // cdc-domain: clk_i

  // Lower 2x10-bit symbol interface, transmit (clk_i)
  output logic [19:0] ser_tx_data_o,    // [9:0] first symbol, bit 0 first on the line
  output logic        ser_tx_elec_idle_o,
  input  logic        ser_tx_rxdet_i,   // far-end receiver termination present // cdc-domain: clk_i

  // Lower 2x10-bit symbol interface, receive (ser_rx_clk_i)
  input  logic        ser_rx_clk_i,     // recovered clock, 125 MHz nominal
  input  logic [19:0] ser_rx_data_i,    // bit 0 earliest; no symbol alignment // cdc-domain: ser_rx_clk_i
  input  logic        ser_rx_elec_idle_i // the line is in electrical idle // cdc-domain: ser_rx_clk_i
);

  // The packet stream between the two IPs (dll HAS, lower packet stream):
  // flat, as both IPs name its ports.
  logic        pkt_tx_valid;
  logic        pkt_tx_ready;
  logic [15:0] pkt_tx_data;
  logic        pkt_tx_sop;
  logic        pkt_tx_eop;
  logic        pkt_tx_dllp;
  logic        pkt_rx_valid;
  logic [15:0] pkt_rx_data;
  logic        pkt_rx_sop;
  logic        pkt_rx_eop;
  logic        pkt_rx_dllp;
  logic        pkt_rx_err;
  logic        pkt_rx_nullify;
  logic        dll_retrain_req;    // one-cycle pulse on a REPLAY_NUM rollover
  logic        phy_retrain_req;

  // Either source retrains the link; each is a one-cycle pulse (HAS, stack wiring).
  assign phy_retrain_req = retrain_req_i | dll_retrain_req;

  pcie_dll #(
    .FC_PH   (FC_PH),
    .FC_PD   (FC_PD),
    .FC_NPH  (FC_NPH),
    .FC_NPD  (FC_NPD),
    .FC_CPLH (FC_CPLH),
    .FC_CPLD (FC_CPLD)
  ) u_dll (
    .clk_i            (clk_i),
    .rst_i            (rst_i),
    .tlp_tx_valid_i   (tlp_tx_valid_i),
    .tlp_tx_ready_o   (tlp_tx_ready_o),
    .tlp_tx_data_i    (tlp_tx_data_i),
    .tlp_tx_sop_i     (tlp_tx_sop_i),
    .tlp_tx_eop_i     (tlp_tx_eop_i),
    .tlp_rx_valid_o   (tlp_rx_valid_o),
    .tlp_rx_ready_i   (tlp_rx_ready_i),
    .tlp_rx_data_o    (tlp_rx_data_o),
    .tlp_rx_sop_o     (tlp_rx_sop_o),
    .tlp_rx_eop_o     (tlp_rx_eop_o),
    .fc_ph_avail_o    (fc_ph_avail_o),
    .fc_pd_avail_o    (fc_pd_avail_o),
    .fc_nph_avail_o   (fc_nph_avail_o),
    .fc_npd_avail_o   (fc_npd_avail_o),
    .fc_cplh_avail_o  (fc_cplh_avail_o),
    .fc_cpld_avail_o  (fc_cpld_avail_o),
    .dl_up_o          (dl_up_o),
    .dl_state_o       (dl_state_o),
    .err_o            (dl_err_o),
    .pkt_tx_valid_o   (pkt_tx_valid),
    .pkt_tx_ready_i   (pkt_tx_ready),
    .pkt_tx_data_o    (pkt_tx_data),
    .pkt_tx_sop_o     (pkt_tx_sop),
    .pkt_tx_eop_o     (pkt_tx_eop),
    .pkt_tx_dllp_o    (pkt_tx_dllp),
    .pkt_rx_valid_i   (pkt_rx_valid),
    .pkt_rx_data_i    (pkt_rx_data),
    .pkt_rx_sop_i     (pkt_rx_sop),
    .pkt_rx_eop_i     (pkt_rx_eop),
    .pkt_rx_dllp_i    (pkt_rx_dllp),
    .pkt_rx_err_i     (pkt_rx_err),
    .pkt_rx_nullify_i (pkt_rx_nullify),
    .link_up_i        (link_up_o),
    .ltssm_state_i    (ltssm_state_o),
    .retrain_req_o    (dll_retrain_req)
  );

  pcie_phy #(
    .TIMEOUT_DIV     (TIMEOUT_DIV),
    .ERR_TIMEOUT_DIV (ERR_TIMEOUT_DIV)
  ) u_phy (
    .clk_i              (clk_i),
    .rst_i              (rst_i),
    .port_role_i        (port_role_i),
    .pkt_tx_valid_i     (pkt_tx_valid),
    .pkt_tx_ready_o     (pkt_tx_ready),
    .pkt_tx_data_i      (pkt_tx_data),
    .pkt_tx_sop_i       (pkt_tx_sop),
    .pkt_tx_eop_i       (pkt_tx_eop),
    .pkt_tx_dllp_i      (pkt_tx_dllp),
    .pkt_tx_nullify_i   (1'b0),        // the dll never nullifies
    .pkt_rx_valid_o     (pkt_rx_valid),
    .pkt_rx_data_o      (pkt_rx_data),
    .pkt_rx_sop_o       (pkt_rx_sop),
    .pkt_rx_eop_o       (pkt_rx_eop),
    .pkt_rx_dllp_o      (pkt_rx_dllp),
    .pkt_rx_err_o       (pkt_rx_err),
    .pkt_rx_nullify_o   (pkt_rx_nullify),
    .link_up_o          (link_up_o),
    .ltssm_state_o      (ltssm_state_o),
    .rcv_err_o          (rcv_err_o),
    .retrain_req_i      (phy_retrain_req),
    .hot_reset_req_i    (hot_reset_req_i),
    .disable_req_i      (disable_req_i),
    .loopback_req_i     (loopback_req_i),
    .pm_state_req_i     (2'd0),        // L0 only: no L0s, L1 or L2 (HAS, power states)
    .ser_tx_data_o      (ser_tx_data_o),
    .ser_tx_elec_idle_o (ser_tx_elec_idle_o),
    .ser_tx_rxdet_i     (ser_tx_rxdet_i),
    .ser_rx_clk_i       (ser_rx_clk_i),
    .ser_rx_data_i      (ser_rx_data_i),
    .ser_rx_elec_idle_i (ser_rx_elec_idle_i)
  );

endmodule

`default_nettype wire
