// LTSSM of pcie_mac: the main machine, its timers, the PIPE power,
// detection and polarity control, and the transmit control of the
// ordered-set block (wiki/src/rtl/mac/MAS.md; states and arcs in
// wiki/src/arch/mac/LTSSM.md and ARCS.md).
//
// Core states: Detect, Polling, Configuration (x1, both port roles), L0 and
// Recovery. The power states (L0s sub-machines, L1, L2) and the control
// states (Disabled, Hot Reset, Loopback) have their own blocks below, still
// stubs; their arcs out of the core states are stubbed where the arc list
// places them, so their priority is already fixed.
//
// One step per clk_i cycle, in the order of the architecture model's step
// (src/arch/mac/pcie_mac_arch.py, Mac.step): PIPE status, then the received
// events, then the state's arcs, then the PIPE control and transmit mode
// of the state the step ends in. Every output is a register.
`default_nettype none

`include "common_macros.svh"

module pcie_mac_ltssm #(
  parameter int unsigned TIMEOUT_DIV     = 1,  // 1..1024: divides Detect.Quiet's 12 ms
  parameter int unsigned ERR_TIMEOUT_DIV = 1   // 1..1024: divides error-path timeouts, floored
) (
  input  logic                    clk_i,
  input  logic                    rst_i,          // synchronous, active high
  input  logic                    port_role_i,    // 0 downstream, 1 upstream; static outside reset

  // Link control and status
  output logic                    link_up_o,
  output logic [5:0]              ltssm_state_o,
  input  pcie_mac_pkg::t_link_req req_i,

  // PIPE control and status
  output common_pkg::t_pipe_ctl     pipe_o,
  input  common_pkg::t_pipe_rx      pipe_rx_i,

  // Ordered-set block (mac_os), transmit: one request at a time, a level,
  // taken at os_tx_i.ack; the TS fields with it
  output pcie_mac_pkg::t_os_req   os_req_o,
  output pcie_mac_pkg::t_ts_tx    ts_o,
  input  pcie_mac_pkg::t_os_tx    os_tx_i,

  // Ordered-set block, receive: the events and TS fields of this word
  input  pcie_mac_pkg::t_os_rx    os_rx_i,
  input  pcie_mac_pkg::t_ts_rx    os_ts_i,

  // Frame block (mac_frame): the data source of idle symbols and packets
  output pcie_mac_pkg::t_fr_ctl   fr_o,
  output logic [7:0]              partner_n_fts_o,  // FTS the partner needs (255 until learned)
  input  logic                    fr_tx_idle_i,     // this cycle's line word is two idle symbols
  input  logic [1:0]              fr_rx_idle_i      // symbol i of this word is idle
);

  // ------------------------------------------------------------ parameters

  `COMMON_ASSERT_PARAM(g_bad_timeout_div,
                       TIMEOUT_DIV >= 1 && TIMEOUT_DIV <= pcie_mac_pkg::DIV_MAX,
                       "TIMEOUT_DIV must be in 1..1024")
  `COMMON_ASSERT_PARAM(g_bad_err_timeout_div,
                       ERR_TIMEOUT_DIV >= 1 && ERR_TIMEOUT_DIV <= pcie_mac_pkg::DIV_MAX,
                       "ERR_TIMEOUT_DIV must be in 1..1024")

  // Timeouts in cycles: T is divided by TIMEOUT_DIV, E by ERR_TIMEOUT_DIV
  // with the floor (wiki/src/arch/mac/LTSSM.md#common-rules).
  localparam int unsigned T_QUIET = pcie_mac_pkg::DETECT_QUIET_CYCLES / TIMEOUT_DIV;
  localparam int unsigned T_E2    = pcie_mac_pkg::err_timeout(
                                      pcie_mac_pkg::T2MS_CYCLES, ERR_TIMEOUT_DIV);
  localparam int unsigned T_E24   = pcie_mac_pkg::err_timeout(
                                      pcie_mac_pkg::T24MS_CYCLES, ERR_TIMEOUT_DIV);
  localparam int unsigned T_E48   = pcie_mac_pkg::err_timeout(
                                      pcie_mac_pkg::T48MS_CYCLES, ERR_TIMEOUT_DIV);
  localparam int unsigned TW      = pcie_mac_pkg::TIMER_W;
  localparam int unsigned RW      = pcie_mac_pkg::RUN_W;
  localparam int unsigned AW      = pcie_mac_pkg::AFTER_W;
  localparam int unsigned SW      = pcie_mac_pkg::TS_SENT_W;
  localparam pcie_mac_pkg::t_run   RUN_LONG    = RW'(pcie_mac_pkg::RUN_LONG);
  localparam pcie_mac_pkg::t_run   RUN_SHORT   = RW'(pcie_mac_pkg::RUN_SHORT);
  localparam pcie_mac_pkg::t_after AFTER_COUNT = AW'(pcie_mac_pkg::AFTER_COUNT);
  localparam pcie_mac_pkg::t_ts_sent MIN_TS1   = SW'(pcie_mac_pkg::POLLING_MIN_TS1);
  localparam logic [7:0] IDLE_TO_RLOCK_MAX = 8'(pcie_mac_pkg::IDLE_TO_RLOCK_MAX);

  // ------------------------------------------------------------- registers

  pcie_mac_pkg::t_regs r_q;
  pcie_mac_pkg::t_regs r_d;

  `COMMON_DFF_RST_VAL(r_q, r_d, clk_i, rst_i, pcie_mac_pkg::REGS_RESET)

  common_pkg::t_ltssm_state st;   // the state this step starts in
  logic dsp;                              // downstream role

  assign st  = r_q.state;
  assign dsp = !port_role_i;

  // State groups (wiki/src/arch/mac/LTSSM.md#state-encoding).
  logic in_detect, in_polling, in_cfg, in_l0, in_recovery, in_power, in_control;

  always_comb begin
    in_detect   = 1'b0;
    in_polling  = 1'b0;
    in_cfg      = 1'b0;
    in_l0       = 1'b0;
    in_recovery = 1'b0;
    in_power    = 1'b0;
    in_control  = 1'b0;
    case (st)
      common_pkg::LTSSM_DETECT_QUIET,
      common_pkg::LTSSM_DETECT_ACTIVE:         in_detect   = 1'b1;
      common_pkg::LTSSM_POLLING_ACTIVE,
      common_pkg::LTSSM_POLLING_COMPLIANCE,
      common_pkg::LTSSM_POLLING_CONFIGURATION: in_polling  = 1'b1;
      common_pkg::LTSSM_CFG_LINKWIDTH_START,
      common_pkg::LTSSM_CFG_LINKWIDTH_ACCEPT,
      common_pkg::LTSSM_CFG_LANENUM_WAIT,
      common_pkg::LTSSM_CFG_LANENUM_ACCEPT,
      common_pkg::LTSSM_CFG_COMPLETE,
      common_pkg::LTSSM_CFG_IDLE:              in_cfg      = 1'b1;
      common_pkg::LTSSM_L0:                    in_l0       = 1'b1;
      common_pkg::LTSSM_RECOVERY_RCVRLOCK,
      common_pkg::LTSSM_RECOVERY_RCVRCFG,
      common_pkg::LTSSM_RECOVERY_IDLE:         in_recovery = 1'b1;
      common_pkg::LTSSM_L1_ENTRY,
      common_pkg::LTSSM_L1_IDLE,
      common_pkg::LTSSM_L2_IDLE:               in_power    = 1'b1;
      common_pkg::LTSSM_DISABLED,
      common_pkg::LTSSM_HOT_RESET,
      common_pkg::LTSSM_LOOPBACK_ENTRY,
      common_pkg::LTSSM_LOOPBACK_ACTIVE,
      common_pkg::LTSSM_LOOPBACK_EXIT:         in_control  = 1'b1;
      default: ;
    endcase
  end

  // ------------------------------------------------------- 1. PIPE status

  // PhyStatus is high in PCS reset; its first low marks the PCS ready, and
  // after that each high cycle is a completion pulse: of receiver detection
  // while pipe_o.tx_detect_rx is high, else of a PowerDown change.
  logic phy_pulse;
  logic pcs_ready_c, pd_pending_c, detect_done_c, detect_found_c;

  assign phy_pulse      = r_q.pcs_ready && pipe_rx_i.phy_status;
  assign pcs_ready_c    = r_q.pcs_ready || !pipe_rx_i.phy_status;
  assign pd_pending_c   = r_q.pd_pending && !(phy_pulse && !r_q.detect_rx);
  assign detect_done_c  = r_q.detect_done || (phy_pulse && r_q.detect_rx);
  assign detect_found_c = (phy_pulse && r_q.detect_rx) ? (pipe_rx_i.status == 3'd3)
                                                       : r_q.detect_found;

  // ----------------------------------------------------- 2. receive events

  // A usable TS, and one that breaks every run (failed to decode, or
  // received inverted) (wiki/src/arch/mac/LTSSM.md#common-rules).
  logic ts_any, ts_good, ts_bad, ts1, ts2;   // a TS ended; decoded; breaks the runs
  logic ts_start, tx_busy;           // a TS starts; a started set has words left
  logic rx_parsed;                   // the received word's symbols are parsed
  logic [1:0] rx_idle, rx_break;     // per symbol: idle; restarts the idle run
  logic link_pad, link_num, lane_pad, lane_num, lane_zero, link_is_n;
  logic [7:0] n_link;   // N: 0 downstream, the learned number upstream

  assign ts_any    = os_rx_i.ts || os_rx_i.ts_bad;
  assign ts_good   = os_rx_i.ts && !os_ts_i.ts_inv;
  assign ts_bad    = os_rx_i.ts_bad || (os_rx_i.ts && os_ts_i.ts_inv);
  assign ts2       = os_ts_i.ts2;
  assign ts1       = !os_ts_i.ts2;
  assign link_pad  = os_ts_i.link_pad;
  assign link_num  = os_ts_i.link_num;       // any K other than PAD matches no number
  assign lane_pad  = os_ts_i.lane_pad;
  assign lane_num  = os_ts_i.lane_num;
  assign lane_zero = lane_num && (os_ts_i.lane == 8'd0);
  assign n_link    = dsp ? 8'd0 : r_q.rx_link;

  // Transmit events: a TS starts when the block acknowledges this LTSSM's
  // TS request; a set is unfinished until its last word.
  assign ts_start = os_tx_i.ack && r_q.tx_mode == pcie_mac_pkg::TX_TS;
  assign tx_busy  = os_tx_i.valid && !os_tx_i.last;

  // Idle symbols: parsed, outside an ordered set; any other parsed symbol
  // outside an ordered set (a K symbol, other data) restarts the run.
  assign rx_parsed = pipe_rx_i.valid && !pipe_rx_i.elec_idle;
  assign rx_idle   = {2{rx_parsed}} & fr_rx_idle_i;
  assign rx_break  = {2{rx_parsed}} & ~os_rx_i.os_sym & ~fr_rx_idle_i;
  assign link_is_n = link_num && (os_ts_i.link == n_link);

  // What a received TS matches in the current state (the arcs' conditions).
  // go: the advancing run; pad: the PAD exit (Recovery.Idle: lane PAD);
  // renum: Lanenum.Accept's renumbering; mism: RcvrCfg's mismatching TS1.
  logic m_go, m_pad, m_renum, m_mism;

  always_comb begin
    m_go    = 1'b0;
    m_pad   = 1'b0;
    m_renum = 1'b0;
    m_mism  = 1'b0;
    case (st)
      common_pkg::LTSSM_POLLING_ACTIVE:
        m_go = link_pad && lane_pad;
      common_pkg::LTSSM_POLLING_CONFIGURATION:
        m_go = ts2 && link_pad && lane_pad;
      common_pkg::LTSSM_CFG_LINKWIDTH_START:
        m_go = ts1 && lane_pad && (dsp ? link_is_n : link_num);
      common_pkg::LTSSM_CFG_LINKWIDTH_ACCEPT: begin
        m_go  = ts1 && link_is_n && (dsp ? lane_pad : lane_zero);
        m_pad = ts1 && link_pad && lane_pad;
      end
      common_pkg::LTSSM_CFG_LANENUM_WAIT: begin
        m_go  = link_is_n && lane_zero;
        m_pad = ts1 && link_pad;
      end
      common_pkg::LTSSM_CFG_LANENUM_ACCEPT: begin
        m_go    = link_is_n && lane_zero;
        m_pad   = ts1 && link_pad;
        m_renum = ts1 && link_is_n && lane_num && (os_ts_i.lane != 8'd0);
      end
      common_pkg::LTSSM_CFG_COMPLETE:
        m_go = ts2 && link_is_n && lane_zero;
      common_pkg::LTSSM_RECOVERY_RCVRLOCK:
        m_go = link_is_n && lane_zero;
      common_pkg::LTSSM_RECOVERY_RCVRCFG: begin
        m_go   = ts2 && link_is_n && lane_zero;
        m_mism = ts1 && !(link_is_n && lane_zero);
      end
      common_pkg::LTSSM_RECOVERY_IDLE:
        m_pad = ts1 && lane_pad;
      default: ;
    endcase
  end

  // Counts after this step's events: runs of consecutive matching TS (a TS
  // that does not match, or breaks, restarts them), the latched 8-runs, TS
  // and idle symbols sent after the first matching one received.
  pcie_mac_pkg::t_run     run_go_c, run_pad_c, run_renum_c, run_mism_c, idle_run_c;
  pcie_mac_pkg::t_after   after_c, mism_after_c, idle_after_c;
  pcie_mac_pkg::t_ts_sent ts_sent_c;
  logic got8_c, mism8_c, after_on_c, mism_after_on_c, idle8_c, idle_after_on_c;
  logic ts_rx_any_c, left_ei_c, eios_rx_c, retrain_c, polarity_c;
  logic [7:0] rx_link_c, partner_n_fts_c;
  logic [1:0] idle_sent;

  always_comb begin
    run_go_c    = pcie_mac_pkg::run_next(r_q.run_go,    ts_good, ts_bad, m_go);
    run_pad_c   = pcie_mac_pkg::run_next(r_q.run_pad,   ts_good, ts_bad, m_pad);
    run_renum_c = pcie_mac_pkg::run_next(r_q.run_renum, ts_good, ts_bad, m_renum);
    run_mism_c  = pcie_mac_pkg::run_next(r_q.run_mism,  ts_good, ts_bad, m_mism);
    got8_c      = r_q.got8  || (run_go_c   == RUN_LONG);
    mism8_c     = r_q.mism8 || (run_mism_c == RUN_LONG);
    // The TS sent in the previous cycle count only if the first matching TS
    // was received before it.
    after_c         = r_q.after_on
                    ? pcie_mac_pkg::after_next(r_q.after, {1'b0, ts_start}) : '0;
    after_on_c      = r_q.after_on || (ts_good && m_go);
    mism_after_c    = r_q.mism_after_on
                    ? pcie_mac_pkg::after_next(r_q.mism_after, {1'b0, ts_start}) : '0;
    mism_after_on_c = r_q.mism_after_on || (ts_good && m_mism);
    ts_sent_c   = (r_q.ts_sent == MIN_TS1) ? r_q.ts_sent
                                           : r_q.ts_sent + SW'(ts_start);
    ts_rx_any_c = r_q.ts_rx_any || ts_any;

    // Idle symbols, [7:0] first; a TS ending in this word restarts the run
    // before them.
    idle_run_c = ts_any ? '0 : r_q.idle_run;
    idle8_c    = r_q.idle8;
    for (int unsigned i = 0; i < 2; i++) begin
      if (rx_break[i]) begin
        idle_run_c = '0;
      end else if (rx_idle[i] && idle_run_c != RUN_LONG) begin
        idle_run_c = idle_run_c + 1'b1;
      end
      if (idle_run_c == RUN_LONG) begin
        idle8_c = 1'b1;
      end
    end
    idle_sent       = fr_tx_idle_i ? 2'd2 : 2'd0;
    idle_after_c    = r_q.idle_after_on
                    ? pcie_mac_pkg::after_next(r_q.idle_after, idle_sent) : '0;
    idle_after_on_c = r_q.idle_after_on || (|rx_idle);

    left_ei_c = r_q.left_ei || !pipe_rx_i.elec_idle;
    eios_rx_c = r_q.eios_rx || os_rx_i.eios;
    retrain_c = r_q.retrain || (in_l0 && req_i.retrain);

    // Polarity: an inverted TS in Polling sets it, once per training.
    polarity_c = r_q.polarity
              || (os_rx_i.ts && os_ts_i.ts_inv
                  && (st == common_pkg::LTSSM_POLLING_ACTIVE
                      || st == common_pkg::LTSSM_POLLING_CONFIGURATION));

    // Upstream: N is the link number of the last TS1 with one received in
    // Configuration.Linkwidth.Start.
    rx_link_c = r_q.rx_link;
    if (!dsp && st == common_pkg::LTSSM_CFG_LINKWIDTH_START && ts_good && ts1
        && link_num) begin
      rx_link_c = os_ts_i.link;
    end

    // The partner's N_FTS, from the TS2 that complete Configuration.Complete
    // and Recovery.RcvrCfg.
    partner_n_fts_c = r_q.partner_n_fts;
    if (ts_good && m_go && ts2 && (st == common_pkg::LTSSM_CFG_COMPLETE
                                   || st == common_pkg::LTSSM_RECOVERY_RCVRCFG)) begin
      partner_n_fts_c = os_ts_i.n_fts;
    end
  end

  // ---------------------------------------------------------- 3. the arcs

  // The state's timeout: the cycle count since entry reaches it this step.
  logic [TW-1:0] limit;
  logic          timeout;
  logic          timed_exit;   // the timeout always leaves this state

  always_comb begin
    limit      = '1;
    timed_exit = 1'b1;
    case (st)
      common_pkg::LTSSM_DETECT_QUIET: begin
        limit      = TW'(T_QUIET);
        timed_exit = 1'b0;               // also waits for the PCS
      end
      common_pkg::LTSSM_POLLING_ACTIVE,
      common_pkg::LTSSM_CFG_LINKWIDTH_START,
      common_pkg::LTSSM_RECOVERY_RCVRLOCK:    limit = TW'(T_E24);
      common_pkg::LTSSM_POLLING_CONFIGURATION,
      common_pkg::LTSSM_RECOVERY_RCVRCFG:     limit = TW'(T_E48);
      common_pkg::LTSSM_CFG_LINKWIDTH_ACCEPT,
      common_pkg::LTSSM_CFG_LANENUM_WAIT,
      common_pkg::LTSSM_CFG_LANENUM_ACCEPT,
      common_pkg::LTSSM_CFG_COMPLETE,
      common_pkg::LTSSM_CFG_IDLE,
      common_pkg::LTSSM_RECOVERY_IDLE:        limit = TW'(T_E2);
      default:                                        timed_exit = 1'b0;
    endcase
  end

  assign timeout = (r_q.timer >= limit - 1'b1);

  // Each group proposes its arc; the arcs page's row order is the priority
  // inside a state. go: an arc is taken; to: its target.
  logic det_go, pol_go, cfg_go, l0_go, rec_go, pwr_go, ctl_go;
  common_pkg::t_ltssm_state det_to, pol_to, cfg_to, l0_to, rec_to, pwr_to, ctl_to;
  logic cfg_rlock_inc, rec_rlock_inc;   // an Idle timeout increments idle_to_rlock

  // Detect (wiki/src/arch/mac/ARCS.md#detect-and-polling).
  always_comb begin
    det_go = 1'b0;
    det_to = common_pkg::LTSSM_DETECT_QUIET;
    case (st)
      common_pkg::LTSSM_DETECT_QUIET:
        if (pcs_ready_c && !pd_pending_c && r_q.pd == common_pkg::PD_P1
            && (timeout || !pipe_rx_i.elec_idle)) begin
          det_go = 1'b1;
          det_to = common_pkg::LTSSM_DETECT_ACTIVE;
        end
      common_pkg::LTSSM_DETECT_ACTIVE:
        if (detect_done_c && !r_q.detect_rx) begin
          det_go = 1'b1;
          det_to = detect_found_c ? common_pkg::LTSSM_POLLING_ACTIVE
                                  : common_pkg::LTSSM_DETECT_QUIET;
        end
      default: ;
    endcase
  end

  // Polling (wiki/src/arch/mac/ARCS.md#detect-and-polling).
  always_comb begin
    pol_go = 1'b0;
    pol_to = common_pkg::LTSSM_DETECT_QUIET;
    case (st)
      common_pkg::LTSSM_POLLING_ACTIVE:
        if (ts_sent_c == MIN_TS1 && run_go_c == RUN_LONG) begin
          pol_go = 1'b1;
          pol_to = common_pkg::LTSSM_POLLING_CONFIGURATION;
        end else if (timeout) begin
          pol_go = 1'b1;
          pol_to = left_ei_c ? common_pkg::LTSSM_DETECT_QUIET
                             : common_pkg::LTSSM_POLLING_COMPLIANCE;
        end
      common_pkg::LTSSM_POLLING_COMPLIANCE:
        if (!pipe_rx_i.elec_idle) begin
          pol_go = 1'b1;
          pol_to = common_pkg::LTSSM_POLLING_ACTIVE;
        end
      common_pkg::LTSSM_POLLING_CONFIGURATION:
        if (got8_c && after_c == AFTER_COUNT) begin
          pol_go = 1'b1;
          pol_to = common_pkg::LTSSM_CFG_LINKWIDTH_START;
        end else if (timeout) begin
          pol_go = 1'b1;
        end
      default: ;
    endcase
  end

  // Configuration, x1 (wiki/src/arch/mac/ARCS.md#configuration).
  // Stubbed: Linkwidth.Start to Loopback.Entry and to Disabled (control
  // states), listed first as on the arcs page.
  logic stub_lws_loopback, stub_lws_disabled;

  assign stub_lws_loopback = 1'b0;
  assign stub_lws_disabled = 1'b0;

  always_comb begin
    cfg_go        = 1'b0;
    cfg_to        = common_pkg::LTSSM_DETECT_QUIET;
    cfg_rlock_inc = 1'b0;
    case (st)
      common_pkg::LTSSM_CFG_LINKWIDTH_START:
        if (stub_lws_loopback) begin
          cfg_go = 1'b1;
          cfg_to = common_pkg::LTSSM_LOOPBACK_ENTRY;
        end else if (stub_lws_disabled) begin
          cfg_go = 1'b1;
          cfg_to = common_pkg::LTSSM_DISABLED;
        end else if (run_go_c >= RUN_SHORT) begin
          cfg_go = 1'b1;
          cfg_to = common_pkg::LTSSM_CFG_LINKWIDTH_ACCEPT;
        end else if (timeout) begin
          cfg_go = 1'b1;
        end
      common_pkg::LTSSM_CFG_LINKWIDTH_ACCEPT:
        if (run_go_c >= RUN_SHORT) begin
          cfg_go = 1'b1;
          cfg_to = common_pkg::LTSSM_CFG_LANENUM_WAIT;
        end else if (run_pad_c >= RUN_SHORT || timeout) begin
          cfg_go = 1'b1;
        end
      common_pkg::LTSSM_CFG_LANENUM_WAIT:
        if (run_go_c >= RUN_SHORT) begin
          cfg_go = 1'b1;
          cfg_to = common_pkg::LTSSM_CFG_LANENUM_ACCEPT;
        end else if (run_pad_c >= RUN_SHORT || timeout) begin
          cfg_go = 1'b1;
        end
      common_pkg::LTSSM_CFG_LANENUM_ACCEPT:
        if (run_go_c >= RUN_SHORT) begin
          cfg_go = 1'b1;
          cfg_to = common_pkg::LTSSM_CFG_COMPLETE;
        end else if (run_renum_c >= RUN_SHORT) begin
          cfg_go = 1'b1;
          cfg_to = common_pkg::LTSSM_CFG_LANENUM_WAIT;
        end else if (run_pad_c >= RUN_SHORT || timeout) begin
          cfg_go = 1'b1;
        end
      common_pkg::LTSSM_CFG_COMPLETE:
        if (got8_c && after_c == AFTER_COUNT) begin
          cfg_go = 1'b1;
          cfg_to = common_pkg::LTSSM_CFG_IDLE;
        end else if (timeout) begin
          cfg_go = 1'b1;
        end
      common_pkg::LTSSM_CFG_IDLE:
        if (idle8_c && idle_after_c == AFTER_COUNT) begin
          cfg_go = 1'b1;
          cfg_to = common_pkg::LTSSM_L0;
        end else if (timeout) begin
          cfg_go = 1'b1;
          if (r_q.idle_to_rlock != IDLE_TO_RLOCK_MAX) begin
            cfg_to        = common_pkg::LTSSM_RECOVERY_RCVRLOCK;
            cfg_rlock_inc = 1'b1;
          end
        end
      default: ;
    endcase
  end

  // L0, main machine (wiki/src/arch/mac/ARCS.md#l0). A main exit waits for
  // the transmit sub-machine in Tx_L0 and the receive one out of Entry and
  // FTS. Stubbed: L0 to L1.Entry and L2.Idle (power states).
  logic l0_exit_ok, l0_to_rlock, stub_l0_l1, stub_l0_l2;

  assign stub_l0_l1  = 1'b0;
  assign stub_l0_l2  = 1'b0;
  assign l0_exit_ok  = r_q.tx_sub == common_pkg::L0S_SUB_L0
                    && r_q.rx_sub != common_pkg::L0S_SUB_ENTRY
                    && r_q.rx_sub != common_pkg::L0S_SUB_FTS;
  assign l0_to_rlock = (r_q.rx_sub == common_pkg::L0S_SUB_L0
                        && (ts_any || (pipe_rx_i.elec_idle && !eios_rx_c)))
                    || (dsp && (req_i.hot_reset || req_i.disable_link))
                    || req_i.loopback || retrain_c;

  always_comb begin
    l0_go = 1'b0;
    l0_to = common_pkg::LTSSM_RECOVERY_RCVRLOCK;
    if (in_l0 && l0_exit_ok) begin
      if (l0_to_rlock) begin
        l0_go = 1'b1;
      end else if (stub_l0_l1) begin
        l0_go = 1'b1;
        l0_to = common_pkg::LTSSM_L1_ENTRY;
      end else if (stub_l0_l2) begin
        l0_go = 1'b1;
        l0_to = common_pkg::LTSSM_L2_IDLE;
      end
    end
  end

  // Recovery (wiki/src/arch/mac/ARCS.md#recovery). Stubbed: Recovery.Idle
  // to Hot Reset, Disabled and Loopback.Entry (control states), first in
  // priority as on the arcs page.
  logic stub_ri_hot_reset, stub_ri_disabled, stub_ri_loopback;

  assign stub_ri_hot_reset = 1'b0;
  assign stub_ri_disabled  = 1'b0;
  assign stub_ri_loopback  = 1'b0;

  always_comb begin
    rec_go        = 1'b0;
    rec_to        = common_pkg::LTSSM_DETECT_QUIET;
    rec_rlock_inc = 1'b0;
    case (st)
      common_pkg::LTSSM_RECOVERY_RCVRLOCK:
        if (run_go_c == RUN_LONG) begin
          rec_go = 1'b1;
          rec_to = common_pkg::LTSSM_RECOVERY_RCVRCFG;
        end else if (timeout) begin
          rec_go = 1'b1;
          if (ts_rx_any_c) begin
            rec_to = common_pkg::LTSSM_CFG_LINKWIDTH_START;
          end
        end
      common_pkg::LTSSM_RECOVERY_RCVRCFG:
        if (got8_c && after_c == AFTER_COUNT) begin
          rec_go = 1'b1;
          rec_to = common_pkg::LTSSM_RECOVERY_IDLE;
        end else if (mism8_c && mism_after_c == AFTER_COUNT) begin
          rec_go = 1'b1;
          rec_to = common_pkg::LTSSM_CFG_LINKWIDTH_START;
        end else if (timeout) begin
          rec_go = 1'b1;
        end
      common_pkg::LTSSM_RECOVERY_IDLE:
        if (stub_ri_hot_reset) begin
          rec_go = 1'b1;
          rec_to = common_pkg::LTSSM_HOT_RESET;
        end else if (stub_ri_disabled) begin
          rec_go = 1'b1;
          rec_to = common_pkg::LTSSM_DISABLED;
        end else if (stub_ri_loopback) begin
          rec_go = 1'b1;
          rec_to = common_pkg::LTSSM_LOOPBACK_ENTRY;
        end else if (run_pad_c >= RUN_SHORT) begin
          rec_go = 1'b1;
          rec_to = common_pkg::LTSSM_CFG_LINKWIDTH_START;
        end else if (idle8_c && idle_after_c == AFTER_COUNT) begin
          rec_go = 1'b1;
          rec_to = common_pkg::LTSSM_L0;
        end else if (timeout) begin
          rec_go = 1'b1;
          if (r_q.idle_to_rlock != IDLE_TO_RLOCK_MAX) begin
            rec_to        = common_pkg::LTSSM_RECOVERY_RCVRLOCK;
            rec_rlock_inc = 1'b1;
          end
        end
      default: ;
    endcase
  end

  // Power states, L1 and L2 (wiki/src/arch/mac/ARCS.md#l1-and-l2): stub,
  // never entered in the core build.
  always_comb begin
    pwr_go = 1'b0;
    pwr_to = st;
  end

  // Control states, Disabled, Hot Reset and Loopback
  // (wiki/src/arch/mac/ARCS.md#control-states): stub, never entered in the
  // core build.
  always_comb begin
    ctl_go = 1'b0;
    ctl_to = st;
  end

  // The arc of this step.
  logic                             go, rlock_inc;
  common_pkg::t_ltssm_state nxt;

  always_comb begin
    go        = 1'b0;
    nxt       = st;
    rlock_inc = 1'b0;
    if (in_detect) begin
      go  = det_go;
      nxt = det_to;
    end else if (in_polling) begin
      go  = pol_go;
      nxt = pol_to;
    end else if (in_cfg) begin
      go        = cfg_go;
      nxt       = cfg_to;
      rlock_inc = cfg_rlock_inc;
    end else if (in_l0) begin
      go  = l0_go;
      nxt = l0_to;
    end else if (in_recovery) begin
      go        = rec_go;
      nxt       = rec_to;
      rlock_inc = rec_rlock_inc;
    end else if (in_power) begin
      go  = pwr_go;
      nxt = pwr_to;
    end else if (in_control) begin
      go  = ctl_go;
      nxt = ctl_to;
    end
    if (!go) begin
      nxt = st;
    end
  end

  // ------------------------------------- 4. next registers and PIPE control

  // What the state this step ends in sends, and its PowerDown
  // (wiki/src/arch/mac/LTSSM.md#what-each-state-transmits).
  pcie_mac_pkg::t_tx_mode    mode;
  common_pkg::t_power_down want_pd;
  logic                            active;

  always_comb begin
    mode    = pcie_mac_pkg::TX_TS;
    want_pd = common_pkg::PD_P0;
    case (nxt)
      common_pkg::LTSSM_DETECT_QUIET,
      common_pkg::LTSSM_DETECT_ACTIVE: begin
        mode    = pcie_mac_pkg::TX_NONE;
        want_pd = common_pkg::PD_P1;
      end
      common_pkg::LTSSM_POLLING_COMPLIANCE: mode = pcie_mac_pkg::TX_COMPLIANCE;
      common_pkg::LTSSM_CFG_IDLE,
      common_pkg::LTSSM_RECOVERY_IDLE:      mode = pcie_mac_pkg::TX_IDLE;
      common_pkg::LTSSM_L0:                 mode = pcie_mac_pkg::TX_L0;
      common_pkg::LTSSM_POLLING_ACTIVE,
      common_pkg::LTSSM_POLLING_CONFIGURATION,
      common_pkg::LTSSM_CFG_LINKWIDTH_START,
      common_pkg::LTSSM_CFG_LINKWIDTH_ACCEPT,
      common_pkg::LTSSM_CFG_LANENUM_WAIT,
      common_pkg::LTSSM_CFG_LANENUM_ACCEPT,
      common_pkg::LTSSM_CFG_COMPLETE,
      common_pkg::LTSSM_RECOVERY_RCVRLOCK,
      common_pkg::LTSSM_RECOVERY_RCVRCFG:   mode = pcie_mac_pkg::TX_TS;
      default:                                      mode = pcie_mac_pkg::TX_NONE;
    endcase
  end

  // The transmitter sends in P0 with no PowerDown change pending: what the
  // state sends, or the rest of an ordered set already started (a set is
  // finished before electrical idle, with no request made meanwhile).
  assign active = (mode != pcie_mac_pkg::TX_NONE || tx_busy)
               && r_q.pd == common_pkg::PD_P0 && !pd_pending_c;

  always_comb begin
    r_d = r_q;

    // Counts, with this step's events.
    r_d.timer         = (r_q.timer == '1) ? r_q.timer : r_q.timer + 1'b1;
    r_d.pcs_ready     = pcs_ready_c;
    r_d.detect_done   = detect_done_c;
    r_d.detect_found  = detect_found_c;
    r_d.polarity      = polarity_c;
    r_d.rx_link       = rx_link_c;
    r_d.partner_n_fts = partner_n_fts_c;
    r_d.ts_sent       = ts_sent_c;
    r_d.run_go        = run_go_c;
    r_d.run_pad       = run_pad_c;
    r_d.run_renum     = run_renum_c;
    r_d.run_mism      = run_mism_c;
    r_d.got8          = got8_c;
    r_d.mism8         = mism8_c;
    r_d.after_on      = after_on_c;
    r_d.after         = after_c;
    r_d.mism_after_on = mism_after_on_c;
    r_d.mism_after    = mism_after_c;
    r_d.ts_rx_any     = ts_rx_any_c;
    r_d.idle_run      = idle_run_c;
    r_d.idle8         = idle8_c;
    r_d.idle_after_on = idle_after_on_c;
    r_d.idle_after    = idle_after_c;
    r_d.left_ei       = left_ei_c;
    r_d.eios_rx       = eios_rx_c;
    r_d.retrain       = retrain_c;
    r_d.reset_n       = 1'b1;

    // State entry: the timer and every per-state count restart.
    if (go) begin
      r_d.state         = nxt;
      r_d.timer         = '0;
      r_d.ts_sent       = '0;
      r_d.run_go        = '0;
      r_d.run_pad       = '0;
      r_d.run_renum     = '0;
      r_d.run_mism      = '0;
      r_d.got8          = 1'b0;
      r_d.mism8         = 1'b0;
      r_d.after_on      = 1'b0;
      r_d.after         = '0;
      r_d.mism_after_on = 1'b0;
      r_d.mism_after    = '0;
      r_d.ts_rx_any     = 1'b0;
      r_d.idle_run      = '0;
      r_d.idle8         = 1'b0;
      r_d.idle_after_on = 1'b0;
      r_d.idle_after    = '0;
      r_d.left_ei       = 1'b0;
      r_d.eios_rx       = 1'b0;
      r_d.retrain       = 1'b0;
      if (rlock_inc) begin
        r_d.idle_to_rlock = r_q.idle_to_rlock + 8'd1;
      end
      case (nxt)
        common_pkg::LTSSM_DETECT_QUIET: begin
          r_d.link_up  = 1'b0;
          r_d.rx_link  = '0;
          r_d.polarity = 1'b0;
        end
        common_pkg::LTSSM_DETECT_ACTIVE: begin
          r_d.detect_done  = 1'b0;
          r_d.detect_found = 1'b0;
        end
        common_pkg::LTSSM_POLLING_ACTIVE: r_d.link_up = 1'b0;
        common_pkg::LTSSM_L0: begin
          r_d.link_up       = 1'b1;
          r_d.idle_to_rlock = '0;
          r_d.tx_sub        = common_pkg::L0S_SUB_L0;
          r_d.rx_sub        = common_pkg::L0S_SUB_L0;
        end
        default: ;
      endcase
    end

    // PIPE control: one PowerDown change at a time, only with the
    // transmitter already in electrical idle; receiver detection in P1.
    r_d.pd_pending = pd_pending_c;
    r_d.tx_mode    = pcie_mac_pkg::TX_NONE;
    if (!active) begin
      if (r_q.pd != want_pd && !pd_pending_c && r_q.tx_ei) begin
        r_d.pd         = want_pd;
        r_d.pd_pending = 1'b1;
      end
      r_d.tx_ei = 1'b1;
      if (r_d.state == common_pkg::LTSSM_DETECT_ACTIVE
          && r_d.pd == common_pkg::PD_P1 && !r_d.pd_pending) begin
        r_d.detect_rx = !r_d.detect_done;
      end else if (r_q.detect_rx
                   && r_d.state != common_pkg::LTSSM_DETECT_ACTIVE) begin
        r_d.detect_rx = 1'b0;
      end
    end else begin
      r_d.tx_ei   = 1'b0;
      r_d.tx_mode = mode;
    end

    // TS fields of the state this step ends in.
    r_d.tx_ts2      = nxt == common_pkg::LTSSM_POLLING_CONFIGURATION
                   || nxt == common_pkg::LTSSM_CFG_COMPLETE
                   || nxt == common_pkg::LTSSM_RECOVERY_RCVRCFG;
    r_d.tx_link     = dsp ? 8'd0 : r_d.rx_link;
    r_d.tx_link_pad = nxt == common_pkg::LTSSM_POLLING_ACTIVE
                   || nxt == common_pkg::LTSSM_POLLING_CONFIGURATION
                   || (nxt == common_pkg::LTSSM_CFG_LINKWIDTH_START && !dsp);
    r_d.tx_lane_pad = nxt == common_pkg::LTSSM_POLLING_ACTIVE
                   || nxt == common_pkg::LTSSM_POLLING_CONFIGURATION
                   || nxt == common_pkg::LTSSM_CFG_LINKWIDTH_START
                   || nxt == common_pkg::LTSSM_CFG_LINKWIDTH_ACCEPT;
  end

  // ---------------------------------------------------------------- outputs

  assign link_up_o           = r_q.link_up;
  assign ltssm_state_o       = in_l0 ? {common_pkg::LTSSM_L0[5:4], r_q.tx_sub, r_q.rx_sub}
                                     : r_q.state;
  assign pipe_o.reset_n       = r_q.reset_n;
  assign pipe_o.tx_elec_idle = r_q.tx_ei;
  assign pipe_o.tx_detect_rx = r_q.detect_rx;
  assign pipe_o.rx_polarity  = r_q.polarity;
  assign pipe_o.power_down   = r_q.pd;
  assign os_req_o.ts         = r_q.tx_mode == pcie_mac_pkg::TX_TS;
  assign os_req_o.compliance = r_q.tx_mode == pcie_mac_pkg::TX_COMPLIANCE;
  assign fr_o.tx_l0             = r_q.tx_mode == pcie_mac_pkg::TX_IDLE
                            || r_q.tx_mode == pcie_mac_pkg::TX_L0;
  assign fr_o.tx_stop           = r_q.tx_mode != pcie_mac_pkg::TX_L0;
  assign fr_o.rx_l0             = in_l0 && r_q.rx_sub == common_pkg::L0S_SUB_L0;
  assign ts_o.ts2            = r_q.tx_ts2;
  assign ts_o.link_pad       = r_q.tx_link_pad;
  assign ts_o.link           = r_q.tx_link;
  assign ts_o.lane_pad       = r_q.tx_lane_pad;
  assign ts_o.lane           = 8'd0;          // x1: the lane number is always 0
  assign ts_o.ctl            = '0;            // control bits: control states only
  assign partner_n_fts_o     = r_q.partner_n_fts;
  assign os_req_o.skp        = 1'b0;           // requested SKP, EIOS, FTS: power states
  assign os_req_o.eios       = 1'b0;
  assign os_req_o.fts        = 1'b0;

  // Inputs the core states do not use yet: the power states read
  // req_i.pm_state, SKP and FTS, the control states the control bits.
  `COMMON_UNUSED_BITS(unused_core, {req_i.pm_state, os_ts_i.ctl, os_rx_i.skp, os_rx_i.fts,
                                    os_tx_i.compliance})

  // ------------------------------------------------------------- invariants

  // The state is one of the encoding's, in exactly one group, and never a
  // power or control state while those are stubs.
  `COMMON_ASSERT_KNOWN(a_state_known, clk_i, rst_i, r_q.state)
  `COMMON_ASSERT(a_state_one_group, clk_i, rst_i,
                 $onehot({in_detect, in_polling, in_cfg, in_l0, in_recovery, in_power,
                          in_control}))
  `COMMON_ASSERT_NEVER(a_no_stub_state, clk_i, rst_i, in_power || in_control)
  // The sub-machines stay in Tx_L0 and Rx_L0 until the power states arrive.
  `COMMON_ASSERT_NEVER(a_l0s_core_only, clk_i, rst_i,
                       in_l0 && (r_q.tx_sub != common_pkg::L0S_SUB_L0
                                 || r_q.rx_sub != common_pkg::L0S_SUB_L0))

  // Detect starts nothing: Detect.Quiet only finishes a unit already
  // started, Detect.Active (P1) sends nothing; the transmit mode is NONE in
  // electrical idle; data only in P0 with no change pending.
  `COMMON_ASSERT_NEVER(a_no_tx_in_detect, clk_i, rst_i,
                       in_detect && r_q.tx_mode != pcie_mac_pkg::TX_NONE)
  `COMMON_ASSERT_NEVER(a_no_tx_in_detect_active, clk_i, rst_i,
                       st == common_pkg::LTSSM_DETECT_ACTIVE && !r_q.tx_ei)
  `COMMON_ASSERT_NEVER(a_mode_none_in_ei, clk_i, rst_i,
                       r_q.tx_ei && r_q.tx_mode != pcie_mac_pkg::TX_NONE)
  `COMMON_ASSERT_NEVER(a_tx_only_in_p0, clk_i, rst_i,
                       !r_q.tx_ei && (r_q.pd != common_pkg::PD_P0 || r_q.pd_pending))
  `COMMON_ASSERT_NEVER(a_compliance_in_pc, clk_i, rst_i,
                       r_q.tx_mode == pcie_mac_pkg::TX_COMPLIANCE
                       && st != common_pkg::LTSSM_POLLING_COMPLIANCE)

  // One PowerDown change at a time, made in electrical idle; receiver
  // detection only in Detect.Active, in P1, in electrical idle.
  `COMMON_ASSERT_NEVER(a_pd_one_change, clk_i, rst_i, r_d.pd != r_q.pd && pd_pending_c)
  `COMMON_ASSERT_NEVER(a_pd_change_in_ei, clk_i, rst_i, r_d.pd != r_q.pd && !r_q.tx_ei)
  `COMMON_ASSERT_NEVER(a_pd_wait_pcs, clk_i, rst_i, r_d.pd != r_q.pd && !r_q.pcs_ready)
  `COMMON_ASSERT_NEVER(a_detect_rx_p1_ei, clk_i, rst_i,
                       r_q.detect_rx && (r_q.pd != common_pkg::PD_P1 || !r_q.tx_ei
                                         || st != common_pkg::LTSSM_DETECT_ACTIVE))

  // Polarity changes only in Polling, or back to 0 on entry to Detect.Quiet.
  `COMMON_ASSERT_NEVER(a_polarity_in_polling, clk_i, rst_i,
                       r_d.polarity != r_q.polarity && !in_polling
                       && !(go && nxt == common_pkg::LTSSM_DETECT_QUIET))

  // The link is down in Detect and Polling.
  `COMMON_ASSERT_NEVER(a_link_down_in_training, clk_i, rst_i,
                       r_q.link_up && (in_detect || in_polling))

  // Counters stay in range: runs to 8, counts after the first to 16, TS
  // sent to 1024, and idle_to_rlock never wraps.
  `COMMON_ASSERT_NEVER(a_runs_in_range, clk_i, rst_i,
                       r_q.run_go > RUN_LONG || r_q.run_pad > RUN_LONG
                       || r_q.run_renum > RUN_LONG || r_q.run_mism > RUN_LONG
                       || r_q.idle_run > RUN_LONG)
  `COMMON_ASSERT_NEVER(a_after_in_range, clk_i, rst_i,
                       r_q.after > AFTER_COUNT || r_q.mism_after > AFTER_COUNT
                       || r_q.idle_after > AFTER_COUNT)
  `COMMON_ASSERT_NEVER(a_ts_sent_in_range, clk_i, rst_i, r_q.ts_sent > MIN_TS1)
  `COMMON_ASSERT_NEVER(a_idle_to_rlock_no_wrap, clk_i, rst_i,
                       rlock_inc && r_q.idle_to_rlock == IDLE_TO_RLOCK_MAX)
  // A state whose timeout always exits never outlives it.
  `COMMON_ASSERT_NEVER(a_timeout_exits, clk_i, rst_i, timed_exit && r_q.timer >= limit)

endmodule

`default_nettype wire
