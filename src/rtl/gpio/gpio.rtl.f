// pcie_gpio: synthesizable sources only (wiki/src/rtl/gpio/MAS.md#files).
// The vocabulary, the crossing cells and the transmit buffer's RAM queue,
// the IP package, the blocks, then the top.
-F ../common/common_macros.rtl.f
-F ../common/cdc/common_cdc_rst.rtl.f
-F ../common/cdc/common_cdc_sync2.rtl.f
-F ../common/cdc/common_cdc_fifo.rtl.f
-F ../common/ram/common_sdp_ram_fifo.rtl.f
pcie_gpio_pkg.sv
pcie_gpio_tx.sv
pcie_gpio_rx_front.sv
pcie_gpio_link.sv
pcie_gpio.sv
