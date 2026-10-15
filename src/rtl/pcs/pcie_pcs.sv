// 8b/10b physical coding sublayer: PIPE (16-bit, clk_i) above, the
// 2x10-bit symbol interface below. Interfaces and behavior:
// wiki/src/arch/pcs/HAS.md; structure: wiki/src/rtl/pcs/MAS.md.
//
// The top holds PIPE control (reset and PhyStatus, PowerDown acknowledge,
// receiver detection, RxElecIdle), RxStatus and RxValid, the error counter
// that requests a relock, loopback, and the crossings 4 and 5. The four
// sub-IPs do the rest: pcs_enc8b10b (transmit), pcs_align (comma lock,
// ser_rx_clk_i), pcs_elastic (SKP elastic buffer, crossings 1 to 3) and
// pcs_dec8b10b (polarity and decode, clk_i).
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

  // The RX-domain reset (crossing 2) comes from the elastic buffer's
  // synchronizer (u_elastic.ser_rx_rst_o): asserted with rst_i, released on
  // the second ser_rx_clk_i edge after it falls. Every RX-domain consumer
  // here takes it synchronously.
  logic rx_rst;

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
  // Receive path: aligner (ser_rx_clk_i) -> elastic buffer -> decoder
  // (clk_i) -> RxStatus, one output register.
  // ------------------------------------------------------------------

  logic [19:0] al_sym;
  logic        al_lock;
  logic        al_gen;
  logic        relock_rx;

  pcie_pcs_align u_align (
    .clk_i       (ser_rx_clk_i),
    .rst_i       (rx_rst),
    .data_i      (ser_rx_data_i),
    .elec_idle_i (ser_rx_elec_idle_i),
    .relock_i    (relock_rx),
    .sym_o       (al_sym),
    .lock_o      (al_lock),
    .gen_o       (al_gen)
  );

  logic        eb_valid;
  logic [19:0] eb_sym;
  logic [1:0]  eb_lock;
  logic [1:0]  eb_gen;
  logic        eb_skp_add;
  logic        eb_skp_rem;
  logic        eb_ovf;
  logic        eb_udf;
  logic [5:0]  eb_fill;

  // The aligner sends a word every RX cycle.
  pcie_pcs_elastic u_elastic (
    .clk_i          (clk_i),
    .rst_i          (rst_i),
    .ser_rx_clk_i   (ser_rx_clk_i),
    .ser_rx_valid_i (1'b1),
    .ser_rx_sym_i   (al_sym),
    .ser_rx_lock_i  (al_lock),
    .ser_rx_gen_i   (al_gen),
    .rx_valid_o     (eb_valid),
    .rx_sym_o       (eb_sym),
    .rx_lock_o      (eb_lock),
    .rx_gen_o       (eb_gen),
    .rx_skp_add_o   (eb_skp_add),
    .rx_skp_rem_o   (eb_skp_rem),
    .rx_ovf_o       (eb_ovf),
    .rx_udf_o       (eb_udf),
    .fill_o         (eb_fill),
    .ser_rx_rst_o   (rx_rst)
  );

  // The fill is a debug and DV output of the elastic buffer.
  `COMMON_UNUSED_BITS(unused_eb_fill, eb_fill)

  // Decoder: an underflow word is not decoded. Its RD resets with the PCS
  // clk_i side (pipe_reset_ni included).
  logic        dec_valid;
  logic [15:0] dec_data;
  logic [1:0]  dec_k;
  logic [1:0]  dec_code_err;
  logic [1:0]  dec_disp_err;

  assign dec_valid = eb_valid && !eb_udf;

  pcie_pcs_dec8b10b u_dec (
    .clk_i          (clk_i),
    .rst_i          (pcs_rst),
    .in_valid_i     (dec_valid),
    .in_sym_i       (eb_sym),
    .in_lock_i      (eb_lock),
    .in_gen_i       (eb_gen),
    .polarity_i     (pipe_rx_polarity_i),
    .out_data_o     (dec_data),
    .out_k_o        (dec_k),
    .out_code_err_o (dec_code_err),
    .out_disp_err_o (dec_disp_err)
  );

  // One PIPE word per elastic-buffer word: RxValid when both symbols are
  // locked; EDB, EDB as K symbols on underflow; the RxStatus precedence
  // 4, 5, 6, 7, 1, 2, 0 (RECEIVE.md, Decode and RxStatus).
  logic                     word_valid;
  logic                     word_lock;
  logic [15:0]              word_data;
  logic [1:0]               word_k;
  logic                     word_code_err;
  logic                     word_disp_err;
  pcie_pcs_pkg::t_rx_status word_status;

  assign word_lock     = dec_valid && (eb_lock == 2'b11);
  assign word_code_err = word_lock && (dec_code_err != 2'b00);
  assign word_disp_err = word_lock && (dec_disp_err != 2'b00);
  assign word_data     = eb_udf ? {pcie_pcs_dec8b10b_pkg::K_EDB, pcie_pcs_dec8b10b_pkg::K_EDB}
                                : dec_data;
  assign word_k        = eb_udf ? 2'b11 : dec_k;
  assign word_valid    = eb_valid;

  always_comb begin
    word_status = pcie_pcs_pkg::RX_OK;
    if (!eb_valid) begin
      word_status = pcie_pcs_pkg::RX_OK;
    end else if (word_code_err) begin
      word_status = pcie_pcs_pkg::RX_CODE_ERR;
    end else if (eb_ovf) begin
      word_status = pcie_pcs_pkg::RX_EB_OVERFLOW;
    end else if (eb_udf) begin
      word_status = pcie_pcs_pkg::RX_EB_UNDERFLOW;
    end else if (word_disp_err) begin
      word_status = pcie_pcs_pkg::RX_DISP_ERR;
    end else if (eb_skp_add) begin
      word_status = pcie_pcs_pkg::RX_SKP_ADDED;
    end else if (eb_skp_rem) begin
      word_status = pcie_pcs_pkg::RX_SKP_REMOVED;
    end
  end

  // PIPE receive register. Data is held when no word is sent (loopback
  // then repeats the last word, a valid code). The receive status and the
  // error-counter inputs reset with the PCS clk_i side.
  logic                     rx_valid_q;
  logic [15:0]              rx_data_q;
  logic [1:0]               rx_k_q;
  pcie_pcs_pkg::t_rx_status rx_status_q;
  logic                     rx_errored_q;
  logic                     rx_gen_q;
  logic                     word_errored;

  assign word_errored = word_code_err || word_disp_err;

  `COMMON_DFF_RST(rx_valid_q, word_lock, clk_i, pcs_rst)
  `COMMON_DFF_RST_EN(rx_data_q, word_data, clk_i, word_valid, pcs_rst, 16'd0)
  `COMMON_DFF_RST_EN(rx_k_q, word_k, clk_i, word_valid, pcs_rst, 2'd0)
  `COMMON_DFF_RST_VAL(rx_status_q, word_status, clk_i, pcs_rst, pcie_pcs_pkg::RX_OK)
  `COMMON_DFF_RST(rx_errored_q, word_errored, clk_i, pcs_rst)
  `COMMON_DFF_RST_EN(rx_gen_q, eb_gen[1], clk_i, word_lock, pcs_rst, 1'b0)

  // ------------------------------------------------------------------
  // Error counter and relock request (crossing 4). It watches the words
  // that leave with RxValid: an errored word adds 1, ERR_DECAY_WORDS clean
  // words in a row take 1 away; at ERR_LIMIT one request goes out and the
  // counter waits for a word of the other lock generation. A change of the
  // lock generation on PIPE clears the counter.
  // ------------------------------------------------------------------

  logic [pcie_pcs_pkg::ERR_CNT_W-1:0]   err_cnt_q;
  logic [pcie_pcs_pkg::ERR_CNT_W-1:0]   err_cnt_d;
  logic [pcie_pcs_pkg::CLEAN_CNT_W-1:0] clean_cnt_q;
  logic [pcie_pcs_pkg::CLEAN_CNT_W-1:0] clean_cnt_d;
  logic                                 last_gen_q;
  logic                                 last_gen_d;
  logic                                 gen_known_q;
  logic                                 gen_known_d;
  logic                                 wait_q;
  logic                                 wait_d;
  logic                                 wait_gen_q;
  logic                                 wait_gen_d;
  logic                                 relock_req;
  logic [pcie_pcs_pkg::ERR_CNT_W-1:0]   err_cnt_new;
  logic [pcie_pcs_pkg::CLEAN_CNT_W-1:0] clean_cnt_new;

  always_comb begin
    err_cnt_d   = err_cnt_q;
    clean_cnt_d = clean_cnt_q;
    last_gen_d  = last_gen_q;
    gen_known_d = gen_known_q;
    wait_d      = wait_q;
    wait_gen_d  = wait_gen_q;
    relock_req  = 1'b0;
    err_cnt_new   = err_cnt_q;
    clean_cnt_new = clean_cnt_q;
    if (rx_valid_q) begin
      // A new locked run starts the count again.
      if (!gen_known_q || (rx_gen_q != last_gen_q)) begin
        err_cnt_new   = '0;
        clean_cnt_new = '0;
      end
      last_gen_d  = rx_gen_q;
      gen_known_d = 1'b1;
      if (wait_q && (rx_gen_q != wait_gen_q)) begin
        wait_d = 1'b0;                      // the aligner has relocked
      end
      err_cnt_d   = err_cnt_new;
      clean_cnt_d = clean_cnt_new;
      if (wait_d) begin
        // No counting while a request is outstanding.
      end else if (rx_errored_q) begin
        clean_cnt_d = '0;
        if (err_cnt_new == pcie_pcs_pkg::ERR_CNT_W'(pcie_pcs_pkg::ERR_LIMIT - 1)) begin
          err_cnt_d  = '0;
          relock_req = 1'b1;
          wait_d     = 1'b1;
          wait_gen_d = rx_gen_q;
        end else begin
          err_cnt_d = err_cnt_new + 1'b1;
        end
      end else if (clean_cnt_new ==
                   pcie_pcs_pkg::CLEAN_CNT_W'(pcie_pcs_pkg::ERR_DECAY_WORDS - 1)) begin
        clean_cnt_d = '0;
        err_cnt_d   = (err_cnt_new == '0) ? '0 : err_cnt_new - 1'b1;
      end else begin
        clean_cnt_d = clean_cnt_new + 1'b1;
      end
    end
  end

  `COMMON_DFF_RST(err_cnt_q, err_cnt_d, clk_i, pcs_rst)
  `COMMON_DFF_RST(clean_cnt_q, clean_cnt_d, clk_i, pcs_rst)
  `COMMON_DFF_RST(last_gen_q, last_gen_d, clk_i, pcs_rst)
  `COMMON_DFF_RST(gen_known_q, gen_known_d, clk_i, pcs_rst)
  `COMMON_DFF_RST(wait_q, wait_d, clk_i, pcs_rst)
  `COMMON_DFF_RST(wait_gen_q, wait_gen_d, clk_i, pcs_rst)

  // Crossing 4: both sides reset with rst_i and the RX reset, never
  // pipe_reset_ni alone, so the two sides reset together.
  `COMMON_PULSE_SYNC(u_relock_sync, relock_rx, relock_req, clk_i, rst_i, ser_rx_clk_i, rx_rst)

  // ------------------------------------------------------------------
  // Transmit path: P0 and not TxElecIdle sends a word through the encoder
  // (pcs_enc8b10b), which holds the whole two-cycle TX latency; its outputs
  // drive ser_tx_* with no further register.
  // ------------------------------------------------------------------

  logic        tx_word;
  logic        loopback;
  logic [15:0] enc_in_data;
  logic [1:0]  enc_in_datak;
  logic        enc_in_compliance;
  logic        enc_valid;
  logic [19:0] enc_data;

  assign tx_word  = (power == pcie_pcs_pkg::P0) && !pipe_tx_elec_idle_i;
  // Loopback (P0, TxElecIdle 0, TxDetectRx 1): re-encode the received
  // words as they leave on PIPE; a code error is already EDB as a K symbol.
  assign loopback = tx_word && pipe_tx_detect_rx_i;

  assign enc_in_data       = loopback ? rx_data_q : pipe_tx_data_i;
  assign enc_in_datak      = loopback ? rx_k_q    : pipe_tx_datak_i;
  assign enc_in_compliance = !loopback && pipe_tx_compliance_i;

  pcie_pcs_enc8b10b u_enc (
    .clk_i        (clk_i),
    .rst_i        (pcs_rst),
    .valid_i      (tx_word),
    .data_i       (enc_in_data),
    .datak_i      (enc_in_datak),
    .compliance_i (enc_in_compliance),
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
                               : rx_status_q;

  assign pipe_phy_status_o   = pcs_rst || !ready || ack_pulse || det_pulse;
  assign pipe_rx_status_o    = pcs_rst ? 3'd0 : rx_status;
  assign pipe_rx_valid_o     = !pcs_rst && rx_valid_q;
  assign pipe_rx_data_o      = rx_data_q;
  assign pipe_rx_datak_o     = rx_k_q;
  assign pipe_rx_elec_idle_o = pcs_rst || !rx_active_sync;
  assign ser_tx_elec_idle_o  = pcs_rst || !enc_valid;
  assign ser_tx_data_o       = enc_data;

  // ------------------------------------------------------------------
  // Assertions
  // ------------------------------------------------------------------

  // RxStatus 3 only with the receiver-detection PhyStatus pulse.
  `COMMON_ASSERT_NEVER(a_detected_only_with_pulse, clk_i, pcs_rst,
                       pipe_rx_status_o == 3'd3 && !(det_pulse && pipe_phy_status_o))
  // Once ready, every PhyStatus pulse lasts one cycle.
  `COMMON_ASSERT(a_phy_status_one_cycle, clk_i, pcs_rst,
                 (ready && pipe_phy_status_o) |=> !pipe_phy_status_o)
  // At most ERR_LIMIT - 1 errors are ever counted.
  `COMMON_ASSERT_NEVER(a_err_cnt_range, clk_i, pcs_rst,
                       err_cnt_q > pcie_pcs_pkg::ERR_CNT_W'(pcie_pcs_pkg::ERR_LIMIT - 1))
  // An underflow word never carries RxValid.
  `COMMON_ASSERT_NEVER(a_udf_not_valid, clk_i, pcs_rst, eb_valid && eb_udf && word_lock)
  // The power-acknowledge countdown never exceeds its start value.
  `COMMON_ASSERT_NEVER(a_ack_cnt_range, clk_i, pcs_rst,
                       ack_cnt_q > pcie_pcs_pkg::ACK_CNT_W'(pcie_pcs_pkg::POWER_ACK_CYCLES))

endmodule

`default_nettype wire
