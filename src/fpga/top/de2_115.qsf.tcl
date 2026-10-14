# Quartus settings for pcie_top on the Terasic DE2-115.
# Sourced by the project script that ./build fpga generates.
# Pins and I/O standards: the CLOCK_50, push-button, slide-switch and LED
# tables [cite: Terasic DE2-115 User Manual, rev unverified, §4]; the switch,
# key and LED banks run at 2.5 V with jumper JP7 in its default position.

set_global_assignment -name FAMILY "Cyclone IV E"
set_global_assignment -name DEVICE EP4CE115F29C7
set_global_assignment -name RESERVE_ALL_UNUSED_PINS_WEAK_PULLUP "AS INPUT TRI-STATED"
# Registers keep their power-up value of 0; the wrapper's power-on reset
# counter relies on it.
set_global_assignment -name ALLOW_POWER_UP_DONT_CARE OFF

# CLOCK_50
set_location_assignment PIN_Y2 -to clk_50_i
set_instance_assignment -name IO_STANDARD "3.3-V LVTTL" -to clk_50_i

# KEY[2:0]
set_location_assignment PIN_M23 -to key_ni[0]
set_location_assignment PIN_M21 -to key_ni[1]
set_location_assignment PIN_N21 -to key_ni[2]

# SW[7:0]
set_location_assignment PIN_AB28 -to sw_i[0]
set_location_assignment PIN_AC28 -to sw_i[1]
set_location_assignment PIN_AC27 -to sw_i[2]
set_location_assignment PIN_AD27 -to sw_i[3]
set_location_assignment PIN_AB27 -to sw_i[4]
set_location_assignment PIN_AC26 -to sw_i[5]
set_location_assignment PIN_AD26 -to sw_i[6]
set_location_assignment PIN_AB26 -to sw_i[7]

# LEDR[7:0]
set_location_assignment PIN_G19 -to ledr_o[0]
set_location_assignment PIN_F19 -to ledr_o[1]
set_location_assignment PIN_E19 -to ledr_o[2]
set_location_assignment PIN_F21 -to ledr_o[3]
set_location_assignment PIN_F18 -to ledr_o[4]
set_location_assignment PIN_E18 -to ledr_o[5]
set_location_assignment PIN_J19 -to ledr_o[6]
set_location_assignment PIN_H19 -to ledr_o[7]

# LEDG[0]
set_location_assignment PIN_E21 -to ledg0_o

foreach port {key_ni[*] sw_i[*] ledr_o[*] ledg0_o} {
  set_instance_assignment -name IO_STANDARD "2.5 V" -to $port
}

# LED drivers: low current and slow edges are enough for static indicators.
foreach port {ledr_o[*] ledg0_o} {
  set_instance_assignment -name CURRENT_STRENGTH_NEW 8MA -to $port
  set_instance_assignment -name SLEW_RATE 0 -to $port
}
