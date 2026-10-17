// Gen1 x1 link stack: pcie_dll over pcie_phy.
// Interfaces and behavior: wiki/src/arch/link/HAS.md; structure:
// wiki/src/rtl/link/MAS.md.
//
// Wave-1 shell: the ports of the HAS with every output tied to its reset
// value. The stack integration arrives in wave 3.
`default_nettype none

module pcie_link #(
  parameter int unsigned FC_PH           = 8,  // posted header credits advertised; 0 infinite, else 1..127
  parameter int unsigned FC_PD           = 32, // posted data credits; 0 infinite, else 8..2047
  parameter int unsigned FC_NPH          = 8,  // non-posted header credits; 0 infinite, else 1..127
  parameter int unsigned FC_NPD          = 8,  // non-posted data credits; 0 infinite, else 1..2047
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

  // Inputs the shell does not use yet; the wave-3 integration consumes them.
  logic unused_inputs;
  assign unused_inputs = ^{clk_i, rst_i, port_role_i, tlp_tx_valid_i, tlp_tx_data_i,
                           tlp_tx_sop_i, tlp_tx_eop_i, tlp_rx_ready_i, retrain_req_i,
                           hot_reset_req_i, disable_req_i, loopback_req_i, ser_tx_rxdet_i,
                           FC_PH[0], FC_PD[0], FC_NPH[0], FC_NPD[0], FC_CPLH[0], FC_CPLD[0],
                           TIMEOUT_DIV[0], ERR_TIMEOUT_DIV[0]};
  logic unused_rx_inputs;
  assign unused_rx_inputs = ^{ser_rx_clk_i, ser_rx_data_i, ser_rx_elec_idle_i};

  // Reset values from the HAS: DL_Inactive, Detect.Quiet, electrical idle.
  assign tlp_tx_ready_o     = 1'b0;
  assign tlp_rx_valid_o     = 1'b0;
  assign tlp_rx_data_o      = 32'd0;
  assign tlp_rx_sop_o       = 1'b0;
  assign tlp_rx_eop_o       = 1'b0;
  assign fc_ph_avail_o      = 8'd0;
  assign fc_pd_avail_o      = 12'd0;
  assign fc_nph_avail_o     = 8'd0;
  assign fc_npd_avail_o     = 12'd0;
  assign fc_cplh_avail_o    = 8'd0;
  assign fc_cpld_avail_o    = 12'd0;
  assign dl_up_o            = 1'b0;
  assign dl_state_o         = 2'd0;
  assign dl_err_o           = 7'd0;
  assign link_up_o          = 1'b0;
  assign ltssm_state_o      = 6'd0;
  assign rcv_err_o          = 1'b0;
  assign ser_tx_data_o      = 20'd0;
  assign ser_tx_elec_idle_o = 1'b1;

endmodule

`default_nettype wire
