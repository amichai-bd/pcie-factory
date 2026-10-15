// Types, constants and pure helpers of the LTSSM sub-IP
// (wiki/src/rtl/mac_ltssm/MAS.md). The state encoding is the HAS one
// (wiki/src/arch/mac/LTSSM.md#state-encoding); pcie_mac_pkg::t_ltssm_state
// holds the same codes until the mac top integration reconciles the two.
`default_nettype none

package pcie_mac_ltssm_pkg;

  localparam int unsigned LTSSM_STATE_W = 6;

  // Main LTSSM states as ltssm_state_o shows them (the full HAS encoding,
  // power and control states included). In L0 the code is
  // LTSSM_L0 | tx << 2 | rx.
  typedef enum logic [LTSSM_STATE_W-1:0] {
    LTSSM_DETECT_QUIET          = 6'h00,
    LTSSM_DETECT_ACTIVE         = 6'h01,
    LTSSM_POLLING_ACTIVE        = 6'h04,
    LTSSM_POLLING_COMPLIANCE    = 6'h05,
    LTSSM_POLLING_CONFIGURATION = 6'h06,
    LTSSM_CFG_LINKWIDTH_START   = 6'h08,
    LTSSM_CFG_LINKWIDTH_ACCEPT  = 6'h09,
    LTSSM_CFG_LANENUM_WAIT      = 6'h0A,
    LTSSM_CFG_LANENUM_ACCEPT    = 6'h0B,
    LTSSM_CFG_COMPLETE          = 6'h0C,
    LTSSM_CFG_IDLE              = 6'h0D,
    LTSSM_RECOVERY_RCVRLOCK     = 6'h10,
    LTSSM_RECOVERY_RCVRCFG      = 6'h11,
    LTSSM_RECOVERY_IDLE         = 6'h12,
    LTSSM_DISABLED              = 6'h18,
    LTSSM_HOT_RESET             = 6'h19,
    LTSSM_LOOPBACK_ENTRY        = 6'h1C,
    LTSSM_LOOPBACK_ACTIVE       = 6'h1D,
    LTSSM_LOOPBACK_EXIT         = 6'h1E,
    LTSSM_L0                    = 6'h20,
    LTSSM_L1_ENTRY              = 6'h30,
    LTSSM_L1_IDLE               = 6'h31,
    LTSSM_L2_IDLE               = 6'h34
  } t_ltssm_state;

  // L0s sub-machine codes, the tx and rx fields of an L0 code. Off (the
  // main machine not in L0) has no code of its own.
  typedef enum logic [1:0] {
    L0S_SUB_L0    = 2'd0,
    L0S_SUB_ENTRY = 2'd1,
    L0S_SUB_IDLE  = 2'd2,
    L0S_SUB_FTS   = 2'd3
  } t_l0s_sub;

  // PIPE PowerDown (wiki/src/arch/phy/PIPE.md#signals).
  typedef enum logic [1:0] {
    PD_P0  = 2'd0,
    PD_P0S = 2'd1,
    PD_P1  = 2'd2,
    PD_P2  = 2'd3
  } t_power_down;

  // What the transmit datapath sends while pipe_tx_elec_idle_o is 0
  // (MAS, ordered-set interface). Codes 5 to 7 are reserved for the power
  // and control states.
  typedef enum logic [2:0] {
    TX_NONE       = 3'd0,  // electrical idle: nothing is sent
    TX_TS         = 3'd1,  // TS1 or TS2 with the tx_ts_* fields
    TX_IDLE       = 3'd2,  // idle data symbols
    TX_COMPLIANCE = 3'd3,  // the compliance pattern
    TX_L0         = 3'd4   // L0: packets, idle between them
  } t_tx_mode;


  // Timeouts in clk_i cycles (8 ns), wiki/src/arch/mac/ARCS.md.
  localparam int unsigned TIMER_W             = 23;         // holds 48 ms
  localparam int unsigned DETECT_QUIET_CYCLES = 1_500_000;  // 12 ms, T
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

  // Every register of the LTSSM, reset together (MAS, Microarchitecture).
  typedef struct packed {
    // main machine and its state timer
    t_ltssm_state state;
    t_timer       timer;           // cycles since entry, saturating
    // L0s sub-machines (only meaningful while state is L0)
    t_l0s_sub     tx_sub;
    t_l0s_sub     rx_sub;
    // PIPE control
    logic         reset_n;         // pipe_reset_no
    logic         pcs_ready;       // PhyStatus seen low after reset
    t_power_down  pd;              // pipe_power_down_o
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
  } t_regs;

  localparam t_regs REGS_RESET = '{
    state:         LTSSM_DETECT_QUIET,
    tx_sub:        L0S_SUB_L0,
    rx_sub:        L0S_SUB_L0,
    pd:            PD_P1,
    tx_ei:         1'b1,
    tx_mode:       TX_NONE,
    tx_link_pad:   1'b1,
    tx_lane_pad:   1'b1,
    partner_n_fts: 8'hFF,
    default:       '0
  };

endpackage

`default_nettype wire
