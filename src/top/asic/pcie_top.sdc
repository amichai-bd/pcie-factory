# pcie_top timing constraints for ./build asic (wiki/bkm/pd.md).
# Clock target from wiki/src/top/asic/ASIC.md (Clock target): 100 MHz, 10 ns.
set clk_period 10.0
set clk_port   clk_i

create_clock -name clk -period $clk_period [get_ports $clk_port]
# Pre-CTS margin for jitter and skew; ORFS replaces skew with the real tree.
set_clock_uncertainty 0.25 [get_clocks clk]

# Boundary budget: inputs arrive and outputs are needed at 20% of the period
# (the neighbouring logic is assumed to use the rest).
set non_clk_inputs [lsearch -inline -all -not -exact [all_inputs] [get_ports $clk_port]]
set_input_delay  [expr {0.2 * $clk_period}] -clock clk $non_clk_inputs
set_output_delay [expr {0.2 * $clk_period}] -clock clk [all_outputs]

# Exceptions: none.
