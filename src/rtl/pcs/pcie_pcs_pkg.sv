`default_nettype none

// Types and constants of the pcie_pcs top: its PIPE encodings, the
// PIPE-control timing and the relock limits
// (wiki/src/arch/pcs/HAS.md#package). Sub-IP types live in each sub-IP's
// own package; the top names them explicitly.
package pcie_pcs_pkg;

  // PowerDown (wiki/src/arch/pcs/PIPE_CONTROL.md#powerdown).
  typedef enum logic [1:0] {
    P0  = 2'd0,
    P0S = 2'd1,
    P1  = 2'd2,
    P2  = 2'd3
  } t_power_down;

  // RxStatus codes (wiki/src/arch/pcs/RECEIVE.md#decode-and-rxstatus).
  typedef enum logic [2:0] {
    RX_OK           = 3'd0,
    RX_SKP_ADDED    = 3'd1,
    RX_SKP_REMOVED  = 3'd2,
    RX_DETECTED     = 3'd3,
    RX_CODE_ERR     = 3'd4,
    RX_EB_OVERFLOW  = 3'd5,
    RX_EB_UNDERFLOW = 3'd6,
    RX_DISP_ERR     = 3'd7
  } t_rx_status;

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

endpackage

`default_nettype wire
