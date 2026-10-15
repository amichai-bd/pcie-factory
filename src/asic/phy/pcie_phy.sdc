# pcie_phy timing constraints for ./build asic (wiki/bkm/pd.md,
# wiki/src/asic/phy/ASIC.md). Clock targets from wiki/src/arch/phy/HAS.md
# (Targets): clk_i and ser_rx_clk_i both at 125 MHz, 8 ns.
set clk_period    8.0
set clk_port      clk_i
set rx_clk_period 8.0
set rx_clk_port   ser_rx_clk_i

# Core clock (also PIPE PCLK and the TX symbol clock) and the recovered RX
# clock. They come from different references, within +-600 ppm of each other
# (phy HAS, Clocks and resets), so they have no phase relationship.
create_clock -name clk    -period $clk_period    [get_ports $clk_port]
create_clock -name rx_clk -period $rx_clk_period [get_ports $rx_clk_port]
# Pre-CTS margin for jitter and skew; ORFS replaces skew with the real trees.
set_clock_uncertainty 0.25 [get_clocks {clk rx_clk}]

# Boundary budget: inputs arrive and outputs are needed at 20% of the period
# of the clock each port is on (the phy HAS port tables); the neighbouring
# logic is assumed to use the rest. Only ser_rx_data_i and
# ser_rx_elec_idle_i are on ser_rx_clk_i; every other port is on clk_i.
set rx_inputs  [get_ports {ser_rx_data_i* ser_rx_elec_idle_i}]
set clk_inputs [lsearch -inline -all -not -exact [all_inputs] [get_ports $clk_port]]
set clk_inputs [lsearch -inline -all -not -exact $clk_inputs [get_ports $rx_clk_port]]
foreach port $rx_inputs {
  set clk_inputs [lsearch -inline -all -not -exact $clk_inputs $port]
}
set_input_delay  [expr {0.2 * $clk_period}]    -clock clk    $clk_inputs
set_input_delay  [expr {0.2 * $rx_clk_period}] -clock rx_clk $rx_inputs
set_output_delay [expr {0.2 * $clk_period}]    -clock clk    [all_outputs]

# Exceptions: the clock-domain crossings (phy MAS, Timing; all inside
# pcie_pcs through approved common_cdc_* cells). The clocks are asynchronous,
# so a same-edge setup or hold check between them is meaningless. Instead:
# - setup: every crossing path, either direction, is bounded to one period
#   of the clock it is captured by, which bounds the skew between the bits of
#   a synchronized Gray pointer and the elastic-buffer memory read that the
#   pointer guards;
# - hold: none, since there is no phase relationship (the synchronizers
#   resolve the arrival).
set_max_delay $rx_clk_period -from [get_clocks clk]    -to [get_clocks rx_clk]
set_max_delay $clk_period    -from [get_clocks rx_clk] -to [get_clocks clk]
set_false_path -hold -from [get_clocks clk]    -to [get_clocks rx_clk]
set_false_path -hold -from [get_clocks rx_clk] -to [get_clocks clk]
