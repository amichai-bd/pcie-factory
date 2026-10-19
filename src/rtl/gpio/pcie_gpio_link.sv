// pcie_gpio link state and receive rules (wiki/src/rtl/gpio/MAS.md#link-state):
// alignment, the runs that take the link up and down, Hot Reset, the quiet
// run, and the receive table that turns slots into the dll receive stream
// (wiki/src/arch/gpio/HAS.md#receive, wiki/src/arch/gpio/LINK_UP.md).
// One slot is handled per clk_i cycle, as the receive crossing delivers it.
`default_nettype none

`include "common_macros.svh"

module pcie_gpio_link #(
  parameter int unsigned CLK_DIV = 2  // even, 2..64: clk_i cycles per transfer
) (
  input  logic                    clk_i,
  input  logic                    rst_i,
  input  logic                    port_role_i,      // 0 downstream, 1 upstream
  input  logic                    hot_reset_req_i,  // level, downstream role only

  // Received slots (the receive crossing) and the alignment flag back.
  input  logic                    slot_valid_i,
  input  pcie_gpio_pkg::t_rx_slot slot_i,
  output logic                    aligned_o,        // flop output, to the receive front end

  // Transmitter control and its slot reports.
  output pcie_gpio_pkg::t_tx_mode tx_mode_o,
  input  pcie_gpio_pkg::t_tx_sent tx_sent_i,

  // dll receive stream and link status (registered).
  output pcie_gpio_pkg::t_rx_beat rx_beat_o,
  output logic                    link_up_o,
  output logic [5:0]              ltssm_state_o,
  output logic                    rcv_err_o
);

  // No slot for WATCHDOG_XFERS own transfer times: alignment loss.
  localparam int unsigned WD_LIMIT = pcie_gpio_pkg::WATCHDOG_XFERS * CLK_DIV;
  localparam int unsigned WD_W     = $clog2(WD_LIMIT + 1);

  pcie_gpio_pkg::t_link    s_q;
  pcie_gpio_pkg::t_link    s_d;
  logic [WD_W-1:0]         wd_q;
  logic [WD_W-1:0]         wd_d;
  pcie_gpio_pkg::t_rx_beat beat_q;
  pcie_gpio_pkg::t_rx_beat beat_d;
  logic                    rcv_err_q;
  logic                    rcv_err_d;

  // ------------------------------------------------------------ slot decode

  logic                 marker;      // transfer 0 read 1
  pcie_gpio_pkg::t_code code;
  logic [15:0]          word;
  logic                 is_sop;      // SOP_TLP, SOP_DLLP, ONE_TLP or ONE_DLLP
  logic                 is_one;      // ONE_TLP or ONE_DLLP
  logic                 code_dllp;   // the SOP or ONE code's DLLP bit
  logic                 is_marker4;  // the control pin read 1, 0, 0, 0

  assign marker     = slot_i.ctrl[0];
  assign code       = pcie_gpio_pkg::t_code'(slot_i.ctrl[3:1]);
  assign word       = slot_i.data;
  assign is_one     = (code == pcie_gpio_pkg::CODE_ONE_TLP) | (code == pcie_gpio_pkg::CODE_ONE_DLLP);
  assign is_sop     = is_one | (code == pcie_gpio_pkg::CODE_SOP_TLP)
                    | (code == pcie_gpio_pkg::CODE_SOP_DLLP);
  assign code_dllp  = slot_i.ctrl[1];
  assign is_marker4 = slot_i.ctrl == 4'b0001;

  // ----------------------------------------------------------- next state

  logic hr_ds;        // downstream in Hot Reset: received words are ignored
  logic table_on;     // the receive table applies to this slot
  logic quiet_over;
  logic hr_req;
  logic wd_fire;
  // Receive table results for this slot.
  logic pulse;        // rcv_err_o: the slot is damaged
  logic dmg;          // damaged for the alignment rule
  logic good;         // a good slot (LINK_UP.md, up)
  logic k_clear;      // a TRAIN word with bit 8 clear
  logic k_ok;         // a TRAIN word with bit 8 set
  logic k_hr;         // a HOT_RESET word
  logic b_close;      // close the open packet with an error eop
  logic b_beat;       // deliver this slot's beat
  logic b_sop;
  logic b_eop;
  logic b_dllp;
  // Transitions.
  logic loss;         // alignment loss
  logic down;         // leaving up
  logic quiet_start;

  assign hr_ds      = (s_q.state == pcie_gpio_pkg::ST_HOT_RESET) & ~port_role_i;
  assign table_on   = s_q.aligned & ~hr_ds;
  assign quiet_over = s_q.quiet_cnt == 5'(pcie_gpio_pkg::QUIET_WORDS);
  assign hr_req     = ~port_role_i & hot_reset_req_i;
  assign wd_fire    = s_q.aligned & ~slot_valid_i & (wd_q == WD_W'(WD_LIMIT - 1));
  assign wd_d       = slot_valid_i ? '0 : (wd_q == WD_W'(WD_LIMIT)) ? wd_q : wd_q + 1'b1;

  // The receive table (HAS, receive) and the idle words (HAS, idle words).
  always_comb begin
    pulse   = 1'b0;
    good    = 1'b0;
    k_clear = 1'b0;
    k_ok    = 1'b0;
    k_hr    = 1'b0;
    b_close = 1'b0;
    b_beat  = 1'b0;
    b_sop   = 1'b0;
    b_eop   = 1'b0;
    b_dllp  = s_q.open_dllp;
    if (!marker || slot_i.new_phase) begin
      // Transfer 0 read 0 (or the front end moved its phase): damaged.
      pulse   = 1'b1;
      b_close = s_q.open;
    end else begin
      unique case (code)
        pcie_gpio_pkg::CODE_IDLE: begin
          if (s_q.open) begin
            pulse   = 1'b1;
            b_close = 1'b1;
          end else begin
            unique case (word)
              pcie_gpio_pkg::WORD_TRAIN:     k_clear = 1'b1;
              pcie_gpio_pkg::WORD_TRAIN_OK: begin
                k_ok = 1'b1;
                good = 1'b1;
              end
              pcie_gpio_pkg::WORD_IDLE:      good    = 1'b1;
              pcie_gpio_pkg::WORD_HOT_RESET: k_hr    = 1'b1;
              default:                       pulse   = 1'b1;
            endcase
          end
        end
        pcie_gpio_pkg::CODE_MID,
        pcie_gpio_pkg::CODE_EOP: begin
          if (s_q.open) begin
            good   = 1'b1;
            b_beat = 1'b1;
            b_eop  = code == pcie_gpio_pkg::CODE_EOP;
          end else if (!s_q.discard) begin
            pulse = 1'b1;
          end
        end
        pcie_gpio_pkg::CODE_ABORT: begin
          if (s_q.open) begin
            good    = 1'b1;
            b_close = 1'b1;
          end else if (!s_q.discard) begin
            pulse = 1'b1;
          end
        end
        default: begin
          // SOP_TLP, SOP_DLLP, ONE_TLP, ONE_DLLP.
          if (s_q.open) begin
            pulse   = 1'b1;
            b_close = 1'b1;
          end else begin
            good   = 1'b1;
            b_beat = 1'b1;
            b_sop  = 1'b1;
            b_eop  = is_one;
            b_dllp = code_dllp;
          end
        end
      endcase
    end
  end

  assign dmg = slot_valid_i & s_q.aligned
             & (table_on ? pulse : (!marker || slot_i.new_phase));

  always_comb begin
    s_d         = s_q;
    beat_d      = '0;
    beat_d.data = word;
    rcv_err_d   = 1'b0;
    loss        = 1'b0;
    down        = 1'b0;
    quiet_start = 1'b0;

    // Words sent: the quiet run and the Hot Reset dwell (own slots).
    if (tx_sent_i.train_clear && !quiet_over) begin
      s_d.quiet_cnt = s_q.quiet_cnt + 5'd1;
    end
    if (tx_sent_i.hot_reset && s_q.hr_cnt != 6'(pcie_gpio_pkg::HR_WORDS)) begin
      s_d.hr_cnt = s_q.hr_cnt + 6'd1;
    end

    // Alignment (LINK_UP.md, rules).
    if (!s_q.aligned) begin
      if (slot_valid_i) begin
        if (!is_marker4) begin
          s_d.align_cnt = '0;
        end else if (slot_i.new_phase) begin
          s_d.align_cnt = 4'd1;
        end else begin
          s_d.align_cnt = s_q.align_cnt + 4'd1;
        end
        if (is_marker4 && (slot_i.new_phase ? 4'd1 : s_q.align_cnt + 4'd1)
                          == 4'(pcie_gpio_pkg::ALIGN_SLOTS)) begin
          s_d.aligned = 1'b1;
          s_d.dmg_win = '0;
        end
      end
    end else begin
      if (dmg) begin
        if (s_q.dmg_win != '0) begin
          loss = 1'b1;
        end
        s_d.dmg_win = 4'(pcie_gpio_pkg::DAMAGE_WINDOW);
      end else if (slot_valid_i && s_q.dmg_win != '0) begin
        s_d.dmg_win = s_q.dmg_win - 4'd1;
      end
      if (wd_fire) begin
        loss = 1'b1;
      end
    end

    // Receive framing and rcv_err_o (table on, one slot).
    if (slot_valid_i && table_on) begin
      rcv_err_d = pulse;
      if (pulse) begin
        s_d.discard = 1'b1;
      end else if ((code == pcie_gpio_pkg::CODE_IDLE) || is_sop) begin
        s_d.discard = 1'b0;
      end
      if (b_close) begin
        s_d.open = 1'b0;
      end
      if (b_sop && !b_eop) begin
        s_d.open      = 1'b1;
        s_d.open_dllp = b_dllp;
      end else if (b_eop) begin
        s_d.open = 1'b0;
      end
    end else if (slot_valid_i) begin
      // Not aligned, or ignored in a downstream Hot Reset: no framing.
      s_d.open    = 1'b0;
      s_d.discard = 1'b0;
    end

    // Runs (LINK_UP.md, rules): one counter per kind; any other slot breaks it.
    if (slot_valid_i && table_on) begin
      s_d.run_good  = (s_q.state == pcie_gpio_pkg::ST_TRAIN && quiet_over && good && !pulse)
                    ? s_q.run_good + 4'd1 : '0;
      s_d.run_clear = (s_q.state == pcie_gpio_pkg::ST_UP && k_clear) ? s_q.run_clear + 3'd1 : '0;
      s_d.run_any   = (s_q.state == pcie_gpio_pkg::ST_UP && (k_clear || k_ok))
                    ? s_q.run_any + 7'd1 : '0;
      s_d.run_hr    = (port_role_i && s_q.state != pcie_gpio_pkg::ST_HOT_RESET && k_hr)
                    ? s_q.run_hr + 3'd1 : '0;
      s_d.run_exit  = (s_q.state == pcie_gpio_pkg::ST_HOT_RESET && (k_clear || k_ok))
                    ? s_q.run_exit + 3'd1 : '0;
    end

    // State (LINK_UP.md, rules and Hot Reset).
    unique case (s_q.state)
      pcie_gpio_pkg::ST_UP: begin
        if (hr_req) begin
          s_d.state  = pcie_gpio_pkg::ST_HOT_RESET;
          s_d.hr_cnt = '0;
          down       = 1'b1;
        end else if (loss) begin
          s_d.state   = pcie_gpio_pkg::ST_TRAIN;
          down        = 1'b1;
          quiet_start = 1'b1;
        end else if (s_d.run_hr == 3'(pcie_gpio_pkg::HR_ENTER_RUN)) begin
          s_d.state   = pcie_gpio_pkg::ST_HOT_RESET;
          down        = 1'b1;
          quiet_start = 1'b1;
        end else if (s_d.run_clear == 3'(pcie_gpio_pkg::DOWN_CLEAR_RUN)
                     || s_d.run_any == 7'(pcie_gpio_pkg::DOWN_ANY_RUN)) begin
          s_d.state   = pcie_gpio_pkg::ST_TRAIN;
          down        = 1'b1;
          quiet_start = 1'b1;
        end
      end
      pcie_gpio_pkg::ST_HOT_RESET: begin
        if (!port_role_i) begin
          if (!hot_reset_req_i && s_q.hr_cnt == 6'(pcie_gpio_pkg::HR_WORDS)) begin
            s_d.state   = pcie_gpio_pkg::ST_TRAIN;
            quiet_start = 1'b1;
          end
        end else if (loss) begin
          s_d.state   = pcie_gpio_pkg::ST_TRAIN;
          quiet_start = 1'b1;
        end else if (s_d.run_exit == 3'(pcie_gpio_pkg::HR_EXIT_RUN)) begin
          s_d.state = pcie_gpio_pkg::ST_TRAIN;
        end
      end
      default: begin
        // ST_TRAIN: not aligned, or training.
        if (hr_req) begin
          s_d.state  = pcie_gpio_pkg::ST_HOT_RESET;
          s_d.hr_cnt = '0;
        end else if (loss) begin
          quiet_start = 1'b1;
        end else if (s_d.run_hr == 3'(pcie_gpio_pkg::HR_ENTER_RUN)) begin
          s_d.state   = pcie_gpio_pkg::ST_HOT_RESET;
          quiet_start = 1'b1;
        end else if (s_d.run_good == 4'(pcie_gpio_pkg::UP_RUN)) begin
          s_d.state = pcie_gpio_pkg::ST_UP;
        end
      end
    endcase

    if (loss) begin
      s_d.aligned   = 1'b0;
      s_d.align_cnt = '0;
      s_d.dmg_win   = '0;
      s_d.open      = 1'b0;
      s_d.discard   = 1'b0;
    end
    if (quiet_start) begin
      s_d.quiet_cnt = '0;
    end
    // Runs restart on every state change: the down runs count only slots
    // received while up, from 0 at going up.
    if (s_d.state != s_q.state || loss) begin
      s_d.run_good  = '0;
      s_d.run_clear = '0;
      s_d.run_any   = '0;
      s_d.run_hr    = '0;
      s_d.run_exit  = '0;
    end

    // Delivery (up only). At link-down an open delivered packet is closed
    // with an error eop (HAS, link-up).
    if (down) begin
      if (s_q.deliv) begin
        beat_d.valid = 1'b1;
        beat_d.eop   = 1'b1;
        beat_d.err   = 1'b1;
        beat_d.dllp  = s_q.open_dllp;
      end
      s_d.deliv = 1'b0;
    end else if (s_q.state == pcie_gpio_pkg::ST_UP && slot_valid_i && table_on) begin
      if (b_close) begin
        beat_d.valid = s_q.deliv;
        beat_d.eop   = 1'b1;
        beat_d.err   = 1'b1;
        beat_d.dllp  = s_q.open_dllp;
        s_d.deliv    = 1'b0;
      end else if (b_beat && (b_sop || s_q.deliv)) begin
        beat_d.valid = 1'b1;
        beat_d.sop   = b_sop;
        beat_d.eop   = b_eop;
        beat_d.dllp  = b_dllp;
        s_d.deliv    = !b_eop;
      end
    end else if (s_q.state != pcie_gpio_pkg::ST_UP) begin
      s_d.deliv = 1'b0;
    end
  end

  `COMMON_DFF_RST_VAL(s_q, s_d, clk_i, rst_i, pcie_gpio_pkg::LINK_RESET)
  `COMMON_DFF_RST(wd_q, wd_d, clk_i, rst_i)
  `COMMON_DFF_RST(beat_q, beat_d, clk_i, rst_i)
  `COMMON_DFF_RST(rcv_err_q, rcv_err_d, clk_i, rst_i)

  // ---------------------------------------------------------------- outputs

  assign aligned_o           = s_q.aligned;
  assign tx_mode_o.up        = s_q.state == pcie_gpio_pkg::ST_UP;
  assign tx_mode_o.hot_reset = hr_ds;
  assign tx_mode_o.bit8      = s_q.aligned & quiet_over;
  assign rx_beat_o           = beat_q;
  assign rcv_err_o           = rcv_err_q;
  assign link_up_o           = s_q.state == pcie_gpio_pkg::ST_UP;

  always_comb begin
    unique case (s_q.state)
      pcie_gpio_pkg::ST_UP:        ltssm_state_o = pcie_gpio_pkg::LTSSM_UP;
      pcie_gpio_pkg::ST_HOT_RESET: ltssm_state_o = pcie_gpio_pkg::LTSSM_HOT_RESET;
      default: ltssm_state_o = s_q.aligned ? pcie_gpio_pkg::LTSSM_TRAINING
                                           : pcie_gpio_pkg::LTSSM_RESET;
    endcase
  end

  // Invariants (MAS, link state).
  // Up only while aligned; the quiet run is over whenever up.
  `COMMON_ASSERT_NEVER(a_up_aligned, clk_i, rst_i,
                       (s_q.state == pcie_gpio_pkg::ST_UP) & ~(s_q.aligned & quiet_over))
  // A delivered packet is an open one; the discard state never has one open.
  `COMMON_ASSERT_NEVER(a_deliv_open, clk_i, rst_i, s_q.deliv & ~s_q.open)
  `COMMON_ASSERT_NEVER(a_discard_closed, clk_i, rst_i, s_q.discard & s_q.open)

endmodule

`default_nettype wire
