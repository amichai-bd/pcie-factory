`default_nettype none

package xy_blk_pkg;
  localparam int W = 8;
  typedef logic [W-1:0] t_count;

  function automatic t_count wrap(input t_count x);
    return x;
  endfunction
endpackage

`default_nettype wire
