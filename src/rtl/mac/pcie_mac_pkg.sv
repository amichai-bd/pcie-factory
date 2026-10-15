// Types and constants of pcie_mac (wiki/src/arch/mac/HAS.md#package):
// the ltssm_state_o encoding (wiki/src/arch/mac/LTSSM.md#state-encoding)
// and the PIPE PowerDown codes (wiki/src/arch/phy/PIPE.md#signals).
`default_nettype none

package pcie_mac_pkg;

  localparam int unsigned LTSSM_STATE_W = 6;

  // Main LTSSM states as ltssm_state_o shows them. In L0 the code is
  // LTSSM_L0 | tx << 2 | rx (t_l0s_sub for each sub-machine).
  typedef enum logic [LTSSM_STATE_W-1:0] {
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

  // L0s sub-machine states, the tx and rx fields of an L0 code.
  typedef enum logic [1:0] {
    L0S_SUB_L0    = 2'd0,
    L0S_SUB_ENTRY = 2'd1,
    L0S_SUB_IDLE  = 2'd2,
    L0S_SUB_FTS   = 2'd3
  } t_l0s_sub;

  // PIPE PowerDown.
  typedef enum logic [1:0] {
    PD_P0  = 2'd0,
    PD_P0S = 2'd1,
    PD_P1  = 2'd2,
    PD_P2  = 2'd3
  } t_power_down;

endpackage

`default_nettype wire
