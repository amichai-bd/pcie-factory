// Transmit arbiter of pcie_dll (wiki/src/rtl/dll/MAS.md#transmit): at the
// end of each packet on the phy stream, and whenever the stream is idle, it
// starts the first ready request in the transmit priority
// (wiki/src/arch/dll/RETRY.md#transmit-priority), adds the CRC-16 and sends
// the DLLP as three beats. A packet once started (its sop taken by the
// phy) is always finished, also when the link goes down: the remaining
// beats up to eop are sent, as the phy expects (MAS, Transmit). The requests are 1-2 a due Nak or Ack
// from the TLP receive path, 3 InitFC, 4 UpdateFC and 5-6 a replayed or new
// TLP from the TLP transmit block, whose beats it passes through with the
// TLP tag.
`default_nettype none

`include "common_macros.svh"

module pcie_dll_tx (
  input  logic                    clk_i,
  input  logic                    rst_i,
  input  logic                    flush_i,        // DL_Inactive: drop everything
  input  pcie_dll_pkg::t_dllp_req acknak_req_i,   // priority 1 and 2: a due Nak or Ack
  input  pcie_dll_pkg::t_dllp_req initfc_req_i,   // priority 3
  input  pcie_dll_pkg::t_dllp_req updfc_req_i,    // priority 4
  input  pcie_dll_pkg::t_tlp_src  tlp_src_i,      // priorities 5 and 6: a TLP, and its beats
  input  logic                    tlp_hold_i,     // rollover hold or rollover now: a TLP not started is withdrawn
  output logic                    acknak_take_o,  // the request is taken at this edge
  output logic                    initfc_take_o,
  output logic                    updfc_take_o,
  output logic                    tlp_take_o,
  output logic                    tlp_beat_o,     // the phy takes a TLP beat at this edge
  output logic                    tlp_abort_o,    // a TLP offered, sop not taken, is withdrawn
  output logic                    busy_o,         // a packet is on the stream
  output logic                    pkt_tx_valid_o,
  input  logic                    pkt_tx_ready_i,
  output pcie_dll_pkg::t_pkt_tx   pkt_tx_o
);

  localparam logic [1:0] LAST_BEAT = 2'd2;  // a DLLP is 3 beats

  pcie_dll_pkg::t_tx_state tx_q, tx_d;

  logic        withdraw;   // a TLP offered, sop not taken, withdrawn by the rollover hold
  logic        accept;     // the stream takes the beat at this edge
  logic        started;    // the phy has taken, or takes now, this packet's sop
  logic        open_q, open_d;  // assertion state: a packet is open at the phy
  logic        cur_eop;    // the beat offered is the packet's last
  logic        free;       // nothing on the stream after this edge
  logic        load;       // a request starts at this edge
  logic [31:0] body;       // the chosen request's body
  logic [15:0] beat_data;  // the DLLP beat offered: bytes 2*beat and 2*beat+1
  logic [15:0] pkt_data;   // the beat offered, DLLP or TLP

  // Under the rollover hold no TLP starts: one whose sop is offered and
  // not taken is no longer offered and is dropped (the replay resends it).
  assign withdraw = tlp_hold_i && tx_q.tlp && tx_q.beat == 2'd0;
  assign accept   = pkt_tx_valid_o && pkt_tx_ready_i;
  // Started: the phy took the sop earlier, or takes it at this edge (also
  // at the edge that samples link_up_i 0).
  assign started = tx_q.busy && (tx_q.beat != 2'd0 || (pkt_tx_ready_i && !withdraw));
  assign cur_eop = tx_q.tlp ? tlp_src_i.eop : tx_q.beat == LAST_BEAT;
  assign free    = !tx_q.busy || (accept && cur_eop);

  always_comb begin
    acknak_take_o = 1'b0;
    initfc_take_o = 1'b0;
    updfc_take_o  = 1'b0;
    tlp_take_o    = 1'b0;
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
      end else if (tlp_src_i.valid) begin
        tlp_take_o = 1'b1;
      end
    end
  end

  assign load = acknak_take_o || initfc_take_o || updfc_take_o || tlp_take_o;

  always_comb begin
    tx_d = tx_q;
    if ((flush_i || withdraw) && !started) begin
      // A request offered but not yet taken is withdrawn; a started packet
      // goes on to its eop below (no new request loads under flush).
      tx_d = '0;
    end else if (load) begin
      tx_d.busy = 1'b1;
      tx_d.tlp  = tlp_take_o;
      tx_d.beat = 2'd0;
      tx_d.pkt  = {pcie_dll_pkg::crc16(body), body};
    end else if (free) begin
      tx_d.busy = 1'b0;
      tx_d.tlp  = 1'b0;
    end else if (accept) begin
      // A TLP's beats come from its source; beat 1 marks its sop taken.
      tx_d.beat = tx_q.tlp ? 2'd1 : tx_q.beat + 2'd1;
    end
  end

  `COMMON_DFF_RST(tx_q, tx_d, clk_i, rst_i)

  assign busy_o         = tx_q.busy;
  assign pkt_tx_valid_o = tx_q.busy && !withdraw;
  assign tlp_beat_o     = accept && tx_q.tlp;
  assign tlp_abort_o    = (flush_i || withdraw) && !started && tx_q.tlp;

  always_comb begin
    case (tx_q.beat)
      2'd1:    beat_data = tx_q.pkt[31:16];
      2'd2:    beat_data = tx_q.pkt[47:32];
      default: beat_data = tx_q.pkt[15:0];
    endcase
  end

  assign pkt_data = tx_q.tlp ? tlp_src_i.data : beat_data;
  assign pkt_tx_o = '{data: pkt_data, sop: tx_q.beat == 2'd0, eop: cur_eop, dllp: !tx_q.tlp};

  // A packet open at the phy, tracked as the MAC tracks it (mac MAS, Frame:
  // open from an accepted sop to an accepted eop, cleared only by reset).
  always_comb begin
    open_d = open_q;
    if (pkt_tx_valid_o && pkt_tx_ready_i) begin
      open_d = !pkt_tx_o.eop;
    end
  end

  `COMMON_DFF_RST(open_q, open_d, clk_i, rst_i)

  `COMMON_ASSERT_NEVER(a_beat_range, clk_i, rst_i, tx_q.busy && tx_q.beat == 2'd3)
  // From an accepted sop to its eop the stream must be ready every cycle
  // (phy HAS, packet stream), link down or not; the dll keeps valid high.
  `COMMON_ASSERT_NEVER(a_ready_inside_packet, clk_i, rst_i,
                       tx_q.busy && tx_q.beat != 2'd0 && !pkt_tx_ready_i)
  // No sop while a packet is open, and every open packet runs to its eop,
  // across a link-down too: valid never drops inside it.
  `COMMON_ASSERT_NEVER(a_sop_inside_packet, clk_i, rst_i,
                       open_q && pkt_tx_valid_o && pkt_tx_o.sop)
  `COMMON_ASSERT_NEVER(a_eop_follows_sop, clk_i, rst_i, open_q && !pkt_tx_valid_o)

endmodule

`default_nettype wire
