// Transmit arbiter of pcie_dll (wiki/src/rtl/dll/MAS.md#transmit): at the
// end of each packet on the phy stream, and whenever the stream is idle, it
// starts the first ready request in the transmit priority
// (wiki/src/arch/dll/RETRY.md#transmit-priority), adds the CRC-16 and sends
// the DLLP as three beats. A packet once started is always finished, unless
// the link goes down. Today's requests are 1-2 Ack or Nak (#213, tied off),
// 3 InitFC and 4 UpdateFC; the TLP sources 5 and 6 join with #212.
`default_nettype none

`include "common_macros.svh"

module pcie_dll_tx (
  input  logic                    clk_i,
  input  logic                    rst_i,
  input  logic                    flush_i,        // DL_Inactive: drop everything
  input  pcie_dll_pkg::t_dllp_req acknak_req_i,   // priority 1 and 2: a due Nak or Ack
  input  pcie_dll_pkg::t_dllp_req initfc_req_i,   // priority 3
  input  pcie_dll_pkg::t_dllp_req updfc_req_i,    // priority 4
  output logic                    acknak_take_o,  // the request is taken at this edge
  output logic                    initfc_take_o,
  output logic                    updfc_take_o,
  output logic                    busy_o,         // a packet is on the stream
  output logic                    pkt_tx_valid_o,
  input  logic                    pkt_tx_ready_i,
  output pcie_dll_pkg::t_pkt_tx   pkt_tx_o
);

  localparam logic [1:0] LAST_BEAT = 2'd2;  // a DLLP is 3 beats

  pcie_dll_pkg::t_tx_state tx_q, tx_d;

  logic        accept;     // the stream takes the beat at this edge
  logic        free;       // nothing on the stream after this edge
  logic        load;       // a request starts at this edge
  logic [31:0] body;       // the chosen request's body
  logic [15:0] beat_data;  // the beat offered: bytes 2*beat and 2*beat+1

  assign accept = tx_q.busy && pkt_tx_ready_i;
  assign free   = !tx_q.busy || (accept && tx_q.beat == LAST_BEAT);

  always_comb begin
    acknak_take_o = 1'b0;
    initfc_take_o = 1'b0;
    updfc_take_o  = 1'b0;
    body          = updfc_req_i.body;
    if (free && !flush_i) begin
      if (acknak_req_i.valid) begin
        acknak_take_o = 1'b1;
        body          = acknak_req_i.body;
      end else if (initfc_req_i.valid) begin
        initfc_take_o = 1'b1;
        body          = initfc_req_i.body;
      end else if (updfc_req_i.valid) begin
        updfc_take_o = 1'b1;
      end
    end
  end

  assign load = acknak_take_o || initfc_take_o || updfc_take_o;

  always_comb begin
    tx_d = tx_q;
    if (flush_i) begin
      tx_d = '0;
    end else if (load) begin
      tx_d.busy = 1'b1;
      tx_d.beat = 2'd0;
      tx_d.pkt  = {pcie_dll_pkg::crc16(body), body};
    end else if (free) begin
      tx_d.busy = 1'b0;
    end else if (accept) begin
      tx_d.beat = tx_q.beat + 2'd1;
    end
  end

  `COMMON_DFF_RST(tx_q, tx_d, clk_i, rst_i)

  assign busy_o         = tx_q.busy;
  assign pkt_tx_valid_o = tx_q.busy;

  always_comb begin
    case (tx_q.beat)
      2'd1:    beat_data = tx_q.pkt[31:16];
      2'd2:    beat_data = tx_q.pkt[47:32];
      default: beat_data = tx_q.pkt[15:0];
    endcase
  end

  assign pkt_tx_o = '{data: beat_data, sop: tx_q.beat == 2'd0, eop: tx_q.beat == LAST_BEAT,
                      dllp: 1'b1};

  `COMMON_ASSERT_NEVER(a_beat_range, clk_i, rst_i, tx_q.busy && tx_q.beat == 2'd3)
  // From an accepted sop to its eop the stream must be ready every cycle
  // (phy HAS, packet stream); the dll keeps valid high meanwhile.
  `COMMON_ASSERT_NEVER(a_ready_inside_packet, clk_i, rst_i,
                       tx_q.busy && tx_q.beat != 2'd0 && !pkt_tx_ready_i && !flush_i)

endmodule

`default_nettype wire
