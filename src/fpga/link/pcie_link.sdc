# Timing constraints for pcie_link on the DE2-115 (wiki/src/fpga/link/FPGA.md).

# The FPGA target is 50 MHz on both clocks (link HAS, Targets; M1 Q26): a
# timing and resource target only; the ASIC clock target is separate.
# CLOCK_50 (PIN_Y2), the board's 50 MHz oscillator: the core clock, clk_i,
# which runs all of the dll and the phy except the PCS receive front end.
create_clock -name clk_core -period 20.000 [get_ports {clk_i}]
# HSMC_CLKIN0 (PIN_AH15): the recovered RX clock, ser_rx_clk_i, at 50 MHz
# nominal. It comes from a source off the board (within 600 ppm of clk_i),
# not from the board oscillator, so it has no phase relationship to
# clk_core.
create_clock -name clk_rx -period 20.000 [get_ports {rx_clk_i}]
derive_clock_uncertainty

# Crossings between clk_core and clk_rx. The dll adds no clock and no
# crossing (link HAS, Clocks and resets), so every such path goes through a
# common_ crossing cell inside pcie_pcs: the common_cdc_fifo Gray pointers
# and memory read (pcs crossing 1), the common_cdc_rst asynchronous set
# from rst_i (crossing 2) and the common_cdc_sync2 inputs, alone or inside
# common_cdc_pulse (crossings 3 to 5). The cells' MAS pages ask for a max
# delay of at most one destination period, not a false path or an
# asynchronous clock group (either would let a Gray vector skew past one
# update), and the clocks have no phase relationship, so hold and removal
# are not checked. Both periods are 20 ns.
set_max_delay -from [get_clocks {clk_core}] -to [get_clocks {clk_rx}] 20.000
set_max_delay -from [get_clocks {clk_rx}] -to [get_clocks {clk_core}] 20.000
set_false_path -hold -from [get_clocks {clk_core}] -to [get_clocks {clk_rx}]
set_false_path -hold -from [get_clocks {clk_rx}] -to [get_clocks {clk_core}]

# The push button and the slide switches are asynchronous to both clocks
# and enter through two-flop synchronizers in the board wrapper; no I/O
# timing applies.
set_false_path -from [get_ports {key0_ni sw_i[*]}]

# The LEDs are static, human-visible outputs; no I/O timing applies.
set_false_path -to [get_ports {ledr_o[*] ledg_o[*]}]
