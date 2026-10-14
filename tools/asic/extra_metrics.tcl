# Extra metrics ORFS does not report: max fanout and logic depth of the
# final design. Run by ./build asic through the ORFS `run` target, so the
# ORFS environment (SCRIPTS_DIR, RESULTS_DIR, REPORTS_DIR, platform files) is
# set. Writes $REPORTS_DIR/asic_extra.json. Spec: wiki/tools/asic/SPEC.md.
#
# Max fanout: the largest number of input pins (plus output ports) on one
#   data net. Nets that reach a register clock pin are excluded.
# Logic depth: the largest number of cells on any path that ends at a
#   register data pin or an output port and starts at a register or an
#   input port (both endpoints excluded, buffers and inverters included).

source $::env(SCRIPTS_DIR)/load.tcl
load_design 6_final.odb 6_final.sdc

interp recursionlimit {} 100000

# Registers end the backward walk; record them by full name.
array set ::asic_regs {}
foreach cell [all_registers -cells] {
  set ::asic_regs([get_full_name $cell]) 1
}

# Nets connected to a register clock pin carry the clock, not data.
array set ::asic_clock_nets {}
foreach pin [all_registers -clock_pins] {
  foreach net [get_nets -quiet -of_objects $pin] {
    set ::asic_clock_nets([get_full_name $net]) 1
  }
}

# --- max fanout ---
set max_fanout 0
set max_fanout_net ""
foreach net [get_nets -quiet *] {
  set name [get_full_name $net]
  if { [info exists ::asic_clock_nets($name)] } {
    continue
  }
  set fanout [llength [get_pins -quiet -of_objects $net -filter "direction == input"]]
  incr fanout [llength [get_ports -quiet -of_objects $net -filter "direction == output"]]
  if { $fanout > $max_fanout } {
    set max_fanout $fanout
    set max_fanout_net $name
  }
}

# --- logic depth ---
array set ::asic_depth {}

# Depth of a cell: 0 for a register, else 1 + the depth of its deepest input.
proc asic_cell_depth { cell } {
  set name [get_full_name $cell]
  if { [info exists ::asic_regs($name)] } {
    return 0
  }
  if { [info exists ::asic_depth($name)] } {
    return $::asic_depth($name)
  }
  # A combinational loop would recurse forever; break it at depth 0.
  set ::asic_depth($name) 0
  set depth 0
  foreach pin [get_pins -quiet -of_objects $cell -filter "direction == input"] {
    set d [asic_pin_depth $pin]
    if { $d > $depth } {
      set depth $d
    }
  }
  set ::asic_depth($name) [expr { $depth + 1 }]
  return $::asic_depth($name)
}

# Depth seen at a pin or port: the depth of the cells driving its net.
proc asic_pin_depth { obj } {
  set depth 0
  foreach net [get_nets -quiet -of_objects $obj] {
    foreach drv [get_pins -quiet -of_objects $net -filter "direction == output"] {
      set d [asic_cell_depth [get_cells -of_objects $drv]]
      if { $d > $depth } {
        set depth $d
      }
    }
  }
  return $depth
}

set logic_depth 0
set logic_depth_endpoint ""
foreach pin [all_registers -data_pins] {
  set d [asic_pin_depth $pin]
  if { $d > $logic_depth } {
    set logic_depth $d
    set logic_depth_endpoint [get_full_name $pin]
  }
}
foreach port [all_outputs] {
  set d [asic_pin_depth $port]
  if { $d > $logic_depth } {
    set logic_depth $d
    set logic_depth_endpoint [get_full_name $port]
  }
}

proc asic_json_string { s } {
  return "\"[string map {\\ \\\\ \" \\\"} $s]\""
}

set out [open $::env(REPORTS_DIR)/asic_extra.json w]
puts $out "{"
puts $out "  \"max_fanout\": $max_fanout,"
puts $out "  \"max_fanout_net\": [asic_json_string $max_fanout_net],"
puts $out "  \"logic_depth\": $logic_depth,"
puts $out "  \"logic_depth_endpoint\": [asic_json_string $logic_depth_endpoint]"
puts $out "}"
close $out
puts "asic_extra: max_fanout $max_fanout ($max_fanout_net), logic_depth $logic_depth ($logic_depth_endpoint)"
