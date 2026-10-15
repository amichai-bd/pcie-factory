# Extra metrics ORFS does not report: max fanout and logic depth of the
# final design. Run by ./build asic through the ORFS `run` target, so the
# ORFS environment (SCRIPTS_DIR, RESULTS_DIR, REPORTS_DIR, platform files) is
# set. Writes $REPORTS_DIR/asic_extra.json. Spec: wiki/tools/asic/SPEC.md.
#
# Max fanout: the largest number of input pins (plus output ports) on one
#   data net. Nets that reach a register clock pin are excluded.
# Logic depth: the largest number of cells on any path that ends at a
#   register input pin other than its clock (data, enable, set or reset) or
#   an output port, and starts at a register or an
#   input port (both endpoints excluded, buffers and inverters included).

source $::env(SCRIPTS_DIR)/load.tcl
load_design 6_final.odb 6_final.sdc

interp recursionlimit {} 100000

proc asic_mark { msg } {
  puts "asic_extra: $msg"
  flush stdout
}
asic_mark "design loaded"

# The walk uses the odb netlist (OpenSTA collection queries over every net
# crashed this OpenROAD build); OpenSTA only identifies the registers and
# the clock nets.
set block [ord::get_db_block]

# Registers end the backward walk; record them by odb instance name.
array set ::asic_regs {}
foreach cell [all_registers -cells] {
  set inst "NULL"
  catch { set inst [sta::sta_to_db_inst $cell] }
  if { $inst eq "NULL" || $inst eq "" } {
    set inst [$block findInst [get_full_name $cell]]
  }
  if { $inst ne "NULL" && $inst ne "" } {
    set ::asic_regs([$inst getName]) 1
  }
}

# Nets that reach a register clock pin carry the clock, not data.
array set ::asic_clock_nets {}
foreach pin [all_registers -clock_pins] {
  foreach net [get_nets -quiet -of_objects $pin] {
    set ::asic_clock_nets([get_full_name $net]) 1
  }
}

proc asic_data_net { net } {
  if { $net eq "NULL" || $net eq "" } {
    return 0
  }
  if { [lsearch -exact {POWER GROUND CLOCK} [$net getSigType]] >= 0 } {
    return 0
  }
  return [expr { ![info exists ::asic_clock_nets([$net getName])] }]
}

asic_mark "[array size ::asic_regs] registers, [array size ::asic_clock_nets] clock nets"

# --- max fanout ---
set max_fanout 0
set max_fanout_net ""
foreach net [$block getNets] {
  if { ![asic_data_net $net] } {
    continue
  }
  set fanout 0
  foreach it [$net getITerms] {
    if { [$it isInputSignal] } {
      incr fanout
    }
  }
  foreach bt [$net getBTerms] {
    if { [$bt getIoType] eq "OUTPUT" } {
      incr fanout
    }
  }
  if { $fanout > $max_fanout } {
    set max_fanout $fanout
    set max_fanout_net [$net getName]
  }
}

asic_mark "max fanout $max_fanout ($max_fanout_net)"

# --- logic depth ---
array set ::asic_depth {}

# Depth of an instance: 0 for a register, else 1 + its deepest input.
proc asic_inst_depth { inst } {
  set name [$inst getName]
  if { [info exists ::asic_regs($name)] } {
    return 0
  }
  if { [info exists ::asic_depth($name)] } {
    return $::asic_depth($name)
  }
  # A combinational loop would recurse forever; break it at depth 0.
  set ::asic_depth($name) 0
  set depth 0
  foreach it [$inst getITerms] {
    if { [$it isInputSignal] } {
      set d [asic_net_depth [$it getNet]]
      if { $d > $depth } {
        set depth $d
      }
    }
  }
  set ::asic_depth($name) [expr { $depth + 1 }]
  return $::asic_depth($name)
}

# Depth seen on a net: the depth of the instances driving it (ports are 0).
proc asic_net_depth { net } {
  if { ![asic_data_net $net] } {
    return 0
  }
  set depth 0
  foreach it [$net getITerms] {
    if { [$it isOutputSignal] } {
      set d [asic_inst_depth [$it getInst]]
      if { $d > $depth } {
        set depth $d
      }
    }
  }
  return $depth
}

set logic_depth 0
set logic_depth_endpoint ""
foreach name [array names ::asic_regs] {
  set inst [$block findInst $name]
  foreach it [$inst getITerms] {
    if { ![$it isInputSignal] } {
      continue
    }
    set d [asic_net_depth [$it getNet]]
    if { $d > $logic_depth } {
      set logic_depth $d
      set logic_depth_endpoint "$name/[[$it getMTerm] getName]"
    }
  }
}
foreach bt [$block getBTerms] {
  if { [$bt getIoType] ne "OUTPUT" } {
    continue
  }
  set d [asic_net_depth [$bt getNet]]
  if { $d > $logic_depth } {
    set logic_depth $d
    set logic_depth_endpoint [$bt getName]
  }
}
asic_mark "logic depth $logic_depth ($logic_depth_endpoint)"

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
asic_mark "wrote $::env(REPORTS_DIR)/asic_extra.json"
