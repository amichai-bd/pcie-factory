# pcie_core timing constraints for ./build asic (wiki/bkm/pd.md,
# wiki/src/asic/core/ASIC.md). Clock targets from wiki/src/arch/core/HAS.md
# (Clocks, Targets): clk_i and ser_rx_clk_i both at 125 MHz, 8 ns, the default
# and nightly target; the M3 gate is the 10 ns study below (M1 Q27).
set clk_period    8.0
set clk_port      clk_i
set rx_clk_period 8.0
set rx_clk_port   ser_rx_clk_i

# build-asic: study-clock-period
# Explicit studies override both domains; normal/nightly runs keep 8 ns.
if {[info exists study_clock_period_ns]} {
  set clk_period    $study_clock_period_ns
  set rx_clk_period $study_clock_period_ns
}

# Core clock (tl, cfg, all of the dll, the MAC, PIPE, the PCS transmit path
# and the elastic buffer's read side) and the recovered RX clock (the PCS
# receive front end). They come from different references, within +-600 ppm
# of each other (link HAS, Clocks and resets), so they have no phase
# relationship.
create_clock -name clk    -period $clk_period    [get_ports $clk_port]
create_clock -name rx_clk -period $rx_clk_period [get_ports $rx_clk_port]
# Pre-CTS margin for jitter and skew; ORFS replaces skew with the real trees.
set_clock_uncertainty 0.25 [get_clocks {clk rx_clk}]

# Boundary budget: inputs arrive and outputs are needed at 20% of the period
# of the clock each port is on (the core HAS port tables, and the cdc-domain
# annotations of pcie_core.sv); the neighbouring logic is assumed to use the
# rest. Only ser_rx_data_i and ser_rx_elec_idle_i are on ser_rx_clk_i; every
# other port is on clk_i.
set rx_inputs  [get_ports {ser_rx_data_i* ser_rx_elec_idle_i}]
set clk_inputs [lsearch -inline -all -not -exact [all_inputs] [get_ports $clk_port]]
set clk_inputs [lsearch -inline -all -not -exact $clk_inputs [get_ports $rx_clk_port]]
foreach port $rx_inputs {
  set clk_inputs [lsearch -inline -all -not -exact $clk_inputs $port]
}
set_input_delay  [expr {0.2 * $clk_period}]    -clock clk    $clk_inputs
set_input_delay  [expr {0.2 * $rx_clk_period}] -clock rx_clk $rx_inputs
set_output_delay [expr {0.2 * $clk_period}]    -clock clk    [all_outputs]

# Exceptions: the clock-domain crossings, as for pcie_link
# (src/asic/link/pcie_link.sdc). tl and cfg add no clock and no crossing
# (core HAS, Clocks), so every crossing is inside pcie_pcs, through approved
# common_cdc_* cells. The clocks are asynchronous, so a same-edge setup or
# hold check between them is meaningless. Instead:
# - setup: every crossing path, either direction, is bounded to one period
#   of the clock it is captured by, which bounds the skew between the bits of
#   a synchronized Gray pointer and the elastic-buffer memory read that the
#   pointer guards;
#   -ignore_clock_latency (an OpenSTA set_max_delay option, as in SDC 2.1;
#   OpenSTA's doc/OpenSTA.pdf) leaves both clock trees out, so the bound is on
#   the data path alone, the same for every bit whatever the tree skew;
# - hold: none, since there is no phase relationship (the synchronizers
#   resolve the arrival).
# The exception is per clock pair, so it would also bound an unsynchronized
# crossing; the lint and cdc-domain checks of ./build check are the guard.
set_max_delay -ignore_clock_latency $rx_clk_period -from [get_clocks clk]    -to [get_clocks rx_clk]
set_max_delay -ignore_clock_latency $clk_period    -from [get_clocks rx_clk] -to [get_clocks clk]
set_false_path -hold -from [get_clocks clk]    -to [get_clocks rx_clk]
set_false_path -hold -from [get_clocks rx_clk] -to [get_clocks clk]
