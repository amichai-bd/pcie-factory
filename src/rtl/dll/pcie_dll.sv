// Data link layer over the phy packet stream.
// Interfaces and behavior: wiki/src/arch/dll/HAS.md; structure:
// wiki/src/rtl/dll/MAS.md.
//
// Wave-1 shell: the ports of the HAS with every output tied to its reset
// value. The data link blocks arrive in wave 2.
`default_nettype none

module pcie_dll #(
  parameter int unsigned FC_PH   = 8,   // posted header credits advertised; 0 infinite, else 1..127
  parameter int unsigned FC_PD   = 32,  // posted data credits; 0 infinite, else 8..2047
  parameter int unsigned FC_NPH  = 8,   // non-posted header credits; 0 infinite, else 1..127
  parameter int unsigned FC_NPD  = 8,   // non-posted data credits; 0 infinite, else 1..2047
  parameter int unsigned FC_CPLH = 8,   // completion header credits; 0 infinite, else 1..127
  parameter int unsigned FC_CPLD = 32   // completion data credits; 0 infinite, else 8..2047
) (
  // Clock and reset
  input  logic        clk_i,            // core clock, 125 MHz
  input  logic        rst_i,            // synchronous to clk_i, active high // cdc-domain: clk_i

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

  // Status
  output logic        dl_up_o,
  output logic [1:0]  dl_state_o,       // 0 DL_Inactive, 1 FC_INIT1, 2 FC_INIT2, 3 DL_Active
  output logic [5:0]  err_o,            // one-cycle error pulses, bit order in the HAS

  // Lower packet stream, transmit (to phy)
  output logic        pkt_tx_valid_o,
  input  logic        pkt_tx_ready_i,   // cdc-domain: clk_i
  output logic [15:0] pkt_tx_data_o,    // [7:0] is the earlier byte
  output logic        pkt_tx_sop_o,
  output logic        pkt_tx_eop_o,
  output logic        pkt_tx_dllp_o,    // tag: 0 TLP, 1 DLLP; held for the packet

  // Lower packet stream, receive (from phy)
  input  logic        pkt_rx_valid_i,   // cdc-domain: clk_i
  input  logic [15:0] pkt_rx_data_i,    // [7:0] is the earlier byte // cdc-domain: clk_i
  input  logic        pkt_rx_sop_i,     // cdc-domain: clk_i
  input  logic        pkt_rx_eop_i,     // cdc-domain: clk_i
  input  logic        pkt_rx_dllp_i,    // tag: 0 TLP, 1 DLLP // cdc-domain: clk_i
  input  logic        pkt_rx_err_i,     // with eop: bad or cut-short packet // cdc-domain: clk_i
  input  logic        pkt_rx_nullify_i, // with eop: ended by EDB // cdc-domain: clk_i

  // Physical-layer status and control
  input  logic        link_up_i,        // phy link_up_o // cdc-domain: clk_i
  input  logic [5:0]  ltssm_state_i,    // phy ltssm_state_o // cdc-domain: clk_i
  output logic        retrain_req_o     // one-cycle pulse on REPLAY_NUM rollover
);

  // Inputs the shell does not use yet; the wave-2 blocks consume them.
  logic unused_inputs;
  assign unused_inputs = ^{clk_i, rst_i, tlp_tx_valid_i, tlp_tx_data_i, tlp_tx_sop_i,
                           tlp_tx_eop_i, tlp_rx_ready_i, pkt_tx_ready_i, pkt_rx_valid_i,
                           pkt_rx_data_i, pkt_rx_sop_i, pkt_rx_eop_i, pkt_rx_dllp_i,
                           pkt_rx_err_i, pkt_rx_nullify_i, link_up_i, ltssm_state_i,
                           FC_PH[0], FC_PD[0], FC_NPH[0], FC_NPD[0], FC_CPLH[0],
                           FC_CPLD[0]};

  // Reset values from the HAS: DL_Inactive, no traffic, no credits.
  assign tlp_tx_ready_o  = 1'b0;
  assign tlp_rx_valid_o  = 1'b0;
  assign tlp_rx_data_o   = 32'd0;
  assign tlp_rx_sop_o    = 1'b0;
  assign tlp_rx_eop_o    = 1'b0;
  assign fc_ph_avail_o   = 8'd0;
  assign fc_pd_avail_o   = 12'd0;
  assign fc_nph_avail_o  = 8'd0;
  assign fc_npd_avail_o  = 12'd0;
  assign fc_cplh_avail_o = 8'd0;
  assign fc_cpld_avail_o = 12'd0;
  assign dl_up_o         = 1'b0;
  assign dl_state_o      = 2'd0;
  assign err_o           = 6'd0;
  assign pkt_tx_valid_o  = 1'b0;
  assign pkt_tx_data_o   = 16'd0;
  assign pkt_tx_sop_o    = 1'b0;
  assign pkt_tx_eop_o    = 1'b0;
  assign pkt_tx_dllp_o   = 1'b0;
  assign retrain_req_o   = 1'b0;

endmodule

`default_nettype wire
