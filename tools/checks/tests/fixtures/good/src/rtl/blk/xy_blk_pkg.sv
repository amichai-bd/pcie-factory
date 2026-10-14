`default_nettype none

`include "xy_blk_defs.svh"

package xy_blk_pkg;
  localparam int W = `XY_BLK_W;
  typedef logic [W-1:0] t_count;

  function automatic t_count wrap(input t_count x);
    return x;
  endfunction
endpackage

`default_nettype wire
