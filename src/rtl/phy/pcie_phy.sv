// Gen1 x1 physical-layer stack: pcie_mac and pcie_pcs joined by PIPE.
// Interfaces and behavior: wiki/src/arch/phy/HAS.md; structure:
// wiki/src/rtl/phy/MAS.md.
//
// Thin integration: no logic of its own. The upper packet stream and link
// control go straight to pcie_mac, the lower symbol interface straight to
// pcie_pcs, and the two IPs meet on the PIPE nets of
// wiki/src/arch/phy/PIPE.md.
`default_nettype none

module pcie_phy #(
  parameter int unsigned TIMEOUT_DIV     = 1,  // 1..1024: divides Detect.Quiet and Hot Reset waits; 1 for silicon
  parameter int unsigned ERR_TIMEOUT_DIV = 1   // 1..1024: divides error-path timeouts (floored); 1 for silicon
) (
  // Clocks, reset and strap
  input  logic        clk_i,            // core clock and PCLK, 125 MHz
  input  logic        rst_i,            // cdc-domain: clk_i
  input  logic        port_role_i,      // cdc-domain: clk_i

  // Upper framed-packet stream, transmit (upper layer to link)
  input  logic        pkt_tx_valid_i,   // cdc-domain: clk_i
  output logic        pkt_tx_ready_o,
  input  logic [15:0] pkt_tx_data_i,    // cdc-domain: clk_i
  input  logic        pkt_tx_sop_i,     // cdc-domain: clk_i
  input  logic        pkt_tx_eop_i,     // cdc-domain: clk_i
  input  logic        pkt_tx_dllp_i,    // cdc-domain: clk_i
  input  logic        pkt_tx_nullify_i, // cdc-domain: clk_i

  // Upper framed-packet stream, receive (link to upper layer)
  output logic        pkt_rx_valid_o,
  output logic [15:0] pkt_rx_data_o,    // [7:0] is the earlier byte
  output logic        pkt_rx_sop_o,
  output logic        pkt_rx_eop_o,
  output logic        pkt_rx_dllp_o,
  output logic        pkt_rx_err_o,     // on the eop beat: bad or cut-short packet
  output logic        pkt_rx_nullify_o, // on the eop beat: ended by EDB, no error

  // Link control and status
  output logic        link_up_o,
  output logic [5:0]  ltssm_state_o,
  output logic        rcv_err_o,        // one-cycle pulse per receiver error
  input  logic        retrain_req_i,    // cdc-domain: clk_i
  input  logic        hot_reset_req_i,  // cdc-domain: clk_i
  input  logic        disable_req_i,    // cdc-domain: clk_i
  input  logic        loopback_req_i,   // cdc-domain: clk_i
  input  logic [1:0]  pm_state_req_i,   // cdc-domain: clk_i

  // Lower 2x10-bit symbol interface, transmit (clk_i)
  output logic [19:0] ser_tx_data_o,    // [9:0] first symbol, bit 0 first on the line
  output logic        ser_tx_elec_idle_o,
  input  logic        ser_tx_rxdet_i,   // cdc-domain: clk_i

  // Lower 2x10-bit symbol interface, receive (ser_rx_clk_i)
  input  logic        ser_rx_clk_i,     // recovered clock, 125 MHz nominal
  input  logic [19:0] ser_rx_data_i,    // cdc-domain: ser_rx_clk_i
  input  logic        ser_rx_elec_idle_i // cdc-domain: ser_rx_clk_i
);

  // PIPE nets (wiki/src/arch/phy/PIPE.md#signals): flat, as the PIPE
  // specification names them; each is pipe_<x>_o of one IP and pipe_<x>_i
  // of the other.
  logic        pipe_reset_n;
  logic [15:0] pipe_tx_data;
  logic [1:0]  pipe_tx_datak;
  logic        pipe_tx_detect_rx;
  logic        pipe_tx_elec_idle;
  logic        pipe_tx_compliance;
  logic        pipe_rx_polarity;
  logic [1:0]  pipe_power_down;
  logic [15:0] pipe_rx_data;
  logic [1:0]  pipe_rx_datak;
  logic        pipe_rx_valid;
  logic        pipe_phy_status;
  logic        pipe_rx_elec_idle;
  logic [2:0]  pipe_rx_status;

  pcie_mac #(
    .TIMEOUT_DIV     (TIMEOUT_DIV),
    .ERR_TIMEOUT_DIV (ERR_TIMEOUT_DIV)
  ) u_mac (
    .clk_i                (clk_i),
    .rst_i                (rst_i),
    .port_role_i          (port_role_i),
    .pkt_tx_valid_i       (pkt_tx_valid_i),
    .pkt_tx_ready_o       (pkt_tx_ready_o),
    .pkt_tx_data_i        (pkt_tx_data_i),
    .pkt_tx_sop_i         (pkt_tx_sop_i),
    .pkt_tx_eop_i         (pkt_tx_eop_i),
    .pkt_tx_dllp_i        (pkt_tx_dllp_i),
    .pkt_tx_nullify_i     (pkt_tx_nullify_i),
    .pkt_rx_valid_o       (pkt_rx_valid_o),
    .pkt_rx_data_o        (pkt_rx_data_o),
    .pkt_rx_sop_o         (pkt_rx_sop_o),
    .pkt_rx_eop_o         (pkt_rx_eop_o),
    .pkt_rx_dllp_o        (pkt_rx_dllp_o),
    .pkt_rx_err_o         (pkt_rx_err_o),
    .pkt_rx_nullify_o     (pkt_rx_nullify_o),
    .link_up_o            (link_up_o),
    .ltssm_state_o        (ltssm_state_o),
    .rcv_err_o            (rcv_err_o),
    .retrain_req_i        (retrain_req_i),
    .hot_reset_req_i      (hot_reset_req_i),
    .disable_req_i        (disable_req_i),
    .loopback_req_i       (loopback_req_i),
    .pm_state_req_i       (pm_state_req_i),
    .pipe_reset_no        (pipe_reset_n),
    .pipe_tx_data_o       (pipe_tx_data),
    .pipe_tx_datak_o      (pipe_tx_datak),
    .pipe_tx_detect_rx_o  (pipe_tx_detect_rx),
    .pipe_tx_elec_idle_o  (pipe_tx_elec_idle),
    .pipe_tx_compliance_o (pipe_tx_compliance),
    .pipe_rx_polarity_o   (pipe_rx_polarity),
    .pipe_power_down_o    (pipe_power_down),
    .pipe_rx_data_i       (pipe_rx_data),
    .pipe_rx_datak_i      (pipe_rx_datak),
    .pipe_rx_valid_i      (pipe_rx_valid),
    .pipe_phy_status_i    (pipe_phy_status),
    .pipe_rx_elec_idle_i  (pipe_rx_elec_idle),
    .pipe_rx_status_i     (pipe_rx_status)
  );

  pcie_pcs u_pcs (
    .clk_i                (clk_i),
    .rst_i                (rst_i),
    .pipe_reset_ni        (pipe_reset_n),
    .pipe_tx_data_i       (pipe_tx_data),
    .pipe_tx_datak_i      (pipe_tx_datak),
    .pipe_tx_detect_rx_i  (pipe_tx_detect_rx),
    .pipe_tx_elec_idle_i  (pipe_tx_elec_idle),
    .pipe_tx_compliance_i (pipe_tx_compliance),
    .pipe_rx_polarity_i   (pipe_rx_polarity),
    .pipe_power_down_i    (pipe_power_down),
    .pipe_rx_data_o       (pipe_rx_data),
    .pipe_rx_datak_o      (pipe_rx_datak),
    .pipe_rx_valid_o      (pipe_rx_valid),
    .pipe_phy_status_o    (pipe_phy_status),
    .pipe_rx_elec_idle_o  (pipe_rx_elec_idle),
    .pipe_rx_status_o     (pipe_rx_status),
    .ser_tx_data_o        (ser_tx_data_o),
    .ser_tx_elec_idle_o   (ser_tx_elec_idle_o),
    .ser_tx_rxdet_i       (ser_tx_rxdet_i),
    .ser_rx_clk_i         (ser_rx_clk_i),
    .ser_rx_data_i        (ser_rx_data_i),
    .ser_rx_elec_idle_i   (ser_rx_elec_idle_i)
  );

endmodule

`default_nettype wire
