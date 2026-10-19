// pcie_gpio package: slot codes, idle words, the link-up constants and the
// structs the adapter's blocks exchange (wiki/src/arch/gpio/HAS.md,
// wiki/src/arch/gpio/LINK_UP.md; structure: wiki/src/rtl/gpio/MAS.md).
`default_nettype none

package pcie_gpio_pkg;

  // ------------------------------------------------------------ slot codes

  // The 3-bit code a slot's control pin carries on transfers 1 to 3 (HAS,
  // slots).
  typedef enum logic [2:0] {
    CODE_IDLE     = 3'd0,  // an idle word, between packets
    CODE_MID      = 3'd1,  // a beat of the open packet
    CODE_SOP_TLP  = 3'd2,  // first beat of a TLP
    CODE_SOP_DLLP = 3'd3,  // first beat of a DLLP
    CODE_EOP      = 3'd4,  // last beat of the open packet
    CODE_ABORT    = 3'd5,  // the open packet is cut by its sender
    CODE_ONE_TLP  = 3'd6,  // a one-beat TLP
    CODE_ONE_DLLP = 3'd7   // a one-beat DLLP
  } t_code;

  // ------------------------------------------------------------ idle words

  localparam logic [15:0] WORD_TRAIN     = 16'h005A;  // TRAIN, bit 8 clear
  localparam logic [15:0] WORD_TRAIN_OK  = 16'h015A;  // TRAIN, bit 8 set
  localparam logic [15:0] WORD_IDLE      = 16'h00C3;  // IDLE, while up
  localparam logic [15:0] WORD_HOT_RESET = 16'h0096;  // HOT_RESET, downstream in Hot Reset

  // ------------------------------------------------- ltssm_state_o codes

  // The four mac encoding codes the adapter uses (HAS, link status).
  localparam logic [5:0] LTSSM_RESET     = 6'h00;  // reset, not aligned
  localparam logic [5:0] LTSSM_TRAINING  = 6'h04;  // aligned, not up
  localparam logic [5:0] LTSSM_HOT_RESET = 6'h19;
  localparam logic [5:0] LTSSM_UP        = 6'h20;

  // ----------------------------------------------------- link-up runs

  // Counted in received slots unless noted (LINK_UP.md, rules).
  localparam int unsigned ALIGN_SLOTS    = 8;   // marker slots at one phase to align
  localparam int unsigned DAMAGE_WINDOW  = 8;   // a second damaged slot within this many: loss
  localparam int unsigned UP_RUN         = 8;   // good slots to go up
  localparam int unsigned DOWN_CLEAR_RUN = 4;   // bit-8-clear TRAIN words while up: down
  localparam int unsigned DOWN_ANY_RUN   = 64;  // TRAIN words of either value while up: down
  localparam int unsigned HR_ENTER_RUN   = 4;   // HOT_RESET words: upstream enters Hot Reset
  localparam int unsigned HR_EXIT_RUN    = 4;   // TRAIN words: upstream leaves Hot Reset
  localparam int unsigned QUIET_WORDS    = 16;  // quiet run, in the sender's own slots
  localparam int unsigned HR_WORDS       = 32;  // HOT_RESET words a downstream end sends
  localparam int unsigned WATCHDOG_XFERS = 64;  // own transfer times with no slot: loss

  // --------------------------------------------------------- transmit

  // A maximum packet: a TLP with a 4-DW header, 128 bytes of payload, its
  // sequence number and LCRC, in 16-bit beats (HAS, transmit).
  localparam int unsigned MAX_PKT_BEATS = 75;
  // Transmit buffer depth D in beats (MAS, transmit buffer): exactly one
  // maximum packet, so ready rises between packets only on an empty buffer.
  localparam int unsigned TX_DEPTH      = 75;
  // The buffer's RAM: 2**TX_ADDR_W entries plus the head register.
  localparam int unsigned TX_ADDR_W     = 7;

  // ---------------------------------------------------------- receive

  // The receive crossing FIFO: 2**RX_ADDR_W slots (MAS, receive crossing).
  localparam int unsigned RX_ADDR_W = 2;

  // ------------------------------------------------------------ types

  // One slot as the transmitter sends it: a code and a 16-bit beat; also a
  // transmit buffer entry.
  typedef struct packed {
    t_code       code;
    logic [15:0] data;   // beat bits [4t+3:4t] go out on transfer t
  } t_slot;

  // Four received transfers at the receiver's slot phase, oldest first, as
  // they cross into clk_i.
  typedef struct packed {
    logic        new_phase;  // the receive front end moved its slot phase here
    logic [3:0]  ctrl;       // control pin, transfer t on bit t: marker on bit 0, code bits on 3:1
    logic [15:0] data;       // data pins, transfer t on bits [4t+3:4t]
  } t_rx_slot;

  // Link state (LINK_UP.md, states): not aligned and training are one state
  // told apart by the alignment flag.
  typedef enum logic [1:0] {
    ST_TRAIN     = 2'd0,
    ST_UP        = 2'd1,
    ST_HOT_RESET = 2'd2
  } t_state;

  // What the link state asks the transmitter to send between packets.
  typedef struct packed {
    logic up;         // up: packets and IDLE words; the buffer runs
    logic hot_reset;  // downstream in Hot Reset: HOT_RESET words
    logic bit8;       // TRAIN words with bit 8 set: aligned and quiet run over
  } t_tx_mode;

  // What the transmitter sent at a slot start, for the link state's counts.
  typedef struct packed {
    logic train_clear;  // a TRAIN word with bit 8 clear
    logic hot_reset;    // a HOT_RESET word
  } t_tx_sent;

  // One receive stream beat.
  typedef struct packed {
    logic        valid;
    logic        sop;
    logic        eop;
    logic        dllp;
    logic        err;
    logic [15:0] data;
  } t_rx_beat;

  // The link state and the receive framing (MAS, link state).
  typedef struct packed {
    t_state     state;
    logic       aligned;     // the receiver is aligned
    logic [3:0] align_cnt;   // marker slots in a row while not aligned
    logic [3:0] dmg_win;     // slots left in the damage window
    logic [4:0] quiet_cnt;   // bit-8-clear TRAIN words of the quiet run sent, to QUIET_WORDS
    logic [5:0] hr_cnt;      // HOT_RESET words sent in this Hot Reset, to HR_WORDS
    logic [3:0] run_good;    // good slots in a row (training, quiet run over)
    logic [2:0] run_clear;   // bit-8-clear TRAIN words in a row (up)
    logic [6:0] run_any;     // TRAIN words in a row (up)
    logic [2:0] run_hr;      // HOT_RESET words in a row (upstream)
    logic [2:0] run_exit;    // TRAIN words in a row (upstream in Hot Reset)
    logic       open;        // a received packet is open
    logic       open_dllp;   // the open packet is a DLLP
    logic       deliv;       // the open packet's sop was delivered
    logic       discard;     // dropping MID, EOP and ABORT after an error
  } t_link;

  localparam t_link LINK_RESET = '{state: ST_TRAIN, default: '0};

  // The dll replay timeout of the GPIO build (link HAS, replay timeout at
  // LOWER 1), in clk_i cycles, at slot time S = 4 * clk_div: the loaded
  // round trip from a TLP's eop taken by the near adapter to its Ack acting
  // at the near dll, with the far end's clock up to twice as slow (the
  // clock contract's ratio 2), so the far terms count twice. Near: the TLP's
  // slots D*S, a slot-phase wait S - 1, L and the dll's 3 cycles. Far: the
  // dll's 35 cycles to the Ack, the far buffer draining a maximum packet and
  // then the packet the far dll had already started (2*D*S: the Ack waits
  // behind both), a slot-phase wait, the Ack's 3 slots and L. L = 3*S is the
  // MAS bound on W. Clamped to the dll's maximum, 65535 (above CLK_DIV 41).
  localparam int unsigned REPLAY_TIMEOUT_MAX = 65535;

  function automatic int unsigned replay_timeout(input int unsigned clk_div);
    int unsigned s;
    int unsigned near_cycles;
    int unsigned far_cycles;
    s           = 4 * clk_div;
    near_cycles = TX_DEPTH * s + (s - 1) + 3 * s + 3;
    far_cycles  = 35 + 2 * TX_DEPTH * s + (s - 1) + 3 * s + 3 * s;
    return (near_cycles + 2 * far_cycles > REPLAY_TIMEOUT_MAX) ? REPLAY_TIMEOUT_MAX
                                                              : near_cycles + 2 * far_cycles;
  endfunction

endpackage

`default_nettype wire
