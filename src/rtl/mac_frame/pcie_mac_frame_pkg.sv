// Types and constants of pcie_mac_frame (wiki/src/rtl/mac_frame/MAS.md):
// the symbol bytes of the symbols table (wiki/src/arch/mac/SYMBOLS.md#symbols),
// the receive ordered-set skip states and the receive beat.
`default_nettype none

package pcie_mac_frame_pkg;

  localparam int unsigned BYTE_W = 8;
  localparam int unsigned BEAT_W = 16;  // two bytes, [7:0] first

  typedef logic [BYTE_W-1:0] t_byte;
  typedef logic [BEAT_W-1:0] t_beat_data;

  // K symbol bytes (SYMBOLS.md, Symbols table).
  localparam t_byte SYM_COM = 8'hBC;  // K28.5
  localparam t_byte SYM_STP = 8'hFB;  // K27.7
  localparam t_byte SYM_SDP = 8'h5C;  // K28.2
  localparam t_byte SYM_END = 8'hFD;  // K29.7
  localparam t_byte SYM_EDB = 8'hFE;  // K30.7
  localparam t_byte SYM_SKP = 8'h1C;  // K28.0
  localparam t_byte SYM_FTS = 8'h3C;  // K28.1
  localparam t_byte SYM_IDL = 8'h7C;  // K28.3
  // Logical idle: the data symbol 00h before scrambling.
  localparam t_byte IDLE_DATA = 8'h00;

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

endpackage

`default_nettype wire
