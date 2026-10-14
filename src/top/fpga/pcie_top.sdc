# Timing constraints for pcie_top on the DE2-115 (wiki/src/top/HAS.md).

# CLOCK_50 (PIN_Y2): the board's 50 MHz oscillator.
create_clock -name CLOCK_50 -period 20.000 [get_ports {clk_50_i}]
derive_clock_uncertainty

# Push buttons and slide switches are asynchronous to CLOCK_50 and enter
# through two-flop synchronizers in the board wrapper; no I/O timing applies.
set_false_path -from [get_ports {key_ni[*] sw_i[*]}]

# The LEDs are static, human-visible outputs; no I/O timing applies.
set_false_path -to [get_ports {ledr_o[*] ledg0_o}]
