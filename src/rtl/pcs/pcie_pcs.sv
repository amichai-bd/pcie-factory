// 8b/10b physical coding sublayer: PIPE (16-bit, clk_i) above, the
// 2x10-bit symbol interface below. Interfaces and behavior:
// wiki/src/arch/pcs/HAS.md; structure: wiki/src/rtl/pcs/MAS.md.
//
// This revision holds PIPE control (reset and PhyStatus, PowerDown
// acknowledge, receiver detection, RxElecIdle), the RX-domain reset and the
// transmit path through pcs_enc8b10b. The receive sub-IPs (decoder,
// aligner, elastic buffer) join as they reach main; until then the receive
// data outputs are tied.
`default_nettype none

`include "common_macros.svh"

module pcie_pcs (
  // Clock and reset
  input  logic        clk_i,                // core clock and PCLK, 125 MHz
  input  logic        rst_i,                // cdc-domain: clk_i

  // PIPE, MAC to PCS (clk_i)
  input  logic        pipe_reset_ni,        // cdc-domain: clk_i
  input  logic [15:0] pipe_tx_data_i,       // cdc-domain: clk_i
  input  logic [1:0]  pipe_tx_datak_i,      // cdc-domain: clk_i
  input  logic        pipe_tx_detect_rx_i,  // cdc-domain: clk_i
  input  logic        pipe_tx_elec_idle_i,  // cdc-domain: clk_i
  input  logic        pipe_tx_compliance_i, // cdc-domain: clk_i
  input  logic        pipe_rx_polarity_i,   // cdc-domain: clk_i
  input  logic [1:0]  pipe_power_down_i,    // cdc-domain: clk_i

  // PIPE, PCS to MAC (clk_i)
  output logic [15:0] pipe_rx_data_o,       // [7:0] is the earlier byte
  output logic [1:0]  pipe_rx_datak_o,
  output logic        pipe_rx_valid_o,
  output logic        pipe_phy_status_o,
  output logic        pipe_rx_elec_idle_o,
  output logic [2:0]  pipe_rx_status_o,

  // Lower 2x10-bit symbol interface, transmit (clk_i)
  output logic [19:0] ser_tx_data_o,        // [9:0] first symbol, bit 0 first on the line
  output logic        ser_tx_elec_idle_o,
  input  logic        ser_tx_rxdet_i,       // cdc-domain: clk_i

  // Lower 2x10-bit symbol interface, receive (ser_rx_clk_i)
  input  logic        ser_rx_clk_i,         // recovered clock, 125 MHz nominal
  input  logic [19:0] ser_rx_data_i,        // cdc-domain: ser_rx_clk_i
  input  logic        ser_rx_elec_idle_i    // cdc-domain: ser_rx_clk_i
);

  // ------------------------------------------------------------------
  // Resets
  // ------------------------------------------------------------------

  // The clk_i side of the PCS is in reset in every cycle rst_i is 1 or
  // pipe_reset_ni is 0 (PIPE_CONTROL.md, Reset and PhyStatus).
  logic pcs_rst;
  assign pcs_rst = rst_i || !pipe_reset_ni;

  // Crossing 2: the RX-domain reset, asserted with rst_i (no clock edge),
  // released on the second ser_rx_clk_i edge after it falls.
  logic rx_rst;
  `COMMON_RESET_SYNC(u_rx_rst, rx_rst, rst_i, ser_rx_clk_i)

  // ------------------------------------------------------------------
  // PhyStatus after reset: high in reset and the 16 cycles after it
  // ------------------------------------------------------------------

  logic [pcie_pcs_pkg::READY_CNT_W-1:0] ready_cnt_q;
  logic [pcie_pcs_pkg::READY_CNT_W-1:0] ready_cnt_d;
  logic                                 ready;

  assign ready       = (ready_cnt_q == '0);
  assign ready_cnt_d = ready ? ready_cnt_q : ready_cnt_q - 1'b1;

  `COMMON_DFF_RST_VAL(ready_cnt_q, ready_cnt_d, clk_i, pcs_rst,
                      pcie_pcs_pkg::READY_CNT_W'(pcie_pcs_pkg::PHY_STATUS_RESET_CYCLES))

  // ------------------------------------------------------------------
  // PowerDown: the state is the value seen; every change seen once ready
  // gives one PhyStatus pulse POWER_ACK_CYCLES cycles later.
  // ------------------------------------------------------------------

  pcie_pcs_pkg::t_power_down power;
  pcie_pcs_pkg::t_power_down power_q;
  logic                      power_change;

  assign power        = pcie_pcs_pkg::t_power_down'(pipe_power_down_i);
  assign power_change = ready && (power != power_q);

  `COMMON_DFF_RST_VAL(power_q, power, clk_i, pcs_rst, pcie_pcs_pkg::P1)

  // ack_cnt_q counts down from POWER_ACK_CYCLES; the pulse is the cycle it
  // holds 1, the fourth after the cycle the change was seen.
  logic [pcie_pcs_pkg::ACK_CNT_W-1:0] ack_cnt_q;
  logic [pcie_pcs_pkg::ACK_CNT_W-1:0] ack_cnt_d;
  logic                               ack_pulse;

  always_comb begin
    ack_cnt_d = ack_cnt_q;
    if (power_change) begin
      ack_cnt_d = pcie_pcs_pkg::ACK_CNT_W'(pcie_pcs_pkg::POWER_ACK_CYCLES);
    end else if (ack_cnt_q != '0) begin
      ack_cnt_d = ack_cnt_q - 1'b1;
    end
  end

  `COMMON_DFF_RST(ack_cnt_q, ack_cnt_d, clk_i, pcs_rst)

  assign ack_pulse = (ack_cnt_q == pcie_pcs_pkg::ACK_CNT_W'(1));

  // ------------------------------------------------------------------
  // Receiver detection (P1, TxElecIdle 1, TxDetectRx 1)
  // ------------------------------------------------------------------

  // det_cnt_q holds the detection cycles seen so far; in detection cycle
  // RX_DETECT_CYCLES it holds RX_DETECT_CYCLES - 1, which is the pulse.
  localparam logic [pcie_pcs_pkg::DETECT_CNT_W-1:0] DET_SAMPLE =
      pcie_pcs_pkg::DETECT_CNT_W'(pcie_pcs_pkg::RX_DETECT_CYCLES - 2);
  localparam logic [pcie_pcs_pkg::DETECT_CNT_W-1:0] DET_LAST =
      pcie_pcs_pkg::DETECT_CNT_W'(pcie_pcs_pkg::RX_DETECT_CYCLES - 1);

  logic [pcie_pcs_pkg::DETECT_CNT_W-1:0] det_cnt_q;
  logic [pcie_pcs_pkg::DETECT_CNT_W-1:0] det_cnt_d;
  logic                                  det_done_q;
  logic                                  det_done_d;
  logic                                  det_counting;
  logic                                  det_pulse;
  logic                                  det_sample_en;
  logic                                  rxdet_q;

  assign det_counting = ready && (power == pcie_pcs_pkg::P1) && pipe_tx_elec_idle_i
                        && pipe_tx_detect_rx_i && !det_done_q;
  assign det_pulse    = (det_cnt_q == DET_LAST) && !det_done_q;

  always_comb begin
    det_cnt_d  = det_cnt_q;
    det_done_d = det_done_q;
    if (!pipe_tx_detect_rx_i) begin
      // A new detection starts only after TxDetectRx was seen 0.
      det_cnt_d  = '0;
      det_done_d = 1'b0;
    end else if (det_pulse) begin
      det_cnt_d  = '0;
      det_done_d = 1'b1;
    end else if (det_counting) begin
      det_cnt_d = det_cnt_q + 1'b1;
    end
  end

  `COMMON_DFF_RST(det_cnt_q, det_cnt_d, clk_i, pcs_rst)
  `COMMON_DFF_RST(det_done_q, det_done_d, clk_i, pcs_rst)

  // ser_tx_rxdet_i is sampled in detection cycle RX_DETECT_CYCLES - 1.
  assign det_sample_en = det_counting && (det_cnt_q == DET_SAMPLE);

  `COMMON_DFF_EN(rxdet_q, ser_tx_rxdet_i, clk_i, det_sample_en)

  // ------------------------------------------------------------------
  // Transmit path: P0 and not TxElecIdle sends a word through the encoder
  // (pcs_enc8b10b), which holds the whole two-cycle TX latency; its outputs
  // drive ser_tx_* with no further register. Loopback joins with the
  // receive path.
  // ------------------------------------------------------------------

  logic        tx_word;
  logic        enc_valid;
  logic [19:0] enc_data;

  assign tx_word = (power == pcie_pcs_pkg::P0) && !pipe_tx_elec_idle_i;

  pcie_pcs_enc8b10b u_enc (
    .clk_i        (clk_i),
    .rst_i        (pcs_rst),
    .valid_i      (tx_word),
    .data_i       (pipe_tx_data_i),
    .datak_i      (pipe_tx_datak_i),
    .compliance_i (pipe_tx_compliance_i),
    .valid_o      (enc_valid),
    .data_o       (enc_data)
  );

  // ------------------------------------------------------------------
  // Crossing 5: receiver active (not electrical idle), registered in the
  // RX domain and synchronized into clk_i. Reset value 0 is "idle".
  // ------------------------------------------------------------------

  logic rx_active;
  logic rx_active_q;
  logic rx_active_sync;

  assign rx_active = !ser_rx_elec_idle_i;

  `COMMON_DFF_RST(rx_active_q, rx_active, ser_rx_clk_i, rx_rst)
  `COMMON_METAFLOP(u_rx_active_sync, rx_active_sync, rx_active_q, clk_i, rst_i)

  // ------------------------------------------------------------------
  // Outputs; reset values from the HAS are forced while pcs_rst is 1.
  // ------------------------------------------------------------------

  pcie_pcs_pkg::t_rx_status rx_status;

  assign rx_status = det_pulse ? (rxdet_q ? pcie_pcs_pkg::RX_DETECTED : pcie_pcs_pkg::RX_OK)
                               : pcie_pcs_pkg::RX_OK;

  assign pipe_phy_status_o   = pcs_rst || !ready || ack_pulse || det_pulse;
  assign pipe_rx_status_o    = pcs_rst ? 3'd0 : rx_status;
  assign pipe_rx_valid_o     = 1'b0;
  assign pipe_rx_data_o      = 16'd0;
  assign pipe_rx_datak_o     = 2'd0;
  assign pipe_rx_elec_idle_o = pcs_rst || !rx_active_sync;
  assign ser_tx_elec_idle_o  = pcs_rst || !enc_valid;
  assign ser_tx_data_o       = enc_data;

  // Inputs the data path consumes once its sub-IPs are integrated.
  `COMMON_UNUSED_BITS(unused_data_inputs, {pipe_rx_polarity_i, ser_rx_data_i})

  // ------------------------------------------------------------------
  // Assertions
  // ------------------------------------------------------------------

  // RxStatus 3 only with the receiver-detection PhyStatus pulse.
  `COMMON_ASSERT_NEVER(a_detected_only_with_pulse, clk_i, pcs_rst,
                       pipe_rx_status_o == 3'd3 && !(det_pulse && pipe_phy_status_o))
  // Once ready, every PhyStatus pulse lasts one cycle.
  `COMMON_ASSERT(a_phy_status_one_cycle, clk_i, pcs_rst,
                 (ready && pipe_phy_status_o) |=> !pipe_phy_status_o)
  // The power-acknowledge countdown never exceeds its start value.
  `COMMON_ASSERT_NEVER(a_ack_cnt_range, clk_i, pcs_rst,
                       ack_cnt_q > pcie_pcs_pkg::ACK_CNT_W'(pcie_pcs_pkg::POWER_ACK_CYCLES))

endmodule

`default_nettype wire
