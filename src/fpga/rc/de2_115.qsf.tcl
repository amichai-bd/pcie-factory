# Quartus settings for pcie_rc on the Terasic DE2-115, the board build
# (LOWER 1). Sourced by the project script that ./build fpga generates.
# Pins and I/O standards: the clock-input, push-button, LED and expansion
# header tables [cite: Terasic DE2-115 User Manual, rev unverified, §4].
# CLOCK_50 is a 3.3 V input:
# [cite: Terasic DE2-115 User Manual, rev unverified, §4.5]
# The expansion header's I/O voltage follows jumper JP6 (3.3 V by default):
# [cite: Terasic DE2-115 User Manual, rev unverified, §4.8]
# The key and LED banks run at 2.5 V with jumper JP7 in its default
# position.

set_global_assignment -name FAMILY "Cyclone IV E"
set_global_assignment -name DEVICE EP4CE115F29C7
set_global_assignment -name RESERVE_ALL_UNUSED_PINS_WEAK_PULLUP "AS INPUT TRI-STATED"
# Registers keep their power-up value of 0; the wrapper's power-on reset
# counter relies on it.
set_global_assignment -name ALLOW_POWER_UP_DONT_CARE OFF

# CLOCK_50: core clock, from the board's 50 MHz oscillator.
set_location_assignment PIN_Y2 -to clk_i
set_instance_assignment -name IO_STANDARD "3.3-V LVTTL" -to clk_i

# KEY[1:0]
set_location_assignment PIN_M23 -to key_ni[0]
set_location_assignment PIN_M21 -to key_ni[1]

# LEDR[15:0]
set_location_assignment PIN_G19 -to ledr_o[0]
set_location_assignment PIN_F19 -to ledr_o[1]
set_location_assignment PIN_E19 -to ledr_o[2]
set_location_assignment PIN_F21 -to ledr_o[3]
set_location_assignment PIN_F18 -to ledr_o[4]
set_location_assignment PIN_E18 -to ledr_o[5]
set_location_assignment PIN_J19 -to ledr_o[6]
set_location_assignment PIN_H19 -to ledr_o[7]
set_location_assignment PIN_J17 -to ledr_o[8]
set_location_assignment PIN_G17 -to ledr_o[9]
set_location_assignment PIN_J15 -to ledr_o[10]
set_location_assignment PIN_H16 -to ledr_o[11]
set_location_assignment PIN_J16 -to ledr_o[12]
set_location_assignment PIN_H17 -to ledr_o[13]
set_location_assignment PIN_F15 -to ledr_o[14]
set_location_assignment PIN_G15 -to ledr_o[15]

# LEDG[3:0]
set_location_assignment PIN_E21 -to ledg_o[0]
set_location_assignment PIN_E22 -to ledg_o[1]
set_location_assignment PIN_E25 -to ledg_o[2]
set_location_assignment PIN_E24 -to ledg_o[3]

foreach port {key_ni[*] ledr_o[*] ledg_o[*]} {
  set_instance_assignment -name IO_STANDARD "2.5 V" -to $port
}

# LED drivers: low current and slow edges are enough for static indicators.
foreach port {ledr_o[*] ledg_o[*]} {
  set_instance_assignment -name CURRENT_STRENGTH_NEW 8MA -to $port
  set_instance_assignment -name SLEW_RATE 0 -to $port
}

# Expansion header GPIO[11:0], header pins 1 to 10, 13 and 14: the adapter's
# 12 pins as the gpio HAS (DE2-115 pins) maps them for the downstream board.
set_location_assignment PIN_AB22 -to gpio_tx_data_o[0]
set_location_assignment PIN_AC15 -to gpio_tx_data_o[1]
set_location_assignment PIN_AB21 -to gpio_tx_data_o[2]
set_location_assignment PIN_Y17  -to gpio_tx_data_o[3]
set_location_assignment PIN_AC21 -to gpio_tx_ctrl_o
set_location_assignment PIN_Y16  -to gpio_rx_data_i[0]
set_location_assignment PIN_AD21 -to gpio_rx_data_i[1]
set_location_assignment PIN_AE16 -to gpio_rx_data_i[2]
set_location_assignment PIN_AD15 -to gpio_rx_data_i[3]
set_location_assignment PIN_AE15 -to gpio_tx_clk_o
set_location_assignment PIN_AC19 -to gpio_rx_ctrl_i
set_location_assignment PIN_AF16 -to gpio_rx_clk_i

foreach port {gpio_tx_clk_o gpio_tx_ctrl_o gpio_tx_data_o[*] gpio_rx_clk_i gpio_rx_ctrl_i gpio_rx_data_i[*]} {
  set_instance_assignment -name IO_STANDARD "3.3-V LVTTL" -to $port
}

# Header outputs: an explicit drive and slew, as for the LEDs (Quartus warns
# on an output with neither). 8 mA and the device's fast slew keep the
# edges well inside the 20 ns half transfer at GPIO_CLK_DIV 2; the cable is
# not measured (gpio HAS, Open items).
foreach port {gpio_tx_clk_o gpio_tx_ctrl_o gpio_tx_data_o[*]} {
  set_instance_assignment -name CURRENT_STRENGTH_NEW 8MA -to $port
  set_instance_assignment -name SLEW_RATE 2 -to $port
}
