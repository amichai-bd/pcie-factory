// Types, constants and the receive-parser step of pcie_mac_os
// (wiki/src/rtl/mac_os/MAS.md). Symbol bytes and TS fields follow
// wiki/src/arch/mac/SYMBOLS.md.
`default_nettype none

package pcie_mac_os_pkg;

  // Symbol bytes (K flag separate).
  localparam logic [7:0] SYM_COM = 8'hBC;   // K28.5
  localparam logic [7:0] SYM_PAD = 8'hF7;   // K23.7
  localparam logic [7:0] SYM_SKP = 8'h1C;   // K28.0
  localparam logic [7:0] SYM_FTS = 8'h3C;   // K28.1
  localparam logic [7:0] SYM_IDL = 8'h7C;   // K28.3

  // TS identifiers, and their bit-inverses (received with inverted polarity).
  localparam logic [7:0] TS1_ID     = 8'h4A;  // D10.2
  localparam logic [7:0] TS2_ID     = 8'h45;  // D5.2
  localparam logic [7:0] TS1_ID_INV = 8'hB5;
  localparam logic [7:0] TS2_ID_INV = 8'hBA;
  localparam logic [7:0] RATE_GEN1  = 8'h02;  // 2.5 GT/s only

  // Compliance pattern data symbols: COM D21.5 COM D10.2.
  localparam logic [7:0] COMPL_D21_5 = 8'hB5;
  localparam logic [7:0] COMPL_D10_2 = 8'h4A;

  // Training control bits.
  localparam int unsigned TX_CTL_W = 3;  // Hot Reset, Disable Link, Loopback
  localparam int unsigned RX_CTL_W = 5;  // ... Disable Scrambling, Compliance Receive

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
    KIND_COMPL = 3'd4
  } t_tx_kind;

  // Receive parser phases (MAS, Receive).
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
    return (b == TS1_ID) || (b == TS2_ID) || (b == TS1_ID_INV) || (b == TS2_ID_INV);
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
    if (k && (b == SYM_COM)) begin
      r.ev.os_sym = 1'b1;
      r.ev.ts_bad = (st.phase == RX_TS);
      r.st.phase  = RX_COM;
    end else begin
      case (st.phase)
        RX_COM: begin
          r.ev.os_sym = 1'b1;
          if (k && (b == SYM_SKP)) begin
            r.ev.skp   = 1'b1;
            r.st.phase = RX_SKP;
          end else if (k && (b == SYM_IDL)) begin
            r.ev.eios  = 1'b1;
            r.st.phase = RX_BODY;
          end else if (k && (b == SYM_FTS)) begin
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
          if (k && (b == SYM_SKP)) begin
            r.ev.os_sym = 1'b1;
          end else begin
            r.st.phase = RX_OUT;
          end
        end
        RX_BODY: begin
          if (k && ((b == SYM_IDL) || (b == SYM_FTS))) begin
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

endpackage

`default_nettype wire
