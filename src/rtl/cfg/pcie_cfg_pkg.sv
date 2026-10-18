// pcie_cfg package: the register map constants, the structs the blocks
// exchange and the pure helpers (wiki/src/rtl/cfg/MAS.md#package). The map
// is wiki/src/arch/cfg/REGISTERS.md and CAPS.md.
`default_nettype none

package pcie_cfg_pkg;

  // ------------------------------------------------------------ map
  // DW indices (byte offset / 4) of the configuration DWs this design
  // implements; every other DW reads 0 and ignores writes.
  localparam logic [9:0] DW_ID      = 10'h000;  // 0x00 Vendor and Device ID
  localparam logic [9:0] DW_CMD     = 10'h001;  // 0x04 Command and Status
  localparam logic [9:0] DW_CLASS   = 10'h002;  // 0x08 Revision ID and Class Code
  localparam logic [9:0] DW_CLS     = 10'h003;  // 0x0C Cache Line Size, Header Type
  localparam logic [9:0] DW_BAR0    = 10'h004;  // 0x10
  localparam logic [9:0] DW_BAR2    = 10'h006;  // 0x18
  localparam logic [9:0] DW_SUBSYS  = 10'h00B;  // 0x2C
  localparam logic [9:0] DW_CAPPTR  = 10'h00D;  // 0x34
  localparam logic [9:0] DW_INTLINE = 10'h00F;  // 0x3C
  localparam logic [9:0] DW_PM_CAP  = 10'h010;  // 0x40 Power Management
  localparam logic [9:0] DW_PM_CSR  = 10'h011;  // 0x44
  localparam logic [9:0] DW_MSI_CTL = 10'h014;  // 0x50 MSI
  localparam logic [9:0] DW_MSI_LO  = 10'h015;  // 0x54
  localparam logic [9:0] DW_MSI_HI  = 10'h016;  // 0x58
  localparam logic [9:0] DW_MSI_DAT = 10'h017;  // 0x5C
  localparam logic [9:0] DW_MSIX    = 10'h018;  // 0x60 MSI-X
  localparam logic [9:0] DW_MSIX_TB = 10'h019;  // 0x64
  localparam logic [9:0] DW_MSIX_PB = 10'h01A;  // 0x68
  localparam logic [9:0] DW_PCIE    = 10'h01C;  // 0x70 PCI Express
  localparam logic [9:0] DW_DEVCAP  = 10'h01D;  // 0x74
  localparam logic [9:0] DW_DEVCTL  = 10'h01E;  // 0x78 Device Control and Status
  localparam logic [9:0] DW_LNKCAP  = 10'h01F;  // 0x7C
  localparam logic [9:0] DW_LNKCTL  = 10'h020;  // 0x80 Link Control and Status
  localparam logic [9:0] DW_DEVCAP2 = 10'h025;  // 0x94
  localparam logic [9:0] DW_DEVCTL2 = 10'h026;  // 0x98
  localparam logic [9:0] DW_LNKCAP2 = 10'h027;  // 0x9C
  localparam logic [9:0] DW_LNKCTL2 = 10'h028;  // 0xA0

  // BAR2: the pending-bit array DWs (byte offset 0x800).
  localparam logic [9:0] DW_PBA0    = 10'h200;
  localparam logic [9:0] DW_PBA1    = 10'h201;

  // Read-only DW values (REGISTERS.md, CAPS.md). The parameter-dependent
  // ones (IDs, Table Size, BAR0 mask) are built in pcie_cfg_regs.
  localparam logic [31:0] VAL_CAPPTR  = 32'h0000_0040;
  localparam logic [31:0] VAL_PM_CAP  = 32'h0003_5001;  // version 3, next 0x50, ID 0x01
  localparam logic [31:0] VAL_PM_CSR  = 32'h0000_0008;  // No_Soft_Reset
  localparam logic [31:0] VAL_MSI_CTL = 32'h0080_6005;  // 64-bit capable, next 0x60, ID 0x05
  localparam logic [31:0] VAL_MSIX    = 32'h0000_7011;  // next 0x70, ID 0x11 (Table Size added)
  localparam logic [31:0] VAL_MSIX_TB = 32'h0000_0002;  // table at BAR2 + 0
  localparam logic [31:0] VAL_MSIX_PB = 32'h0000_0802;  // PBA at BAR2 + 0x800
  localparam logic [31:0] VAL_PCIE    = 32'h0002_0010;  // version 2, Endpoint, ID 0x10, last
  localparam logic [31:0] VAL_DEVCAP  = 32'h0000_8000;  // Role-Based Error Reporting
  localparam logic [31:0] VAL_LNKCAP  = 32'h0040_0011;  // 2.5 GT/s, x1, ASPM Optionality
  localparam logic [31:0] VAL_LNKSTS  = 32'h0011_0000;  // Current speed 1, width 1
  localparam logic [31:0] VAL_DEVCAP2 = 32'h0000_0010;  // Completion Timeout Disable Supported
  localparam logic [31:0] VAL_LNKCAP2 = 32'h0000_0002;  // 2.5 GT/s
  localparam logic [31:0] VAL_LNKCTL2 = 32'h0000_0001;  // Target Link Speed 2.5 GT/s
  localparam logic [31:0] VAL_STS_CAP = 32'h0010_0000;  // Status Capabilities List

  // RW masks of the DWs with writable bits, and their reset values.
  localparam logic [31:0] RW_CMD      = 32'h0000_0546;  // MSE, BME, PER, SERR#, Interrupt Disable
  localparam logic [31:0] RW_BYTE0    = 32'h0000_00FF;  // Cache Line Size, Interrupt Line
  localparam logic [31:0] RW_BAR2     = 32'hFFFF_F000;  // 4 KiB
  localparam logic [31:0] RW_PM_CSR   = 32'h0000_0003;  // PowerState
  localparam logic [31:0] RW_MSI_CTL  = 32'h0071_0000;  // MSI Enable, Multiple Message Enable
  localparam logic [31:0] RW_MSI_LO   = 32'hFFFF_FFFC;
  localparam logic [31:0] RW_ALL      = 32'hFFFF_FFFF;
  localparam logic [31:0] RW_MSI_DAT  = 32'h0000_FFFF;
  localparam logic [31:0] RW_MSIX     = 32'hC000_0000;  // MSI-X Enable, Function Mask
  localparam logic [31:0] RW_DEVCTL   = 32'h0000_78FF;
  localparam logic [31:0] RST_DEVCTL  = 32'h0000_2810;  // Relaxed Ordering, No Snoop, MRRS 2
  localparam logic [31:0] RW_LNKCTL   = 32'h0000_00C3;  // ASPM Control, CCC, Extended Synch
  localparam logic [31:0] RW_DEVCTL2  = 32'h0000_0010;  // Completion Timeout Disable

  // Power states (d_state_o).
  localparam logic [1:0] D0    = 2'd0;
  localparam logic [1:0] D3HOT = 2'd3;

  // msg_kind_o codes (TL_CFG, Messages).
  localparam logic [1:0] MSG_IRQ          = 2'd0;
  localparam logic [1:0] MSG_ERR_COR      = 2'd1;
  localparam logic [1:0] MSG_ERR_NONFATAL = 2'd2;
  localparam logic [1:0] MSG_ERR_FATAL    = 2'd3;

  // irq_status_o codes (APP, Interrupts).
  localparam logic [1:0] IRQ_SENT    = 2'd0;
  localparam logic [1:0] IRQ_PENDING = 2'd1;
  localparam logic [1:0] IRQ_DROPPED = 2'd2;

  // ------------------------------------------------------------ types

  // The register state the other blocks use, from pcie_cfg_regs: once as
  // the registers hold it (_q), once as they hold it after this edge's
  // write (_d).
  typedef struct packed {
    logic        mse;          // Command Memory Space Enable
    logic        bme;          // Command Bus Master Enable
    logic        per;          // Command Parity Error Response
    logic        serr;         // Command SERR# Enable
    logic [1:0]  d_state;      // PowerState
    logic        msi_en;       // MSI Enable
    logic [63:0] msi_addr;     // MSI Message Address, bits 1:0 0
    logic [15:0] msi_data;     // MSI Message Data
    logic        msix_en;      // MSI-X Enable
    logic        msix_fmask;   // MSI-X Function Mask
    logic        cere;         // Device Control: Correctable Error Reporting Enable
    logic        nfere;        // Non-Fatal Error Reporting Enable
    logic        fere;         // Fatal Error Reporting Enable
    logic        urre;         // Unsupported Request Reporting Enable
  } t_ctrl;

  // Status bits set at one edge (RW1C), from the error block and the
  // message block (Signaled System Error).
  typedef struct packed {
    logic dpe;                 // Status Detected Parity Error
    logic sse;                 // Status Signaled System Error
    logic rma;                 // Status Received Master Abort
    logic rta;                 // Status Received Target Abort
    logic mdpe;                // Status Master Data Parity Error
    logic urd;                 // Device Status Unsupported Request Detected
    logic fed;                 // Device Status Fatal Error Detected
    logic nfed;                // Device Status Non-Fatal Error Detected
    logic ced;                 // Device Status Correctable Error Detected
  } t_sts;

  // One due flag per error message kind (HAS, Messages).
  typedef struct packed {
    logic fatal;
    logic nonfatal;
    logic cor;
  } t_err_due;

  // What an offer came from, and so what its take clears.
  typedef enum logic [1:0] {
    SRC_ERR      = 2'd0,       // an error message: its due flag
    SRC_APP      = 2'd1,       // the application's request: answered sent
    SRC_PEND_MSI = 2'd2,       // the MSI pending flag
    SRC_PEND_X   = 2'd3        // the pending bit of vector `vector`
  } t_src;

  // The message offered on msg_*.
  typedef struct packed {
    logic [1:0]  kind;         // msg_kind_o
    logic [63:0] addr;         // msg_addr_o
    logic [31:0] data;         // msg_data_o
    t_src        src;
    logic        msix;         // an interrupt write under MSI-X (else MSI)
    logic [5:0]  vector;       // its MSI-X vector
  } t_offer;

  // The application's request held from its take to its done.
  typedef struct packed {
    logic       msix;          // resolved under MSI-X (else MSI)
    logic [5:0] vector;        // irq_vector_i as taken
  } t_app;

  // An MSI-X table entry as the RAM holds it: three DWs, one word.
  typedef struct packed {
    logic [31:0] data;         // Message Data
    logic [31:0] addr_hi;      // Message Upper Address
    logic [31:0] addr_lo;      // Message Address, bits 1:0 0
  } t_entry;

  // ------------------------------------------------------------ helpers

  // The bit mask of the enabled bytes of a DW.
  function automatic logic [31:0] be_mask(input logic [3:0] be);
    be_mask = {{8{be[3]}}, {8{be[2]}}, {8{be[1]}}, {8{be[0]}}};
  endfunction

  // A configuration write of the RW bits `rw` in the enabled bytes.
  function automatic logic [31:0] wr_rw(input logic [31:0] old, input logic [31:0] data,
                                        input logic [3:0] be, input logic [31:0] rw);
    logic [31:0] m;
    m = be_mask(be) & rw;
    wr_rw = (old & ~m) | (data & m);
  endfunction

  // The RW1C bits `w1c` written 1 in the enabled bytes: the bits to clear.
  function automatic logic [31:0] wr_w1c(input logic [31:0] data, input logic [3:0] be,
                                         input logic [31:0] w1c);
    wr_w1c = data & be_mask(be) & w1c;
  endfunction

  // A write taken at this edge to configuration DW `dw`.
  function automatic logic hit(input logic wr, input logic [9:0] addr, input logic [9:0] dw);
    hit = wr && (addr == dw);
  endfunction



endpackage

`default_nettype wire
