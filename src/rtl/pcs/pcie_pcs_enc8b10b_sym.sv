// One 8b/10b symbol, combinational: byte, K flag and running disparity in,
// 10-bit code and running disparity out. The code rules are in
// wiki/src/arch/pcs/8B10B.md; the structure in
// wiki/src/rtl/pcs/MAS.md#encoder.
//
// Bit order: data_i is HGFEDCBA (x = EDCBA, y = HGF). code_o[0] is line bit
// a, then b c d e i f g h j, so code_o[5:0] is the 6b sub-block {i,e,d,c,b,a}
// and code_o[9:6] the 4b sub-block {j,h,g,f}. Running disparity: 0 negative,
// 1 positive.
`default_nettype none

module pcie_pcs_enc8b10b_sym (
  input  logic [7:0] data_i,  // HGFEDCBA
  input  logic       k_i,     // 1: K symbol
  input  logic       rd_i,    // running disparity before the symbol
  output pcie_pcs_pkg::t_sym10 code_o,  // bit 0 first on the line
  output logic       rd_o     // running disparity after the symbol
);

  logic [4:0] x_in;
  logic [2:0] y_in;
  logic       k_valid;     // K28.0-7, K23.7, K27.7, K29.7, K30.7
  logic [4:0] x;           // after unsupported K -> EDB (K30.7)
  logic [2:0] y;
  logic       k28;
  logic [5:0] six_minus;   // RD- form of the 6b sub-block
  logic       six_flip;    // unbalanced: flips RD
  logic       six_comp;    // unbalanced or 111000: complemented at RD+
  logic [5:0] six;
  logic       rd6;         // running disparity after the 6b sub-block
  logic       alt7;        // y = 7 uses A7
  logic [3:0] four_minus;  // RD- form of the 4b sub-block
  logic       four_flip;   // unbalanced: flips RD
  logic       four_comp;   // unbalanced or 1100: complemented at RD+
  logic       four_inv;
  logic [3:0] four;

  assign x_in    = data_i[4:0];
  assign y_in    = data_i[7:5];
  assign k_valid = (x_in == 5'd28)
                || ((y_in == 3'd7) && ((x_in == 5'd23) || (x_in == 5'd27)
                                    || (x_in == 5'd29) || (x_in == 5'd30)));
  assign x       = (k_i && !k_valid) ? pcie_pcs_pkg::K_EDB[4:0] : x_in;
  assign y       = (k_i && !k_valid) ? pcie_pcs_pkg::K_EDB[7:5] : y_in;
  assign k28     = k_i && (x == 5'd28);

  // 6b sub-block, RD- form. Two or three ones in EDCBA: sent as is, with i
  // set for two ones. Every other x, and D24, uses a fixed form with four
  // ones. Literals are {i,e,d,c,b,a}; the comment gives line order abcdei.
  always_comb begin
    six_minus = {($countones(x) == 2), x};
    unique case (x)
      5'd0:    six_minus = 6'b111001;  // 100111
      5'd1:    six_minus = 6'b101110;  // 011101
      5'd2:    six_minus = 6'b101101;  // 101101
      5'd4:    six_minus = 6'b101011;  // 110101
      5'd8:    six_minus = 6'b100111;  // 111001
      5'd15:   six_minus = 6'b111010;  // 010111
      5'd16:   six_minus = 6'b110110;  // 011011
      5'd23:   six_minus = 6'b010111;  // 111010
      5'd24:   six_minus = 6'b110011;  // 110011
      5'd27:   six_minus = 6'b011011;  // 110110
      5'd29:   six_minus = 6'b011101;  // 101110
      5'd30:   six_minus = 6'b011110;  // 011110
      5'd31:   six_minus = 6'b110101;  // 101011
      default: six_minus = {($countones(x) == 2), x};
    endcase
    if (k28) begin
      six_minus = 6'b111100;           // 001111
    end
  end

  // An unbalanced block flips RD. 111000 / 000111 leave RD negative /
  // positive, so as sent (111000 at RD-, 000111 at RD+) they keep it.
  assign six_flip = $countones(six_minus) != 3;
  assign six_comp = six_flip || (six_minus == 6'b000111);  // 111000
  assign six      = (rd_i && six_comp) ? ~six_minus : six_minus;
  assign rd6      = rd_i ^ six_flip;

  // Dx.7 uses A7 where P7 would extend e and i to a run of five equal bits:
  // P7 is fgh = 111 at RD- and 000 at RD+. Every Kx.7, K28.7 included, uses A7.
  assign alt7 = k_i || (!rd6 && six[4] && six[5]) || (rd6 && !six[4] && !six[5]);

  // 4b sub-block, RD- form. Literals are {j,h,g,f}; the comment gives line
  // order fghj.
  always_comb begin
    four_minus = {($countones(y) == 1), y};  // 1, 2, 5, 6 balanced; 3 is 1100
    unique case (y)
      3'd0:    four_minus = 4'b1101;                      // 1011
      3'd4:    four_minus = 4'b1011;                      // 1101
      3'd7:    four_minus = alt7 ? 4'b1110 : 4'b0111;     // A7 0111, P7 1110
      default: four_minus = {($countones(y) == 1), y};
    endcase
  end

  assign four_flip = $countones(four_minus) != 2;
  assign four_comp = four_flip || (four_minus == 4'b0011);  // 1100, kept like 111000
  // At RD+ a K28 code is the complement of its whole RD- code, so a balanced
  // 4b sub-block is complemented too (K28.1, .2, .5, .6); otherwise the
  // usual column rule at the RD after the 6b sub-block.
  assign four_inv  = (rd6 && four_comp) || (k28 && rd_i && !four_comp);
  assign four      = four_inv ? ~four_minus : four_minus;

  assign code_o = {four, six};
  assign rd_o   = rd6 ^ four_flip;

endmodule

`default_nettype wire
