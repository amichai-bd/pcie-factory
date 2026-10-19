// The shared package (Q24): constants and types that more than one IP of
// the design shares (wiki/src/rtl/common/common_pkg.md). Codes are typed
// enums, so an IP that includes the package without using every code
// still lints clean.
`default_nettype none

package common_pkg;

  // K symbols as the byte that crosses PIPE (wiki/src/arch/mac/SYMBOLS.md#symbols)
  // [cite: PCIe Base Spec, rev 3.0, §4.2.1.1].
  typedef enum logic [7:0] {
    SYM_COM = 8'hBC,  // K28.5
    SYM_STP = 8'hFB,  // K27.7
    SYM_SDP = 8'h5C,  // K28.2
    SYM_END = 8'hFD,  // K29.7
    SYM_EDB = 8'hFE,  // K30.7
    SYM_PAD = 8'hF7,  // K23.7
    SYM_SKP = 8'h1C,  // K28.0
    SYM_FTS = 8'h3C,  // K28.1
    SYM_IDL = 8'h7C   // K28.3
  } t_k_code;

  // Data symbols with a fixed meaning in ordered sets and idle
  // (wiki/src/arch/mac/SYMBOLS.md#ts1-and-ts2).
  typedef enum logic [7:0] {
    IDLE_DATA  = 8'h00,  // logical idle, before scrambling
    RATE_GEN1  = 8'h02,  // data rate identifier: 2.5 GT/s only
    TS1_ID     = 8'h4A,  // D10.2
    TS2_ID     = 8'h45,  // D5.2
    TS1_ID_INV = 8'hB5,  // TS1 received with inverted polarity
    TS2_ID_INV = 8'hBA   // TS2 received with inverted polarity
  } t_d_code;

  // Compliance pattern data symbols: COM D21.5 COM D10.2.
  typedef enum logic [7:0] {
    COMPL_D21_5 = 8'hB5,
    COMPL_D10_2 = 8'h4A
  } t_compl_code;

  // Main LTSSM states as ltssm_state_o shows them
  // (wiki/src/arch/mac/LTSSM.md#state-encoding). In L0 the code is
  // LTSSM_L0 | tx << 2 | rx (t_l0s_sub for each sub-machine).
  typedef enum logic [5:0] {
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

  // L0s sub-machine states, the tx and rx fields of an L0 code. Off (the
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

  // PIPE RxStatus codes, PCS to MAC (wiki/src/arch/pcs/RECEIVE.md#decode-and-rxstatus);
  // 4 to 7 are errors.
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

  // A PIPE data word: two symbols, [7:0] first on the line; bit i of datak
  // marks byte i as a K symbol (wiki/src/arch/phy/PIPE.md#signals).
  typedef struct packed {
    logic [15:0] data;
    logic [1:0]  datak;
  } t_word;

  // PIPE receive status of one word, PCS to MAC.
  typedef struct packed {
    logic       phy_status;  // PhyStatus
    logic       valid;       // RxValid
    logic       elec_idle;   // RxElecIdle
    t_rx_status status;      // RxStatus; 4 to 7 is an error
  } t_pipe_rx;

  // PIPE control, MAC to PCS, other than the data word and TxCompliance.
  typedef struct packed {
    logic        reset_n;       // PCS reset, active low
    logic        tx_elec_idle;  // TxElecIdle
    logic        tx_detect_rx;  // TxDetectRx/Loopback
    logic        rx_polarity;   // RxPolarity
    t_power_down power_down;    // PowerDown
  } t_pipe_ctl;

  // Root port: the tl-seq interface between rc_tl and rc_seq
  // (wiki/src/arch/rc/TL_SEQ.md).

  // A command's operation (TL_SEQ.md#commands).
  typedef enum logic [3:0] {
    RC_OP_CFG_RD  = 4'd0,
    RC_OP_CFG_WR  = 4'd1,
    RC_OP_MEM_RD  = 4'd2,
    RC_OP_MEM_WR  = 4'd3,
    RC_OP_HDR_RD  = 4'd4,
    RC_OP_HDR_WR  = 4'd5,
    RC_OP_HOST_RD = 4'd6,
    RC_OP_HOST_WR = 4'd7,
    RC_OP_ERR_RD  = 4'd8,
    RC_OP_ERR_CLR = 4'd9
  } t_rc_op;

  // A result beat's status (TL_SEQ.md#results).
  typedef enum logic [2:0] {
    RC_ST_OK        = 3'd0,
    RC_ST_UR        = 3'd1,
    RC_ST_CA        = 3'd2,
    RC_ST_CRS       = 3'd3,
    RC_ST_TIMEOUT   = 3'd4,
    RC_ST_LOCAL_UR  = 3'd5,
    RC_ST_LINK_DOWN = 3'd6,
    RC_ST_BAD       = 3'd7
  } t_rc_status;

  // A command, rc_seq to rc_tl. op is the raw code: an undefined one is
  // refused, so it is not t_rc_op.
  typedef struct packed {
    logic [3:0]  op;
    logic [31:0] addr;
    logic [5:0]  len;       // DWs, 1 to 32 for a memory read, else 1
    logic [3:0]  first_be;  // bit i enables data byte i
    logic [3:0]  last_be;   // the last DW's enables when len is above 1, else 0
    logic [31:0] data;      // byte 0 in [7:0]
  } t_rc_cmd;

  // A result beat, rc_tl to rc_seq.
  typedef struct packed {
    t_rc_status  status;
    logic [31:0] data;      // byte 0 in [7:0]
    logic        last;      // the command's last beat
  } t_rc_rsp;

  // A captured MSI, rc_tl to rc_seq.
  typedef struct packed {
    logic [9:0]  offset;    // DW offset in the MSI window
    logic [31:0] data;      // byte 0 in [7:0]
  } t_rc_msi;

endpackage

`default_nettype wire
