# Quartus settings for pcie_link on the Terasic DE2-115.
# Sourced by the project script that ./build fpga generates.
# Pins and I/O standards: the clock-input, HSMC, push-button, slide-switch
# and LED tables [cite: Terasic DE2-115 User Manual, rev unverified, §4].
# CLOCK_50 is a 3.3 V input:
# [cite: Terasic DE2-115 User Manual, rev unverified, §4.5]
# HSMC_CLKIN0 follows jumper JP6 (3.3 V by default):
# [cite: Terasic DE2-115 User Manual, rev unverified, §4.7]
# The switch, key and LED banks run at 2.5 V with jumper JP7 in its default
# position.

set_global_assignment -name FAMILY "Cyclone IV E"
set_global_assignment -name DEVICE EP4CE115F29C7
set_global_assignment -name RESERVE_ALL_UNUSED_PINS_WEAK_PULLUP "AS INPUT TRI-STATED"
# Registers keep their power-up value of 0; the wrapper's power-on reset
# counter and its stimulus shift registers rely on it.
set_global_assignment -name ALLOW_POWER_UP_DONT_CARE OFF

# CLOCK_50: core clock, from the board's 50 MHz oscillator. CLOCK2_50 and
# CLOCK3_50 are buffered from the same oscillator, so none of the three can
# be the RX clock.
set_location_assignment PIN_Y2 -to clk_i
# HSMC_CLKIN0: recovered RX clock, from an independent source off the board
set_location_assignment PIN_AH15 -to rx_clk_i
foreach port {clk_i rx_clk_i} {
  set_instance_assignment -name IO_STANDARD "3.3-V LVTTL" -to $port
}

# KEY[0]
set_location_assignment PIN_M23 -to key0_ni

# SW[2:0]
set_location_assignment PIN_AB28 -to sw_i[0]
set_location_assignment PIN_AC28 -to sw_i[1]
set_location_assignment PIN_AC27 -to sw_i[2]

# LEDR[5:0]
set_location_assignment PIN_G19 -to ledr_o[0]
set_location_assignment PIN_F19 -to ledr_o[1]
set_location_assignment PIN_E19 -to ledr_o[2]
set_location_assignment PIN_F21 -to ledr_o[3]
set_location_assignment PIN_F18 -to ledr_o[4]
set_location_assignment PIN_E18 -to ledr_o[5]

# LEDG[1:0]
set_location_assignment PIN_E21 -to ledg_o[0]
set_location_assignment PIN_E22 -to ledg_o[1]

foreach port {key0_ni sw_i[*] ledr_o[*] ledg_o[*]} {
  set_instance_assignment -name IO_STANDARD "2.5 V" -to $port
}

# LED drivers: low current and slow edges are enough for static indicators.
foreach port {ledr_o[*] ledg_o[*]} {
  set_instance_assignment -name CURRENT_STRENGTH_NEW 8MA -to $port
  set_instance_assignment -name SLEW_RATE 0 -to $port
}
