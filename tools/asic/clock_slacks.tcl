# Setup slack per clock, which ORFS does not report (it gives only the
# totals over all clocks). Run by ./build asic as its own `clocks` step,
# through the ORFS `run` target, after the `extra` step, so a crash, OOM or
# timeout here never costs the other metrics, which metrics.json still
# records; the step's failure fails the run. Writes $REPORTS_DIR/asic_clocks.json.
# Spec: wiki/tools/asic/SPEC.md.
#
# For each SDC clock: the worst slack and the total negative slack over the
# constrained setup endpoints it captures (one worst path per endpoint), and
# the start and end points of its five worst endpoints' paths.
# The result is required: missing or empty, the run is incomplete.

source $::env(SCRIPTS_DIR)/load.tcl
load_design 6_final.odb 6_final.sdc

proc asic_mark { msg } {
  puts "asic_clocks: $msg"
  flush stdout
}
asic_mark "design loaded"

# The parasitics ORFS's final report used: the extracted SPEF when the
# platform has extraction rules, else global-route estimates.
proc asic_clock_slacks {} {
  set spef $::env(RESULTS_DIR)/6_final.spef
  if { [file exists $spef] } {
    read_spef $spef
  } else {
    estimate_parasitics -global_routing
  }
  set result {}
  foreach clk [all_clocks] {
    dict set result [get_property $clk name] [dict create ws "" tns 0.0 paths {}]
  }
  # One path per endpoint: the worst one, so the sum of the negative ones is
  # the TNS. The option names changed in OpenSTA; accept either.
  if { [catch { set ends [find_timing_paths -path_delay max -group_path_count 1000000 \
                            -endpoint_path_count 1 -unique_paths_to_endpoint] }] } {
    set ends [find_timing_paths -path_delay max -group_count 1000000 \
                -endpoint_count 1 -unique_paths_to_endpoint]
  }
  foreach end $ends {
    set clk [get_property $end endpoint_clock]
    if { $clk eq "" || $clk eq "NULL" } {
      continue
    }
    set name [get_property $clk name]
    set slack [expr { double([get_property $end slack]) }]
    if { ![dict exists $result $name] } {
      dict set result $name [dict create ws "" tns 0.0 paths {}]
    }
    # Start and end point names; a failure only drops this path's names.
    if { ![catch { set from [get_full_name [get_property $end startpoint]]
                   set to [get_full_name [get_property $end endpoint]] }] } {
      set paths [dict get $result $name paths]
      lappend paths [list $slack $from $to]
      dict set result $name paths $paths
    }
    set ws [dict get $result $name ws]
    if { $ws eq "" || $slack < $ws } {
      dict set result $name ws $slack
    }
    if { $slack < 0 } {
      dict set result $name tns [expr { [dict get $result $name tns] + $slack }]
    }
  }
  # Keep the five worst paths of each clock.
  dict for {name v} $result {
    set paths [lrange [lsort -real -index 0 [dict get $v paths]] 0 4]
    dict set result $name paths $paths
  }
  return $result
}

# A Tcl error writes an empty clock list; the tool then marks the run
# incomplete (required metric) and warns with the cause.
set clock_slacks {}
if { [catch { set clock_slacks [asic_clock_slacks] } err] } {
  asic_mark "clock slacks failed: $err"
  set clock_slacks {}
}
dict for {name v} $clock_slacks {
  asic_mark "clock $name: setup ws [dict get $v ws], tns [dict get $v tns]"
}

proc asic_json_number { v } {
  if { $v eq "" } {
    return "null"
  }
  return [format %.4f $v]
}

proc asic_json_string { s } {
  return "\"[string map {\\ \\\\ \" \\\"} $s]\""
}

set clocks {}
dict for {name v} $clock_slacks {
  set paths {}
  foreach p [dict get $v paths] {
    lappend paths "{\"slack\": [asic_json_number [lindex $p 0]], \"from\": [asic_json_string [lindex $p 1]], \"to\": [asic_json_string [lindex $p 2]]}"
  }
  lappend clocks "    [asic_json_string $name]: {\"setup_ws\": [asic_json_number [dict get $v ws]], \"setup_tns\": [asic_json_number [dict get $v tns]], \"worst_paths\": \[[join $paths ", "]\]}"
}
set out [open $::env(REPORTS_DIR)/asic_clocks.json w]
if { [llength $clocks] } {
  puts $out "{\n  \"clocks\": {\n[join $clocks ",\n"]\n  }\n}"
} else {
  puts $out "{\n  \"clocks\": {}\n}"
}
close $out
asic_mark "wrote $::env(REPORTS_DIR)/asic_clocks.json"
