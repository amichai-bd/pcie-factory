# Timing constraints for pcie_rc on the DE2-115, the board build at LOWER 1
# (wiki/src/fpga/rc/FPGA.md). Synthesis evidence only: the timing results
# are reported for information, not as a gate (rc HAS, Targets).

# CLOCK_50 (PIN_Y2), the board's 50 MHz oscillator: the core clock, clk_i,
# which runs rc_tl, rc_seq, the dll and the GPIO adapter except its receive
# front end.
create_clock -name clk_core -period 20.000 [get_ports {clk_i}]
# GPIO[11] (PIN_AF16): the partner's forwarded clock, gpio_rx_clk_i. The
# partner board is a DE2-115 at 50 MHz with GPIO_CLK_DIV 2, so it runs at
# 25 MHz nominal (gpio HAS, Pins and transfers). It comes from the
# partner's oscillator, so it has no phase relationship to clk_core.
create_clock -name clk_gpio_rx -period 40.000 [get_ports {gpio_rx_clk_i}]
derive_clock_uncertainty

# Crossings between clk_core and clk_gpio_rx. rc_tl, rc_seq and the dll add
# no clock and no crossing (rc HAS, Clocks and reset), so every such path
# goes through a common_ crossing cell inside the adapter's receive front
# end (gpio MAS, Timing): the common_cdc_fifo Gray pointers and memory
# read, the common_cdc_rst asynchronous set from rst_i, and the
# common_cdc_sync2 alignment flag. The cells' MAS pages ask for a max delay
# of at most one capturing period, not a false path or an asynchronous
# clock group (either would let a Gray vector skew past one update), and
# the clocks have no phase relationship, so hold and removal are not
# checked. 20 ns, the shorter period, is at most one capturing period both
# ways.
set_max_delay -from [get_clocks {clk_core}] -to [get_clocks {clk_gpio_rx}] 20.000
set_max_delay -from [get_clocks {clk_gpio_rx}] -to [get_clocks {clk_core}] 20.000
set_false_path -hold -from [get_clocks {clk_core}] -to [get_clocks {clk_gpio_rx}]
set_false_path -hold -from [get_clocks {clk_gpio_rx}] -to [get_clocks {clk_core}]

# Receive pins: source-synchronous to gpio_rx_clk_i. The partner changes
# them as its forwarded clock falls and they are captured as it rises, half
# a transfer later (gpio HAS, Pins and transfers). 0 ns relative to the
# falling edge states that ideal at the header; the cable and board skew
# are not measured (gpio HAS, Open items), so the check covers only the
# FPGA's own pin-to-register skew.
set_input_delay -clock [get_clocks {clk_gpio_rx}] -clock_fall -max 0.000 [get_ports {gpio_rx_ctrl_i gpio_rx_data_i[*]}]
set_input_delay -clock [get_clocks {clk_gpio_rx}] -clock_fall -min 0.000 [get_ports {gpio_rx_ctrl_i gpio_rx_data_i[*]}]

# Transmit pins: flop outputs on clk_i with no logic after the flop, the
# forwarded clock among them (gpio HAS, Pins and transfers). The partner
# times them against the forwarded clock, so their relationship is set by
# the RTL; 0 ns of external delay bounds each register-to-pin path by one
# clk_core period.
set_output_delay -clock [get_clocks {clk_core}] -max 0.000 [get_ports {gpio_tx_clk_o gpio_tx_ctrl_o gpio_tx_data_o[*]}]
set_output_delay -clock [get_clocks {clk_core}] -min 0.000 [get_ports {gpio_tx_clk_o gpio_tx_ctrl_o gpio_tx_data_o[*]}]

# The push buttons are asynchronous to clk_i and enter through two-flop
# synchronizers in the board wrapper; no I/O timing applies.
set_false_path -from [get_ports {key_ni[*]}]

# The LEDs are static, human-visible outputs; no I/O timing applies.
set_false_path -to [get_ports {ledr_o[*] ledg_o[*]}]
