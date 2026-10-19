// pcie_rc_tl package: the Type 1 header state the blocks share, the
// header access, the front end's state and the command checks
// (wiki/src/rtl/rc_tl/MAS.md#leaf-blocks). Helper functions take plain
// vectors: the t_rc_cmd members len and last are read outside them (MAS,
// Interfaces).
`default_nettype none

package pcie_rc_tl_pkg;

  // ------------------------------------------------------------ header

  // The header state the other blocks route by, from the header's flops
  // (MAS, Type 1 header).
  typedef struct packed {
    logic        mse;        // Memory Space Enable
    logic        bme;        // Bus Master Enable
    logic [7:0]  pri_bus;    // Primary Bus Number
    logic [7:0]  sec_bus;    // Secondary Bus Number
    logic [11:0] mem_base;   // Memory Base [15:4]: address [31:20] of the window's first byte
    logic [11:0] mem_limit;  // Memory Limit [15:4]: address [31:20] of the window's last 1 MiB
  } t_hdr_st;

  // A header access from the front end: the DW the read data is for, and
  // a write at this edge with its byte enables.
  typedef struct packed {
    logic        wr;
    logic [9:0]  idx;
    logic [3:0]  be;
    logic [31:0] data;
  } t_hdr_acc;

  // ------------------------------------------------------------ front end

  // The front end's state (MAS, Commands): a command in progress, from the
  // edge that takes it to its last result beat, and the beat out this
  // cycle (rsp 0 between beats).
  typedef struct packed {
    logic                busy;
    logic                beat;
    common_pkg::t_rc_rsp rsp;
  } t_fe_st;

  // ------------------------------------------------------------ helpers

  // The bits of `old` that `rw` marks, in the bytes `be` enables, take `data`.
  function automatic logic [31:0] wr_rw(input logic [31:0] old, input logic [31:0] data,
                                        input logic [3:0] be, input logic [31:0] rw);
    logic [31:0] m;
    m = {{8{be[3]}}, {8{be[2]}}, {8{be[1]}}, {8{be[0]}}} & rw;
    wr_rw = (old & ~m) | (data & m);
  endfunction

  // An endpoint command: CFG_* or MEM_*.
  function automatic logic is_ep(input logic [3:0] op);
    is_ep = (op == common_pkg::RC_OP_CFG_RD) || (op == common_pkg::RC_OP_CFG_WR)
         || (op == common_pkg::RC_OP_MEM_RD) || (op == common_pkg::RC_OP_MEM_WR);
  endfunction

  // Check 2: a defined operation.
  function automatic logic op_ok(input logic [3:0] op);
    op_ok = (op <= 4'(common_pkg::RC_OP_ERR_CLR));
  endfunction

  // Check 3: len 1 to 32 for MEM_RD, 1 otherwise.
  function automatic logic len_ok(input logic [3:0] op, input logic [5:0] n);
    if (op == common_pkg::RC_OP_MEM_RD) begin
      len_ok = (n >= 6'd1) && (n <= 6'd32);
    end else begin
      len_ok = (n == 6'd1);
    end
  endfunction

  // Check 4: the address bits outside the op's fields are 0. host_mask
  // marks the bits at and above HOST_MEM_ADDR_W.
  function automatic logic addr_ok(input logic [3:0] op, input logic [31:0] addr,
                                   input logic [31:0] host_mask);
    logic [31:0] m;
    case (op)
      common_pkg::RC_OP_CFG_RD, common_pkg::RC_OP_CFG_WR:   m = 32'hF000_0003;
      common_pkg::RC_OP_MEM_RD, common_pkg::RC_OP_MEM_WR:   m = 32'h0000_0003;
      common_pkg::RC_OP_HDR_RD, common_pkg::RC_OP_HDR_WR:   m = 32'hFFFF_F003;
      common_pkg::RC_OP_HOST_RD, common_pkg::RC_OP_HOST_WR: m = host_mask | 32'h0000_0003;
      default:                                              m = 32'h0000_0000;
    endcase
    addr_ok = ((addr & m) == 32'd0);
  endfunction

  // Check 5: a MEM_RD stays inside one aligned 4 KiB block.
  function automatic logic rd_4k_ok(input logic [3:0] op, input logic [9:0] dw,
                                    input logic [5:0] n);
    rd_4k_ok = (op != common_pkg::RC_OP_MEM_RD) || ((11'(dw) + 11'(n)) <= 11'd1024);
  endfunction

  // Check 6: legal byte enables (REQUESTER.md, Byte enables). a2 is addr[2].
  function automatic logic be_ok(input logic [3:0] op, input logic [5:0] n, input logic a2,
                                 input logic [3:0] fbe, input logic [3:0] lbe);
    logic contig;
    contig = ((fbe == 4'b1111) || (fbe == 4'b1110) || (fbe == 4'b1100) || (fbe == 4'b1000))
          && ((lbe == 4'b0001) || (lbe == 4'b0011) || (lbe == 4'b0111) || (lbe == 4'b1111));
    if (n == 6'd1) begin
      be_ok = (lbe == 4'd0);
      if ((op == common_pkg::RC_OP_CFG_RD) || (op == common_pkg::RC_OP_CFG_WR)
          || (op == common_pkg::RC_OP_MEM_WR)) begin
        be_ok = be_ok && (fbe != 4'd0);
      end
    end else begin
      // Only a MEM_RD passes check 3 with len above 1.
      be_ok = (fbe != 4'd0) && (lbe != 4'd0) && (contig || ((n == 6'd2) && !a2));
    end
  endfunction

  // Check 7: outbound routing by the header state. A configuration request
  // goes to bus Secondary Bus Number, device 0; a memory request needs
  // Memory Space Enable and addr[31:20] inside the memory window. a is
  // addr[31:15]; the routing reads only the enables, the Secondary Bus
  // Number and the window of the header state.
  function automatic logic route_ok(input logic [3:0] op, input logic [16:0] a,
                                    input logic mse, input logic [7:0] sec_bus,
                                    input logic [11:0] base, input logic [11:0] limit);
    case (op)
      common_pkg::RC_OP_CFG_RD, common_pkg::RC_OP_CFG_WR:
        route_ok = (a[12:5] == sec_bus) && (a[4:0] == 5'd0);
      common_pkg::RC_OP_MEM_RD, common_pkg::RC_OP_MEM_WR:
        route_ok = mse && (a[16:5] >= base) && (a[16:5] <= limit);
      default:
        route_ok = 1'b1;
    endcase
  endfunction

endpackage

`default_nettype wire
