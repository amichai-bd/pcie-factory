// Types, constants and pure decode functions of the 8b/10b decoder
// (wiki/src/rtl/pcs_dec8b10b/MAS.md). The code itself is defined in
// wiki/src/arch/pcs/8B10B.md; the tables below are its RD- forms, written in
// line order (a first, the leftmost literal bit), exactly as that page writes
// them.
`default_nettype none

package pcie_pcs_dec8b10b_pkg;

  localparam int unsigned SYM_W  = 10;
  localparam int unsigned BYTE_W = 8;

  // EDB (K30.7), substituted for a code error; the value of the pcs HAS's
  // K_EDB (wiki/src/arch/pcs/HAS.md#package).
  localparam logic [BYTE_W-1:0] K_EDB = 8'hFE;

  typedef logic [SYM_W-1:0]  t_sym10;   // one symbol, bit 0 = a, first on the line
  typedef logic [BYTE_W-1:0] t_byte;

  // A code's decode at one assumed running disparity.
  typedef struct packed {
    logic  valid;  // the encoder sends this code for (data, k) at that RD
    logic  k;
    t_byte data;
  } t_match;

  // One decoded symbol.
  typedef struct packed {
    t_byte data;      // decoded byte, or K_EDB on a code error
    logic  k;         // K flag (set with K_EDB on a code error)
    logic  code_err;  // valid at neither RD
    logic  disp_err;  // valid only at the other RD (RD known)
    logic  rd_pos;    // running disparity after the code: 1 positive
  } t_dec;

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
      d.data = m_cur.data;
      d.k    = m_cur.k;
    end else if (rd_known && m_oth.valid) begin
      d.data     = m_oth.data;
      d.k        = m_oth.k;
      d.disp_err = 1'b1;
    end else if (!rd_known && (m_neg.valid || m_pos.valid)) begin
      d.data = m_neg.valid ? m_neg.data : m_pos.data;
      d.k    = m_neg.valid ? m_neg.k : m_pos.k;
    end else begin
      d.data     = K_EDB;
      d.k        = 1'b1;
      d.code_err = 1'b1;
    end
    d.rd_pos = rd_after4(line[3:0], rd_after6(line[9:4], rd_in));
    return d;
  endfunction

endpackage

`default_nettype wire
