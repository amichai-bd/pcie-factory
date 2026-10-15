// Types, constants and pure helpers private to pcie_mac
// (wiki/src/rtl/mac/MAS.md#package). The codes it shares with the other
// IPs (symbols, LTSSM states, PowerDown) are in common_pkg.
`default_nettype none

package pcie_mac_pkg;

  localparam int unsigned BYTE_W = 8;
  localparam int unsigned BEAT_W = 16;  // two bytes, [7:0] first

  typedef logic [BYTE_W-1:0] t_byte;
  typedef logic [BEAT_W-1:0] t_beat_data;

  // ----------------------------------------------- block interfaces (Q25)

  // Upper packet stream, transmit beat (valid and ready stay single wires).
  typedef struct packed {
    t_beat_data data;     // [7:0] is the earlier byte
    logic       sop;
    logic       eop;
    logic       dllp;     // 0 TLP, 1 DLLP; read on the sop beat
    logic       nullify;  // on a TLP's eop beat: end with EDB
  } t_pkt_tx;

  // Upper packet stream, receive beat.
  typedef struct packed {
    logic       valid;
    t_beat_data data;
    logic       sop;
    logic       eop;
    logic       dllp;
    logic       err;      // with eop only
    logic       nullify;  // with eop only
  } t_pkt_rx;

  // Ordered-set requests, LTSSM to the ordered-set block: at most one high.
  typedef struct packed {
    logic ts;
    logic skp;         // requested SKP: the SKP count restarts at 0
    logic eios;
    logic fts;
    logic compliance;
    logic pattern;     // the loopback master's data pattern (one word per unit)
  } t_os_req;

  // TS fields sent with a TS request.
  typedef struct packed {
    logic       ts2;       // 0 TS1, 1 TS2
    logic       link_pad;  // link field is PAD
    t_byte      link;      // link number when not PAD
    logic       lane_pad;  // lane field is PAD
    t_byte      lane;      // lane number when not PAD
    logic [2:0] ctl;       // training control bits 2:0
  } t_ts_tx;

  // Transmit events of the ordered-set block for the word on the line.
  typedef struct packed {
    logic valid;       // the block sends this word
    logic ack;         // a requested set starts in this word
    logic last;        // the last word of a set
    logic compliance;  // TxCompliance for this word
  } t_os_tx;

  // Receive events of one word, from the ordered-set block.
  typedef struct packed {
    logic [1:0] os_sym;  // symbol i belongs to an ordered set
    logic       ts;      // a TS ended and decoded
    logic       ts_bad;  // a TS ended and failed to decode, or was cut short
    logic       skp;
    logic       eios;
    logic       fts;
  } t_os_rx;

  // Fields of the TS that ends with the ts event (valid with it only).
  typedef struct packed {
    logic       ts2;       // TS2 (else TS1)
    logic       ts_inv;    // inverted identifier
    t_byte      link;
    logic       link_pad;  // link field is PAD
    logic       link_num;  // link field is a number (neither: another K)
    t_byte      lane;
    logic       lane_pad;
    logic       lane_num;
    t_byte      n_fts;
    logic [4:0] ctl;       // training control bits 4:0
  } t_ts_rx;

  // LTSSM control of the frame block.
  typedef struct packed {
    logic tx_l0;    // the frame owns the line (idle, packets)
    logic tx_stop;  // start no packet
    logic rx_l0;    // main state L0 and Rx_L0: receive packets
  } t_fr_ctl;

  // The frame's transmit status for the LTSSM, of this cycle's line word:
  // idle symbols sent (the Idle states' count), and for the power states the
  // packet status and the upper layer's offer (the L0s idle count, the L1
  // and L2 entry wait, the L0s exit).
  typedef struct packed {
    logic idle;   // the frame drives the word: two idle symbols
    logic pkt;    // the word carries a packet symbol
    logic open;   // packet symbols remain after this word
    logic offer;  // pkt_tx_valid_i: the upper layer offers a beat
  } t_fr_tx_stat;

  // LTSSM control of the transmit line: SKP schedule and scrambling (MAS,
  // Top).
  typedef struct packed {
    logic skp_hold;    // loopback slave: the SKP count holds, no SKP is scheduled
    logic tx_scr_off;  // Disable Scrambling: send data symbols as they are
  } t_line_ctl;

  // Link-control requests of the upper layer.
  typedef struct packed {
    logic       retrain;    // pulse: L0 to Recovery
    logic       hot_reset;  // level; downstream role only
    logic       disable_link;  // level; downstream role only
    logic       loopback;   // level
    logic [1:0] pm_state;   // 0 L0, 1 L0s, 2 L1, 3 L2
  } t_link_req;

  // ---------------------------------------------------------- scrambler

  localparam int unsigned LFSR_W = 16;

  typedef logic [LFSR_W-1:0] t_lfsr;

  // G(X) = X^16 + X^5 + X^4 + X^3 + 1: bit 15 feeds back into bits 0, 3, 4
  // and 5 [cite: PCIe Base Spec, rev 3.0, §4.2.1.3].
  localparam t_lfsr LFSR_TAPS = 16'h0039;

  // Key byte of a state: key bit i is state bit 15 before shift i.
  function automatic t_byte lfsr_key(t_lfsr state);
    t_lfsr s;
    t_byte key;
    s   = state;
    key = '0;
    for (int unsigned i = 0; i < BYTE_W; i++) begin
      key[i] = s[LFSR_W-1];
      s      = {s[LFSR_W-2:0], 1'b0} ^ (s[LFSR_W-1] ? LFSR_TAPS : '0);
    end
    return key;
  endfunction

  // State after eight shifts (one symbol's advance).
  function automatic t_lfsr lfsr_advance(t_lfsr state);
    t_lfsr s;
    s = state;
    for (int unsigned i = 0; i < BYTE_W; i++) begin
      s = {s[LFSR_W-2:0], 1'b0} ^ (s[LFSR_W-1] ? LFSR_TAPS : '0);
    end
    return s;
  endfunction

  // ------------------------------------------------------- ordered sets

  // Training control bits.
  localparam int unsigned TX_CTL_W = 3;  // Hot Reset, Disable Link, Loopback
  localparam int unsigned RX_CTL_W = 5;  // ... Disable Scrambling, Compliance Receive
  // Their bit positions (SYMBOLS.md, TS1 and TS2).
  localparam int unsigned CTL_HOT_RESET   = 0;
  localparam int unsigned CTL_DISABLE     = 1;
  localparam int unsigned CTL_LOOPBACK    = 2;
  localparam int unsigned CTL_NO_SCRAMBLE = 3;

  // SKP schedule: symbols between SKP ordered sets, and the count width.
  localparam int unsigned SKP_INTERVAL = 1180;
  localparam int unsigned SKP_CREDIT_W = 14;
  localparam logic [SKP_CREDIT_W-1:0] SKP_CREDIT_MAX = '1;

  // Words per ordered set.
  localparam int unsigned TS_WORDS = 8;
  localparam int unsigned OS_WORDS = 2;   // SKP, EIOS, FTS, compliance unit
  localparam int unsigned TX_IDX_W = 3;

  // Transmit set kinds.
  typedef enum logic [2:0] {
    KIND_TS    = 3'd0,
    KIND_SKP   = 3'd1,
    KIND_EIOS  = 3'd2,
    KIND_FTS   = 3'd3,
    KIND_COMPL = 3'd4,
    KIND_PAT   = 3'd5   // loopback master pattern: one word
  } t_tx_kind;

  // Receive parser phases (MAS, Ordered sets, receive).
  typedef enum logic [2:0] {
    RX_OUT  = 3'd0,  // outside an ordered set
    RX_COM  = 3'd1,  // after COM
    RX_TS   = 3'd2,  // inside a TS
    RX_SKP  = 3'd3,  // SKP body
    RX_BODY = 3'd4   // EIOS or FTS body
  } t_rx_phase;

  typedef struct packed {
    t_rx_phase              phase;
    logic [3:0]             idx;      // TS symbols after COM received, 1..15
    logic                   link_k;
    logic [7:0]             link;
    logic                   lane_k;
    logic [7:0]             lane;
    logic [7:0]             n_fts;
    logic [RX_CTL_W-1:0]    ctl;
    logic                   id_k;     // first identifier symbol
    logic [7:0]             id;
    logic                   id_same;  // identifiers so far equal to the first
  } t_rx_state;

  localparam t_rx_state RX_STATE_RESET = '{phase: RX_OUT, default: '0};

  typedef struct packed {
    logic os_sym;  // the symbol belongs to an ordered set
    logic ts;      // a TS completed and decoded
    logic ts_bad;  // a TS failed to decode or was cut short by COM
    logic skp;
    logic eios;
    logic fts;
  } t_rx_ev;

  typedef struct packed {
    t_rx_state st;
    t_rx_ev    ev;
  } t_rx_step;

  // A TS identifier symbol that decodes: TS1, TS2 or their inverses.
  function automatic logic id_known(input logic [7:0] b);
    return (b == common_pkg::TS1_ID) || (b == common_pkg::TS2_ID)
        || (b == common_pkg::TS1_ID_INV) || (b == common_pkg::TS2_ID_INV);
  endfunction

  // Advance the receive parser by one symbol (k, b); return the new state
  // and the symbol's events.
  function automatic t_rx_step rx_step(input t_rx_state st, input logic k,
                                       input logic [7:0] b);
    t_rx_step r;
    logic     same;
    r.st = st;
    r.ev = '0;
    same = 1'b0;
    if (k && (b == common_pkg::SYM_COM)) begin
      r.ev.os_sym = 1'b1;
      r.ev.ts_bad = (st.phase == RX_TS);
      r.st.phase  = RX_COM;
    end else begin
      case (st.phase)
        RX_COM: begin
          r.ev.os_sym = 1'b1;
          if (k && (b == common_pkg::SYM_SKP)) begin
            r.ev.skp   = 1'b1;
            r.st.phase = RX_SKP;
          end else if (k && (b == common_pkg::SYM_IDL)) begin
            r.ev.eios  = 1'b1;
            r.st.phase = RX_BODY;
          end else if (k && (b == common_pkg::SYM_FTS)) begin
            r.ev.fts   = 1'b1;
            r.st.phase = RX_BODY;
          end else begin
            r.st.phase  = RX_TS;
            r.st.idx    = 4'd1;
            r.st.link_k = k;
            r.st.link   = b;
          end
        end
        RX_TS: begin
          r.ev.os_sym = 1'b1;
          r.st.idx    = st.idx + 4'd1;
          case (r.st.idx)
            4'd2: begin
              r.st.lane_k = k;
              r.st.lane   = b;
            end
            4'd3: r.st.n_fts = b;
            4'd4: ;  // data rate identifier: ignored (Gen1 only)
            4'd5: r.st.ctl = b[RX_CTL_W-1:0];
            4'd6: begin
              r.st.id_k    = k;
              r.st.id      = b;
              r.st.id_same = 1'b1;
            end
            default: begin
              same          = st.id_same && (k == st.id_k) && (b == st.id);
              r.st.id_same  = same;
              if (r.st.idx == 4'd15) begin
                r.st.phase = RX_OUT;
                r.ev.ts     = same && !st.id_k && id_known(st.id);
                r.ev.ts_bad = !(same && !st.id_k && id_known(st.id));
              end
            end
          endcase
        end
        RX_SKP: begin
          if (k && (b == common_pkg::SYM_SKP)) begin
            r.ev.os_sym = 1'b1;
          end else begin
            r.st.phase = RX_OUT;
          end
        end
        RX_BODY: begin
          if (k && ((b == common_pkg::SYM_IDL) || (b == common_pkg::SYM_FTS))) begin
            r.ev.os_sym = 1'b1;
          end else begin
            r.st.phase = RX_OUT;
          end
        end
        default: ;  // RX_OUT: not an ordered-set symbol
      endcase
    end
    return r;
  endfunction

  // ------------------------------------------------------------ framing

  // Symbols after a COM in a TS1 or TS2 (SYMBOLS.md, TS1 and TS2).
  localparam int unsigned TS_BODY = 15;
  // Bytes in a DLLP (SYMBOLS.md, Framing).
  localparam int unsigned DLLP_BYTES = 6;
  // Saturating receive byte count: enough to tell 6 from not 6.
  localparam int unsigned LEN_W = 3;
  typedef logic [LEN_W-1:0] t_len;
  localparam int unsigned TS_CNT_W = 4;
  typedef logic [TS_CNT_W-1:0] t_ts_cnt;

  // Receive ordered-set skipping: where the parser is relative to the
  // ordered set the last COM started.
  typedef enum logic [2:0] {
    OS_NONE  = 3'd0,  // not in an ordered set
    OS_FIRST = 3'd1,  // COM seen; the next symbol tells the kind
    OS_TS    = 3'd2,  // TS1/TS2 body: TS_BODY symbols after the COM
    OS_SKP   = 3'd3,  // SKP ordered set: a run of SKP
    OS_RUN   = 3'd4   // EIOS or FTS: a run of IDL or FTS
  } t_os_state;

  // One receive beat, as it waits for the upper stream.
  typedef struct packed {
    t_beat_data data;
    logic       sop;
    logic       eop;
    logic       dllp;
    logic       err;      // only with eop
    logic       nullify;  // only with eop
  } t_rx_beat;

  typedef logic [1:0] t_rxq_cnt;

  // Frame state (Q25): reset and unreset registers in separate structs.
  typedef struct packed {
    logic up_open;    // a packet's sop was accepted, its eop not yet
    logic line_open;  // the packet's start is on the line, its end not yet
  } t_fr_tx_ctl;

  typedef struct packed {
    t_byte carry;     // packet byte for [7:0] of the next word
    logic  dllp;
    logic  tail_edb;  // the packet's end symbol is EDB (nullified TLP)
  } t_fr_tx_data;

  typedef struct packed {
    t_os_state os;      // ordered-set skipping
    logic      in_pkt;  // a packet is open
    logic      resync;  // after a framing error: discard to STP, SDP, END or EDB
    logic      hold_v;  // one byte waiting for its pair
    logic      pend_v;  // a full beat waiting to learn whether it is the last
  } t_fr_rx_ctl;

  localparam t_fr_rx_ctl FR_RX_CTL_RESET = '{os: OS_NONE, default: '0};

  typedef struct packed {
    t_ts_cnt    ts_cnt;
    logic       pkt_dllp;
    logic       pkt_bad;    // a word inside it had an RxStatus error
    logic       pkt_first;  // its first beat is not formed yet
    t_len       len;        // bytes so far, saturating
    t_byte      hold;
    t_beat_data pend;
    logic       pend_sop;
  } t_fr_rx_data;

  typedef struct packed {
    t_rx_beat q0;  // presented
    t_rx_beat q1;
  } t_fr_rxq;

  // -------------------------------------------------------------- LTSSM

  // What the transmit datapath sends while pipe_tx_elec_idle_o is 0
  // (MAS, LTSSM).
  typedef enum logic [3:0] {
    TX_NONE       = 4'd0,  // electrical idle: nothing is sent
    TX_TS         = 4'd1,  // TS1 or TS2 with the tx_ts_* fields
    TX_IDLE       = 4'd2,  // idle data symbols
    TX_COMPLIANCE = 4'd3,  // the compliance pattern
    TX_L0         = 4'd4,  // L0: packets, idle between them
    TX_EIOS       = 4'd5,  // one EIOS, then electrical idle
    TX_PATTERN    = 4'd6,  // the loopback master's data pattern
    TX_LOOP       = 4'd7,  // loopback slave: idle data, TxDetectRx high
    TX_FTS        = 4'd8   // Tx_L0s.FTS: the partner's N_FTS FTS, then one SKP
  } t_tx_mode;

  // Timeouts in clk_i cycles (8 ns), wiki/src/arch/mac/ARCS.md.
  localparam int unsigned TIMER_W             = 23;         // holds 48 ms
  localparam int unsigned DETECT_QUIET_CYCLES = 1_500_000;  // 12 ms, T
  localparam int unsigned HOT_RESET_CYCLES    = 250_000;    // 2 ms Hot Reset dwell, T
  localparam int unsigned T2MS_CYCLES         = 250_000;    // 2 ms, E
  localparam int unsigned T24MS_CYCLES        = 3_000_000;  // 24 ms, E
  localparam int unsigned T48MS_CYCLES        = 6_000_000;  // 48 ms, E
  localparam int unsigned ERR_TIMEOUT_FLOOR   = 16_384;     // 131 us
  localparam int unsigned DIV_MAX             = 1024;

  // Counts (wiki/src/arch/mac/ARCS.md).
  localparam int unsigned TS_SENT_W         = 11;    // up to 1024
  localparam int unsigned POLLING_MIN_TS1   = 1024;
  localparam int unsigned RUN_W             = 4;     // runs saturate at 8
  localparam int unsigned RUN_LONG          = 8;     // "8 consecutive"
  localparam int unsigned RUN_SHORT         = 2;     // "2 consecutive"
  localparam int unsigned AFTER_W           = 5;     // saturates at 16
  localparam int unsigned AFTER_COUNT       = 16;    // "16 sent after receiving one"
  localparam int unsigned IDLE_TO_RLOCK_MAX = 255;
  localparam int unsigned DISABLE_TS1       = 16;    // TS1 sent in Disabled
  localparam int unsigned DIS_TS_W          = 5;
  localparam int unsigned TX_IDLE_MIN       = 3;     // 20 ns of electrical idle, in cycles
  localparam int unsigned EI_CNT_W          = 2;     // saturates at TX_IDLE_MIN

  // Power states (wiki/src/arch/mac/ARCS.md#l0s-sub-machines).
  localparam int unsigned L0S_CNT_W   = 10;   // L0S_IDLE_CYCLES up to 875
  localparam int unsigned RX_CNT_W    = 10;   // Rx_L0s.FTS timeout up to 542

  typedef logic [TIMER_W-1:0]   t_timer;
  typedef logic [TS_SENT_W-1:0] t_ts_sent;
  typedef logic [RUN_W-1:0]     t_run;
  typedef logic [AFTER_W-1:0]   t_after;

  // An error-path timeout (E): t at divider 1, else t / div floored at
  // ERR_TIMEOUT_FLOOR (wiki/src/arch/mac/HAS.md#parameters).
  function automatic int unsigned err_timeout(int unsigned t, int unsigned div);
    int unsigned q;
    q = t / div;
    if (div == 1) begin
      return t;
    end
    return (q > ERR_TIMEOUT_FLOOR) ? q : ERR_TIMEOUT_FLOOR;
  endfunction

  // A run of consecutive matching TS after one received TS: a TS that
  // breaks or does not match restarts it; it saturates at RUN_LONG.
  function automatic t_run run_next(t_run run, logic good, logic bad, logic hit);
    if (bad || (good && !hit)) begin
      return '0;
    end
    if (good && run != t_run'(RUN_LONG)) begin
      return run + 1'b1;
    end
    return run;
  endfunction

  // A count of symbols or TS sent, plus add, saturating at AFTER_COUNT.
  function automatic t_after after_next(t_after a, logic [1:0] add);
    logic [AFTER_W:0] s;
    s = {1'b0, a} + {{(AFTER_W-1){1'b0}}, add};
    return (s > (AFTER_W+1)'(AFTER_COUNT)) ? t_after'(AFTER_COUNT) : s[AFTER_W-1:0];
  endfunction

  // Power-state registers of the LTSSM (Q25): the L0 exit and L1/L2 entry
  // handshake, the transmitter's EIOS and FTS sequence, and the L0s
  // sub-machines' counts and receive flags. All restart on a state entry.
  typedef struct packed {
    logic                 l0_exit;       // a Recovery exit from L0 waits for the sub-machines
    logic                 pm_wait;       // upstream: EIOS sent in L0, waiting for the partner's
    logic                 ev_eios_pm;    // an EIOS received in Rx_L0 while L1 or L2 is requested
    logic                 eios_q;        // an EIOS is queued, not yet started
    logic [7:0]           fts_sent;      // Tx_L0s.FTS: FTS started
    logic                 fts_skp;       // Tx_L0s.FTS: the closing SKP started
    logic [L0S_CNT_W-1:0] idle_cnt;      // Tx_L0: cycles without traffic, saturating
    logic [RX_CNT_W-1:0]  rx_cnt;        // cycles since the receive sub-machine moved
    logic                 rx_fts_to;     // the Rx_L0s.FTS timeout expired
    logic                 sub_ei_seen;   // electrical idle since Rx_L0s.Entry
    logic                 sub_ei_exit;   // ... and left since (EI exit)
    logic                 main_ei_seen;  // electrical idle since state entry
    logic                 main_ei_exit;  // ... and left since (EI exit)
  } t_pm_regs;

  // ----------------------------------------------------------------------
  // Every register of the LTSSM, reset together (MAS, Microarchitecture).
  typedef struct packed {
    // main machine and its state timer
    common_pkg::t_ltssm_state state;
    t_timer       timer;           // cycles since entry, saturating
    // L0s sub-machines (only meaningful while state is L0)
    common_pkg::t_l0s_sub     tx_sub;
    common_pkg::t_l0s_sub     rx_sub;
    // PIPE control
    logic         reset_n;         // pipe_reset_no
    logic         pcs_ready;       // PhyStatus seen low after reset
    common_pkg::t_power_down  pd;              // pipe_power_down_o
    logic         pd_pending;      // a PowerDown change awaits its PhyStatus pulse
    logic         tx_ei;           // pipe_tx_elec_idle_o
    logic         detect_rx;       // pipe_tx_detect_rx_o
    logic         detect_done;     // receiver detection reported (this Detect.Active)
    logic         detect_found;    // ... and found a receiver
    logic         polarity;        // pipe_rx_polarity_o
    // transmit control towards the ordered-set and frame blocks
    t_tx_mode     tx_mode;
    logic         tx_ts2;
    logic         tx_link_pad;
    logic [7:0]   tx_link;
    logic         tx_lane_pad;
    // status
    logic         link_up;
    logic [7:0]   rx_link;         // upstream: link number learned
    logic [7:0]   partner_n_fts;
    logic [7:0]   idle_to_rlock;
    // per-state receive and transmit counts, cleared on every entry
    t_ts_sent     ts_sent;         // TS started in this state
    t_run         run_go;          // run of TS matching the state's advance condition
    t_run         run_pad;         // run of TS1 with PAD fields (error exits)
    t_run         run_renum;       // Lanenum.Accept: TS1 with link N and a lane other than 0
    t_run         run_mism;        // RcvrCfg: TS1 whose link is not N or lane not 0
    logic         got8;            // 8 consecutive go TS (latched)
    logic         mism8;           // 8 consecutive mism TS1 (latched)
    logic         after_on;        // first go TS received
    t_after       after;           // TS sent since then
    logic         mism_after_on;   // first mism TS1 received
    t_after       mism_after;      // TS sent since then
    logic         ts_rx_any;       // any TS received in this state
    t_run         idle_run;        // consecutive idle symbols received
    logic         idle8;           // 8 consecutive idle symbols (latched)
    logic         idle_after_on;   // first idle symbol received
    t_after       idle_after;      // idle symbols sent since then
    logic         left_ei;         // pipe_rx_elec_idle_i was 0 since entry
    logic         eios_rx;         // EIOS received (since entry or Rx_L0 entry)
    logic         retrain;         // retrain_req_i latched in L0
    // control states (wiki/src/arch/mac/ARCS.md#control-states)
    logic         directed;        // Disabled, Hot Reset directed; Loopback master
    t_run         run_hr;          // run of TS1 with Hot Reset set
    t_run         run_dis;         // run of TS1 with Disable Link set
    t_run         run_lb;          // run of TS1 with Loopback set
    t_run         run_dscr;        // Configuration.Complete: go TS2 with Disable Scrambling
    logic         dscr;            // ... 2 consecutive received (latched)
    logic         scr_off;         // scrambling disabled, Configuration.Idle to Detect.Quiet
    logic [DIS_TS_W-1:0] dis_ts;   // TS1 started in Disabled, to 16
    logic         eios_done;       // this state's EIOS has started
    logic [EI_CNT_W-1:0] ei_cycles; // cycles in electrical idle, saturating at 3
    logic         ei_after_eios;   // electrical idle received after an EIOS
    t_pm_regs     pm;              // power states
  } t_regs;

  localparam t_regs REGS_RESET = '{
    state:         common_pkg::LTSSM_DETECT_QUIET,
    tx_sub:        common_pkg::L0S_SUB_L0,
    rx_sub:        common_pkg::L0S_SUB_L0,
    pd:            common_pkg::PD_P1,
    tx_ei:         1'b1,
    tx_mode:       TX_NONE,
    tx_link_pad:   1'b1,
    tx_lane_pad:   1'b1,
    partner_n_fts: 8'hFF,
    default:       '0
  };

endpackage

`default_nettype wire
