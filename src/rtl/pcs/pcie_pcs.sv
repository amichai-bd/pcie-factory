// 8b/10b physical coding sublayer: PIPE (16-bit, clk_i) above, the
// 2x10-bit symbol interface below. Interfaces and behavior:
// wiki/src/arch/pcs/HAS.md; structure: wiki/src/rtl/pcs/MAS.md.
//
// The top holds PIPE control (reset and PhyStatus, PowerDown acknowledge,
// receiver detection, RxElecIdle), RxStatus and RxValid, the error counter
// that requests a relock, loopback, and the crossings 4 and 5. Four
// blocks of the IP do the rest: pcie_pcs_enc8b10b (transmit),
// pcie_pcs_align (comma lock, ser_rx_clk_i), pcie_pcs_elastic (SKP elastic
// buffer, crossings 1 to 3) and pcie_pcs_dec8b10b (polarity and decode,
// clk_i).
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

  common_pkg::t_power_down power;
  common_pkg::t_power_down power_q;
  logic                      power_change;

  assign power        = common_pkg::t_power_down'(pipe_power_down_i);
  assign power_change = ready && (power != power_q);

  `COMMON_DFF_RST_VAL(power_q, power, clk_i, pcs_rst, common_pkg::PD_P1)

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

  // det_q.cnt holds the detection cycles seen so far; in detection cycle
  // RX_DETECT_CYCLES it holds RX_DETECT_CYCLES - 1, which is the pulse.
  localparam logic [pcie_pcs_pkg::DETECT_CNT_W-1:0] DET_SAMPLE =
      pcie_pcs_pkg::DETECT_CNT_W'(pcie_pcs_pkg::RX_DETECT_CYCLES - 2);
  localparam logic [pcie_pcs_pkg::DETECT_CNT_W-1:0] DET_LAST =
      pcie_pcs_pkg::DETECT_CNT_W'(pcie_pcs_pkg::RX_DETECT_CYCLES - 1);

  pcie_pcs_pkg::t_detect det_q;
  pcie_pcs_pkg::t_detect det_d;
  logic                  det_counting;
  logic                  det_pulse;
  logic                  det_sample_en;
  logic                  rxdet_q;

  assign det_counting = ready && (power == common_pkg::PD_P1) && pipe_tx_elec_idle_i
                        && pipe_tx_detect_rx_i && !det_q.done;
  assign det_pulse    = (det_q.cnt == DET_LAST) && !det_q.done;

  always_comb begin
    det_d = det_q;
    if (!pipe_tx_detect_rx_i) begin
      // A new detection starts only after TxDetectRx was seen 0.
      det_d = '{cnt: '0, done: 1'b0};
    end else if (det_pulse) begin
      det_d = '{cnt: '0, done: 1'b1};
    end else if (det_counting) begin
      det_d.cnt = det_q.cnt + 1'b1;
    end
  end

  `COMMON_DFF_RST(det_q, det_d, clk_i, pcs_rst)

  // ser_tx_rxdet_i is sampled in detection cycle RX_DETECT_CYCLES - 1.
  assign det_sample_en = det_counting && (det_q.cnt == DET_SAMPLE);

  `COMMON_DFF_EN(rxdet_q, ser_tx_rxdet_i, clk_i, det_sample_en)

  // ------------------------------------------------------------------
  // Receive path: aligner (ser_rx_clk_i) -> elastic buffer -> decoder
  // (clk_i) -> RxStatus, one output register.
  // ------------------------------------------------------------------

  pcie_pcs_pkg::t_rx_pair al_rx;
  logic                   relock_rx;

  pcie_pcs_align u_align (
    .clk_i       (ser_rx_clk_i),
    .rst_i       (rx_rst),
    .data_i      (ser_rx_data_i),
    .elec_idle_i (ser_rx_elec_idle_i),
    .relock_i    (relock_rx),
    .rx_o        (al_rx)
  );

  logic                                   eb_valid;
  pcie_pcs_pkg::t_rx_pair                 eb_rx;
  pcie_pcs_pkg::t_eb_status               eb_status;
  logic [pcie_pcs_pkg::EB_FILL_W-1:0]     eb_fill;

  // The aligner sends a word every RX cycle.
  pcie_pcs_elastic u_elastic (
    .clk_i          (clk_i),
    .rst_i          (rst_i),
    .ser_rx_clk_i   (ser_rx_clk_i),
    .ser_rx_valid_i (1'b1),
    .ser_rx_i       (al_rx),
    .rx_valid_o     (eb_valid),
    .rx_o           (eb_rx),
    .rx_status_o    (eb_status),
    .fill_o         (eb_fill),
    .ser_rx_rst_o   (rx_rst)
  );

  // The fill is a debug and DV output of the elastic buffer.
  `COMMON_UNUSED_BITS(unused_eb_fill, eb_fill)

  // Decoder: an underflow word is not decoded. Its RD resets with the PCS
  // clk_i side (pipe_reset_ni included).
  logic                    dec_valid;
  pcie_pcs_pkg::t_dec_pair dec;

  assign dec_valid = eb_valid && !eb_status.udf;

  pcie_pcs_dec8b10b u_dec (
    .clk_i      (clk_i),
    .rst_i      (pcs_rst),
    .in_valid_i (dec_valid),
    .in_i       (eb_rx),
    .polarity_i (pipe_rx_polarity_i),
    .out_o      (dec)
  );

  // One PIPE word per elastic-buffer word: RxValid when both symbols are
  // locked; the RxStatus precedence 4, 5, 6, 7, 1, 2, 0 (RECEIVE.md, Decode
  // and RxStatus). An underflow word holds two all-zero symbols (elastic
  // buffer MAS), a code error in both lanes, so the decoder already gives
  // EDB, EDB as K symbols (a_udf_edb).
  logic                     word_lock;
  logic                     word_code_err;
  logic                     word_disp_err;
  common_pkg::t_rx_status word_status;

  assign word_lock     = dec_valid && eb_rx.sym0.lock && eb_rx.sym1.lock;
  assign word_code_err = word_lock && (dec.sym0.code_err || dec.sym1.code_err);
  assign word_disp_err = word_lock && (dec.sym0.disp_err || dec.sym1.disp_err);

  always_comb begin
    word_status = common_pkg::RX_OK;
    if (!eb_valid) begin
      word_status = common_pkg::RX_OK;
    end else if (word_code_err) begin
      word_status = common_pkg::RX_CODE_ERR;
    end else if (eb_status.ovf) begin
      word_status = common_pkg::RX_EB_OVERFLOW;
    end else if (eb_status.udf) begin
      word_status = common_pkg::RX_EB_UNDERFLOW;
    end else if (word_disp_err) begin
      word_status = common_pkg::RX_DISP_ERR;
    end else if (eb_status.skp_add) begin
      word_status = common_pkg::RX_SKP_ADDED;
    end else if (eb_status.skp_rem) begin
      word_status = common_pkg::RX_SKP_REMOVED;
    end
  end

  // PIPE receive register. Data is held when no word is sent (loopback
  // then repeats the last word, a valid code). The receive status and the
  // error-counter inputs reset with the PCS clk_i side.
  pcie_pcs_pkg::t_rx_word rx_q;
  pcie_pcs_pkg::t_rx_word rx_d;

  always_comb begin
    rx_d         = rx_q;
    rx_d.valid   = word_lock;
    rx_d.status  = word_status;
    rx_d.errored = word_code_err || word_disp_err;
    if (eb_valid) begin
      rx_d.sym0 = dec.sym0.sym;
      rx_d.sym1 = dec.sym1.sym;
    end
    if (word_lock) begin
      rx_d.gen = eb_rx.sym1.gen;
    end
  end

  `COMMON_DFF_RST(rx_q, rx_d, clk_i, pcs_rst)

  // ------------------------------------------------------------------
  // Error counter and relock request (crossing 4). It watches the words
  // that leave with RxValid: an errored word adds 1, ERR_DECAY_WORDS clean
  // words in a row take 1 away; at ERR_LIMIT one request goes out and the
  // counter waits for a word of the other lock generation. A change of the
  // lock generation on PIPE clears the counter.
  // ------------------------------------------------------------------

  pcie_pcs_pkg::t_relock                rl_q;
  pcie_pcs_pkg::t_relock                rl_d;
  logic                                 relock_req;
  logic [pcie_pcs_pkg::ERR_CNT_W-1:0]   err_cnt_new;
  logic [pcie_pcs_pkg::CLEAN_CNT_W-1:0] clean_cnt_new;

  always_comb begin
    rl_d          = rl_q;
    relock_req    = 1'b0;
    err_cnt_new   = rl_q.err_cnt;
    clean_cnt_new = rl_q.clean_cnt;
    if (rx_q.valid) begin
      // A new locked run starts the count again.
      if (!rl_q.gen_known || (rx_q.gen != rl_q.last_gen)) begin
        err_cnt_new   = '0;
        clean_cnt_new = '0;
      end
      rl_d.last_gen  = rx_q.gen;
      rl_d.gen_known = 1'b1;
      if (rl_q.wait_relock && (rx_q.gen != rl_q.wait_gen)) begin
        rl_d.wait_relock = 1'b0;            // the aligner has relocked
      end
      rl_d.err_cnt   = err_cnt_new;
      rl_d.clean_cnt = clean_cnt_new;
      if (rl_d.wait_relock) begin
        // No counting while a request is outstanding.
      end else if (rx_q.errored) begin
        rl_d.clean_cnt = '0;
        if (err_cnt_new == pcie_pcs_pkg::ERR_CNT_W'(pcie_pcs_pkg::ERR_LIMIT - 1)) begin
          rl_d.err_cnt     = '0;
          relock_req       = 1'b1;
          rl_d.wait_relock = 1'b1;
          rl_d.wait_gen    = rx_q.gen;
        end else begin
          rl_d.err_cnt = err_cnt_new + 1'b1;
        end
      end else if (clean_cnt_new ==
                   pcie_pcs_pkg::CLEAN_CNT_W'(pcie_pcs_pkg::ERR_DECAY_WORDS - 1)) begin
        rl_d.clean_cnt = '0;
        rl_d.err_cnt   = (err_cnt_new == '0) ? '0 : err_cnt_new - 1'b1;
      end else begin
        rl_d.clean_cnt = clean_cnt_new + 1'b1;
      end
    end
  end

  `COMMON_DFF_RST(rl_q, rl_d, clk_i, pcs_rst)

  // Crossing 4: both sides reset with rst_i and the RX reset, never
  // pipe_reset_ni alone, so the two sides reset together.
  `COMMON_PULSE_SYNC(u_relock_sync, relock_rx, relock_req, clk_i, rst_i, ser_rx_clk_i, rx_rst)

  // ------------------------------------------------------------------
  // Transmit path: P0 and not TxElecIdle sends a word through the encoder
  // (pcie_pcs_enc8b10b), which holds the whole two-cycle TX latency; its outputs
  // drive ser_tx_* with no further register.
  // ------------------------------------------------------------------

  logic                   tx_word;
  logic                   loopback;
  pcie_pcs_pkg::t_tx_word pipe_tx;
  pcie_pcs_pkg::t_tx_word enc_in;
  logic                   enc_valid;
  pcie_pcs_pkg::t_word    enc_data;

  assign tx_word  = (power == common_pkg::PD_P0) && !pipe_tx_elec_idle_i;
  // Loopback (P0, TxElecIdle 0, TxDetectRx 1): re-encode the received
  // words as they leave on PIPE; a code error is already EDB as a K symbol.
  assign loopback = tx_word && pipe_tx_detect_rx_i;

  assign pipe_tx = '{compliance: pipe_tx_compliance_i,
                     sym1: '{k: pipe_tx_datak_i[1], data: pipe_tx_data_i[15:8]},
                     sym0: '{k: pipe_tx_datak_i[0], data: pipe_tx_data_i[7:0]}};
  assign enc_in  = loopback ? '{compliance: 1'b0, sym1: rx_q.sym1, sym0: rx_q.sym0} : pipe_tx;

  pcie_pcs_enc8b10b u_enc (
    .clk_i   (clk_i),
    .rst_i   (pcs_rst),
    .valid_i (tx_word),
    .word_i  (enc_in),
    .valid_o (enc_valid),
    .data_o  (enc_data)
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

  common_pkg::t_rx_status rx_status;

  assign rx_status = det_pulse ? (rxdet_q ? common_pkg::RX_DETECTED : common_pkg::RX_OK)
                               : rx_q.status;

  assign pipe_phy_status_o   = pcs_rst || !ready || ack_pulse || det_pulse;
  assign pipe_rx_status_o    = pcs_rst ? 3'd0 : rx_status;
  assign pipe_rx_valid_o     = !pcs_rst && rx_q.valid;
  assign pipe_rx_data_o      = {rx_q.sym1.data, rx_q.sym0.data};
  assign pipe_rx_datak_o     = {rx_q.sym1.k, rx_q.sym0.k};
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
                       rl_q.err_cnt > pcie_pcs_pkg::ERR_CNT_W'(pcie_pcs_pkg::ERR_LIMIT - 1))
  // An underflow word is EDB, EDB as K symbols.
  `COMMON_ASSERT_NEVER(a_udf_edb, clk_i, pcs_rst, eb_valid && eb_status.udf
                       && (dec.sym0.sym != '{k: 1'b1, data: common_pkg::SYM_EDB}
                           || dec.sym1.sym != '{k: 1'b1, data: common_pkg::SYM_EDB}))
  // An underflow word never carries RxValid.
  `COMMON_ASSERT_NEVER(a_udf_not_valid, clk_i, pcs_rst, eb_valid && eb_status.udf && word_lock)
  // The power-acknowledge countdown never exceeds its start value.
  `COMMON_ASSERT_NEVER(a_ack_cnt_range, clk_i, pcs_rst,
                       ack_cnt_q > pcie_pcs_pkg::ACK_CNT_W'(pcie_pcs_pkg::POWER_ACK_CYCLES))

endmodule

`default_nettype wire
