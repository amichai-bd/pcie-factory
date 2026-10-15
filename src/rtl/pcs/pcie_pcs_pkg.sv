`default_nettype none

// The one package of the pcs IP (Q24): the types and constants its blocks
// share and exchange (wiki/src/rtl/pcs/MAS.md#package); the codes it shares
// with the mac (PowerDown, RxStatus, the K symbols) are in common_pkg. The
// PIPE-control timing and relock limits of the top
// (wiki/src/arch/pcs/HAS.md#package), the symbol and word types of the
// receive path, the elastic buffer's word, and the pure 8b/10b decode
// functions. RTL names every item as pcie_pcs_pkg::<name>, never through
// import.
package pcie_pcs_pkg;

  // ---------------------------------------------------------------- top

  // PIPE-control timing, clk_i cycles (wiki/src/arch/pcs/PIPE_CONTROL.md).
  localparam int unsigned PHY_STATUS_RESET_CYCLES = 16;
  localparam int unsigned RX_DETECT_CYCLES        = 64;
  localparam int unsigned POWER_ACK_CYCLES        = 4;

  // Relock (wiki/src/arch/pcs/RECEIVE.md#comma-alignment-and-symbol-lock):
  // errored words that request a relock, clean words that forgive one.
  localparam int unsigned ERR_LIMIT       = 3;
  localparam int unsigned ERR_DECAY_WORDS = 8;

  // Counter widths for those timings and limits.
  localparam int unsigned READY_CNT_W  = $clog2(PHY_STATUS_RESET_CYCLES + 1);
  localparam int unsigned DETECT_CNT_W = $clog2(RX_DETECT_CYCLES);
  localparam int unsigned ACK_CNT_W    = $clog2(POWER_ACK_CYCLES + 1);
  localparam int unsigned ERR_CNT_W    = $clog2(ERR_LIMIT + 1);
  localparam int unsigned CLEAN_CNT_W  = $clog2(ERR_DECAY_WORDS + 1);

  // Receiver detection: cycles counted, and the detection already reported
  // (wiki/src/arch/pcs/PIPE_CONTROL.md#receiver-detection).
  typedef struct packed {
    logic [DETECT_CNT_W-1:0] cnt;
    logic                    done;
  } t_detect;

  // Error counter and relock request (RECEIVE.md, Relock request).
  typedef struct packed {
    logic [ERR_CNT_W-1:0]   err_cnt;    // errored words counted
    logic [CLEAN_CNT_W-1:0] clean_cnt;  // clean words in a row
    logic                   last_gen;   // generation of the last RxValid word
    logic                   gen_known;  // last_gen holds a word's generation
    logic                   wait_relock; // a request is out: wait for a new generation
    logic                   wait_gen;   // the generation that made the request
  } t_relock;

  // ------------------------------------------------------------ symbols

  localparam int unsigned SYM_W    = 10;            // one 10-bit symbol
  localparam int unsigned BYTE_W   = 8;
  localparam int unsigned WORD_W   = 2 * SYM_W;     // two symbols per cycle
  localparam int unsigned WINDOW_W = 2 * WORD_W;    // aligner: previous word, then the current one
  localparam int unsigned COMMA_W  = 7;             // bits a to g of a symbol
  localparam int unsigned OFFSET_W = 4;             // symbol boundary 0 to 9

  typedef logic [SYM_W-1:0]    t_sym10;   // one symbol, bit 0 (line bit a) first
  typedef logic [BYTE_W-1:0]   t_byte;
  typedef logic [WORD_W-1:0]   t_word;    // two symbols, [9:0] first
  typedef logic [WINDOW_W-1:0] t_window;
  typedef logic [COMMA_W-1:0]  t_comma;
  typedef logic [OFFSET_W-1:0] t_offset;

  // A byte with its K flag: one symbol before encoding or after decoding.
  typedef struct packed {
    logic  k;
    t_byte data;
  } t_k_byte;

  // One PIPE transmit word for the encoder: two symbols, sym0 first on the
  // line, and TxCompliance (negative RD before sym0).
  typedef struct packed {
    logic    compliance;
    t_k_byte sym1;
    t_k_byte sym0;
  } t_tx_word;

  // One received raw symbol with its lock tags (aligner to elastic buffer to
  // decoder).
  typedef struct packed {
    logic   idle;  // starts in a word received in electrical idle (never locked)
    logic   lock;  // received under symbol lock
    logic   gen;   // lock-generation bit
    t_sym10 code;  // bit 0 first on the line
  } t_rx_sym;

  // Two received symbols, sym0 the earlier, each with its own lock tags.
  // The aligner tags both with its word's lock; the elastic buffer's read
  // side may pair symbols of two FIFO words.
  typedef struct packed {
    t_rx_sym sym1;
    t_rx_sym sym0;
  } t_rx_pair;

  // One decoded symbol (decoder to the top).
  typedef struct packed {
    logic    code_err;  // valid at neither RD; sym is EDB as K
    logic    disp_err;  // valid only at the other RD (RD known)
    t_k_byte sym;
  } t_dec_sym;

  typedef struct packed {
    t_dec_sym sym1;
    t_dec_sym sym0;     // the earlier symbol
  } t_dec_pair;

  // The PIPE receive register: one word as it leaves on PIPE, with the
  // error-counter inputs (RECEIVE.md, Decode and RxStatus).
  typedef struct packed {
    logic       idle;     // RxElecIdle: the last word presented began in electrical idle
    logic       valid;    // RxValid: both symbols locked, or sym0 locked before idle
    common_pkg::t_rx_status status;   // RxStatus
    logic       errored;  // a code or disparity error under RxValid
    logic       gen;      // lock generation of the last RxValid word
    t_k_byte    sym1;
    t_k_byte    sym0;     // RxData[7:0], RxDataK[0]
  } t_rx_word;

  // Raw codes of COM (K28.5) and SKP (K28.0), bit 0 first, in both columns
  // (wiki/src/arch/pcs/8B10B.md, K symbols). The two columns are each
  // other's complement, so these four values also match an inverted lane.
  localparam t_sym10 COM_RD_MINUS = 10'h17C;  // line order 0011111010
  localparam t_sym10 COM_RD_PLUS  = 10'h283;  // line order 1100000101
  localparam t_sym10 SKP_RD_MINUS = 10'h0BC;  // line order 0011110100
  localparam t_sym10 SKP_RD_PLUS  = 10'h343;  // line order 1100001011

  // ------------------------------------------------------ elastic buffer

  // Sizes and fill levels, in symbols unless noted
  // (wiki/src/arch/pcs/RECEIVE.md#elastic-buffer).
  localparam int unsigned EB_ADDR_W  = 4;   // 16 words of two symbols
  localparam int unsigned EB_NOMINAL = 8;   // start level and nominal fill
  localparam int unsigned EB_HIGH    = 9;   // fill high at or above this
  localparam int unsigned EB_LOW     = 7;   // add a SKP at or below this
  localparam int unsigned EB_HOLDOFF = 8;   // recentring: at most once per 8 cycles
  localparam int unsigned EB_FILL_W  = 6;   // visible fill, 0 to 34 symbols
  localparam int unsigned HOLDOFF_W  = 4;

  // One FIFO word (28 bits). sym0 is the earlier symbol; ovf marks the
  // first word written after a lost one and belongs to sym0.
  typedef struct packed {
    logic       ovf;     // a word was lost just before this one
    logic [1:0] rem;     // rem[i]: a SKP was removed just before sym i
    logic [1:0] idle;    // idle[i]: sym i began in electrical idle
    logic       gen;     // lock generation of the locked symbols
    logic [1:0] lock;    // lock[i]: sym i is locked (a lock end may split a word)
    t_sym10     sym1;
    t_sym10     sym0;
  } t_eb_word;

  // One symbol on the read side, with the flags of its word.
  typedef struct packed {
    logic   ovf;
    logic   rem;
    logic   idle;
    logic   gen;
    logic   lock;
    t_sym10 sym;
  } t_eb_sym;

  // The read side's status of one word (elastic buffer to the top).
  typedef struct packed {
    logic skp_add;  // a SKP was added after a COM of this word
    logic skp_rem;  // a SKP was removed after a COM of this word
    logic ovf;      // words were lost just before this one
    logic udf;      // no symbol to send: an underflow word
  } t_eb_status;

  // Write-side state: the recentring holdoff, the removal and overflow
  // marks, and the packing register.
  typedef struct packed {
    logic [HOLDOFF_W-1:0] holdoff;
    logic                 prev_com;    // last symbol taken was a COM
    logic                 pend_rem;    // next kept symbol follows a removed SKP
    logic                 pend_ovf;    // next written word follows a lost one
    logic                 hold_valid;  // the packing register holds a symbol
    t_eb_sym              hold;
  } t_eb_wr;

  // Read-side state: start, the leftover register, the owed SKP copy, the
  // reported removal and the recentring holdoff.
  typedef struct packed {
    logic                 started;
    logic                 left_valid;  // the leftover register holds a symbol
    t_eb_sym              left;
    logic                 dup;         // send the next symbol twice
    logic                 rem_ack;     // next symbol's removed flag already reported
    logic [HOLDOFF_W-1:0] holdoff;
  } t_eb_rd;

  function automatic logic is_com(t_sym10 s);
    return (s == COM_RD_MINUS) || (s == COM_RD_PLUS);
  endfunction

  function automatic logic is_skp(t_sym10 s);
    return (s == SKP_RD_MINUS) || (s == SKP_RD_PLUS);
  endfunction

  // ------------------------------------------------------------ aligner

  // Lock state of the aligner.
  typedef struct packed {
    logic    lock;
    logic    gen;     // lock generation, flips at every lock
    t_offset offset;  // symbol boundary in the window
  } t_align;

  // ------------------------------------------------------------ decoder

  // The code itself is defined in wiki/src/arch/pcs/8B10B.md; the tables
  // below are its RD- forms, written in line order (a first, the leftmost
  // literal bit), exactly as that page writes them.

  // A code's decode at one assumed running disparity.
  typedef struct packed {
    logic  valid;  // the encoder sends this code for (data, k) at that RD
    logic  k;
    t_byte data;
  } t_match;

  // One decoded symbol and the running disparity after it.
  typedef struct packed {
    t_dec_sym sym;     // EDB as K on a code error
    logic     rd_pos;  // running disparity after the code: 1 positive
  } t_dec;

  // Running-disparity state, after the last symbol of the last word.
  typedef struct packed {
    logic rd_known;  // RD is known (the last symbol was locked)
    logic rd_pos;    // RD positive
    logic gen;       // lock generation of the last symbol
    logic pol;       // polarity in the previous cycle
  } t_dec_state;

  // ------------------------------------------------------------ sub-blocks

  // 6b sub-block abcdei (line order, a = bit 5) of the RD- column:
  // {hit, x}.
  function automatic logic [5:0] six_minus(input logic [5:0] abcdei);
    logic [5:0] hit_x;
    case (abcdei)
      6'b100111: hit_x = {1'b1, 5'd0};
      6'b011101: hit_x = {1'b1, 5'd1};
      6'b101101: hit_x = {1'b1, 5'd2};
      6'b110001: hit_x = {1'b1, 5'd3};
      6'b110101: hit_x = {1'b1, 5'd4};
      6'b101001: hit_x = {1'b1, 5'd5};
      6'b011001: hit_x = {1'b1, 5'd6};
      6'b111000: hit_x = {1'b1, 5'd7};
      6'b111001: hit_x = {1'b1, 5'd8};
      6'b100101: hit_x = {1'b1, 5'd9};
      6'b010101: hit_x = {1'b1, 5'd10};
      6'b110100: hit_x = {1'b1, 5'd11};
      6'b001101: hit_x = {1'b1, 5'd12};
      6'b101100: hit_x = {1'b1, 5'd13};
      6'b011100: hit_x = {1'b1, 5'd14};
      6'b010111: hit_x = {1'b1, 5'd15};
      6'b011011: hit_x = {1'b1, 5'd16};
      6'b100011: hit_x = {1'b1, 5'd17};
      6'b010011: hit_x = {1'b1, 5'd18};
      6'b110010: hit_x = {1'b1, 5'd19};
      6'b001011: hit_x = {1'b1, 5'd20};
      6'b101010: hit_x = {1'b1, 5'd21};
      6'b011010: hit_x = {1'b1, 5'd22};
      6'b111010: hit_x = {1'b1, 5'd23};
      6'b110011: hit_x = {1'b1, 5'd24};
      6'b100110: hit_x = {1'b1, 5'd25};
      6'b010110: hit_x = {1'b1, 5'd26};
      6'b110110: hit_x = {1'b1, 5'd27};
      6'b001110: hit_x = {1'b1, 5'd28};
      6'b101110: hit_x = {1'b1, 5'd29};
      6'b011110: hit_x = {1'b1, 5'd30};
      6'b101011: hit_x = {1'b1, 5'd31};
      default:   hit_x = 6'd0;
    endcase
    return hit_x;
  endfunction

  // 4b sub-block fghj (line order, f = bit 3) of the RD- column:
  // {hit, a7, y}; y = 7 has the primary (P7) and alternate (A7) forms.
  function automatic logic [4:0] four_minus(input logic [3:0] fghj);
    logic [4:0] hit_y;
    case (fghj)
      4'b1011: hit_y = {1'b1, 1'b0, 3'd0};
      4'b1001: hit_y = {1'b1, 1'b0, 3'd1};
      4'b0101: hit_y = {1'b1, 1'b0, 3'd2};
      4'b1100: hit_y = {1'b1, 1'b0, 3'd3};
      4'b1101: hit_y = {1'b1, 1'b0, 3'd4};
      4'b1010: hit_y = {1'b1, 1'b0, 3'd5};
      4'b0110: hit_y = {1'b1, 1'b0, 3'd6};
      4'b1110: hit_y = {1'b1, 1'b0, 3'd7};  // P7
      4'b0111: hit_y = {1'b1, 1'b1, 3'd7};  // A7
      default: hit_y = 5'd0;
    endcase
    return hit_y;
  endfunction

  // The RD+ form is the complement of an unbalanced RD- form or of the
  // alternating one (111000, 1100); a balanced RD- form is sent unchanged.
  // So a received RD+ block is looked up unchanged when it is balanced and
  // not alternating, else complemented; 111000 and 1100 are RD- only.
  function automatic logic [5:0] six_plus(input logic [5:0] abcdei);
    logic [5:0] hit_x;
    if (abcdei == 6'b111000) begin
      hit_x = 6'd0;
    end else if ($countones(abcdei) == 3 && abcdei != 6'b000111) begin
      hit_x = six_minus(abcdei);
    end else begin
      hit_x = six_minus(~abcdei);
    end
    return hit_x;
  endfunction

  function automatic logic [4:0] four_plus(input logic [3:0] fghj);
    logic [4:0] hit_y;
    if (fghj == 4'b1100) begin
      hit_y = 5'd0;
    end else if ($countones(fghj) == 2 && fghj != 4'b0011) begin
      hit_y = four_minus(fghj);
    end else begin
      hit_y = four_minus(~fghj);
    end
    return hit_y;
  endfunction

  // Running disparity after a sub-block (8B10B.md, Running disparity).
  function automatic logic rd_after6(input logic [5:0] abcdei, input logic rd_pos);
    logic rd;
    if ($countones(abcdei) > 3 || abcdei == 6'b000111) begin
      rd = 1'b1;
    end else if ($countones(abcdei) < 3 || abcdei == 6'b111000) begin
      rd = 1'b0;
    end else begin
      rd = rd_pos;
    end
    return rd;
  endfunction

  function automatic logic rd_after4(input logic [3:0] fghj, input logic rd_pos);
    logic rd;
    if ($countones(fghj) > 2 || fghj == 4'b0011) begin
      rd = 1'b1;
    end else if ($countones(fghj) < 2 || fghj == 4'b1100) begin
      rd = 1'b0;
    end else begin
      rd = rd_pos;
    end
    return rd;
  endfunction

  // ---------------------------------------------------------------- codes

  // A symbol (bit 0 = a) in line order, a in bit 9.
  function automatic logic [SYM_W-1:0] line_order(input t_sym10 sym);
    logic [SYM_W-1:0] line;
    for (int unsigned b = 0; b < SYM_W; b++) begin
      line[SYM_W-1-b] = sym[b];
    end
    return line;
  endfunction

  // Whether the encoder sends ``line`` at running disparity ``rd_pos``, and
  // for which byte (8B10B.md, Encoder and K symbols).
  function automatic t_match match_at(input logic [SYM_W-1:0] line, input logic rd_pos);
    t_match     m;
    logic [5:0] six;
    logic [3:0] four;
    logic [5:0] hit_x;
    logic [4:0] hit_y;
    logic [4:0] k28_y;
    logic       rd6;
    logic       alt_req;
    logic       k28_six;
    six  = line[9:4];
    four = line[3:0];
    m    = '0;
    // K28.y: 001111 then the RD+ form of y's 4b block (A7 for y = 7) at
    // RD-, the complement of that whole code at RD+.
    k28_six = rd_pos ? (six == 6'b110000) : (six == 6'b001111);
    k28_y   = rd_pos ? four_plus(~four) : four_plus(four);
    // Data and Kx.7: the 6b block at rd_pos, the 4b block at the RD after it.
    hit_x   = rd_pos ? six_plus(six) : six_minus(six);
    rd6     = rd_after6(six, rd_pos);
    hit_y   = rd6 ? four_plus(four) : four_minus(four);
    // A7 replaces P7 when P7 would extend e, i into five equal bits: e = i
    // = 1 before P7's 111 (RD- after 6b), e = i = 0 before 000 (RD+).
    alt_req = rd6 ? (six[1:0] == 2'b00) : (six[1:0] == 2'b11);
    if (k28_six) begin
      m.valid = k28_y[4] && (k28_y[2:0] != 3'd7 || k28_y[3]);
      m.k     = 1'b1;
      m.data  = {k28_y[2:0], 5'd28};
    end else if (hit_x[5] && hit_y[4]) begin
      m.data = {hit_y[2:0], hit_x[4:0]};
      if (hit_y[2:0] != 3'd7) begin
        m.valid = 1'b1;                       // y = 0..6: data only
      end else if (hit_y[3] == alt_req) begin
        m.valid = 1'b1;                       // Dx.7 with the required form
      end else if (hit_y[3] && (hit_x[4:0] == 5'd23 || hit_x[4:0] == 5'd27 ||
                                hit_x[4:0] == 5'd29 || hit_x[4:0] == 5'd30)) begin
        m.valid = 1'b1;                       // Kx.7: always A7
        m.k     = 1'b1;
      end
    end
    return m;
  endfunction

  // Decode one code (bit 0 = a) at the running disparity ``rd_pos`` when
  // ``rd_known``, else with RD unknown (either column accepted, RD- assumed
  // when both are, or neither); 8B10B.md, Decoder and error classes.
  function automatic t_dec decode(input t_sym10 sym, input logic rd_known, input logic rd_pos);
    t_dec             d;
    logic [SYM_W-1:0] line;
    t_match           m_neg;
    t_match           m_pos;
    t_match           m_cur;
    t_match           m_oth;
    logic             rd_in;
    line  = line_order(sym);
    m_neg = match_at(line, 1'b0);
    m_pos = match_at(line, 1'b1);
    m_cur = rd_pos ? m_pos : m_neg;
    m_oth = rd_pos ? m_neg : m_pos;
    d     = '0;
    if (rd_known) begin
      rd_in = rd_pos;
    end else begin
      rd_in = !m_neg.valid && m_pos.valid;
    end
    if (rd_known && m_cur.valid) begin
      d.sym.sym.data = m_cur.data;
      d.sym.sym.k    = m_cur.k;
    end else if (rd_known && m_oth.valid) begin
      d.sym.sym.data = m_oth.data;
      d.sym.sym.k    = m_oth.k;
      d.sym.disp_err = 1'b1;
    end else if (!rd_known && (m_neg.valid || m_pos.valid)) begin
      d.sym.sym.data = m_neg.valid ? m_neg.data : m_pos.data;
      d.sym.sym.k    = m_neg.valid ? m_neg.k : m_pos.k;
    end else begin
      d.sym.sym.data = common_pkg::SYM_EDB;
      d.sym.sym.k    = 1'b1;
      d.sym.code_err = 1'b1;
    end
    d.rd_pos = rd_after4(line[3:0], rd_after6(line[9:4], rd_in));
    return d;
  endfunction

endpackage

`default_nettype wire
