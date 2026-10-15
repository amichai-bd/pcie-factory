# Timing constraints for pcie_phy on the DE2-115 (wiki/src/fpga/phy/FPGA.md).

# SMA_CLKIN (PIN_AH14): the core clock and PCLK, clk_i, at 125 MHz.
create_clock -name clk_core -period 8.000 [get_ports {clk_125_i}]
# HSMC_CLKIN0 (PIN_AH15): the recovered RX clock, ser_rx_clk_i, at 125 MHz
# nominal. It comes from another oscillator (within 600 ppm of clk_i), so it
# has no phase relationship to clk_core.
create_clock -name clk_rx -period 8.000 [get_ports {rx_clk_i}]
derive_clock_uncertainty

# Crossings between clk_core and clk_rx. Every such path goes through a
# common_ crossing cell inside pcie_pcs (phy and pcs HAS, Clocks and
# resets): the common_cdc_fifo Gray pointers and memory read (pcs crossing
# 1), the common_cdc_rst asynchronous set from rst_i (crossing 2) and the
# common_cdc_sync2 inputs, alone or inside common_cdc_pulse (crossings 3 to
# 5). The cells' MAS pages ask for a max delay of at most one destination
# period, not a false path or an asynchronous clock group (either would let
# a Gray vector skew past one update), and the clocks have no phase
# relationship, so hold and removal are not checked. Both periods are 8 ns.
set_max_delay -from [get_clocks {clk_core}] -to [get_clocks {clk_rx}] 8.000
set_max_delay -from [get_clocks {clk_rx}] -to [get_clocks {clk_core}] 8.000
set_false_path -hold -from [get_clocks {clk_core}] -to [get_clocks {clk_rx}]
set_false_path -hold -from [get_clocks {clk_rx}] -to [get_clocks {clk_core}]

# The push button and the slide switches are asynchronous to both clocks
# and enter through two-flop synchronizers in the board wrapper; no I/O
# timing applies.
set_false_path -from [get_ports {key0_ni sw_i[*]}]

# The LEDs are static, human-visible outputs; no I/O timing applies.
set_false_path -to [get_ports {ledr_o[*] ledg_o[*]}]
