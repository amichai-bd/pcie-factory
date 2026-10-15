# asic test fixtures

`orfs/` holds report files from a real `./build asic` run of the pinned ORFS
image (`openroad/orfs:26Q4-142-gd4b96380b`, sky130hd, a 10 ns clock) on a
small synthesis target, laid out as ORFS writes them under
`WORK_HOME/{logs,reports}/<platform>/<nick>/base/`:

| File | ORFS source | Used for |
| --- | --- | --- |
| `logs/1_synth.json`, `2_1_floorplan.json`, `4_1_cts.json`, `5_2_route.json`, `6_report.json` | Per-step metrics | Stage merge (`synth` vs `finish` counts), timing, area, utilization |
| `reports/6_finish.rpt` | `report_metrics` at finish, cut to its `report_checks -path_delay min` and `max` sections | Worst setup path (input port to register, 4 cells), first-`max`-section selection |
| `reports/synth_stat.txt` | Yosys `stat` (0.68 table format) | Synthesis cell count and area fallback |
| `reports/asic_extra.json` | `tools/asic/extra_metrics.tcl` | Max fanout and logic depth |
| `reports/asic_clocks.json` | `tools/asic/clock_slacks.tcl` (written by hand, see below) | Setup slack per clock |

Edits from the real files: the top module and instance names were renamed
to `counter` and `u_inc` (the fixture IP in `tools/build/tests/fixtures/src/`)
to keep `tools/` design-neutral, and `6_finish.rpt` was cut to the two
sections the parser reads. The run predates `clock_slacks.tcl`, so
`asic_clocks.json` was written by hand from the same run's single-clock
`finish__timing__setup__ws` and `finish__timing__setup__tns`, in the
script's output format. Otherwise the content is unchanged, so the parsers
see real formatting.

Publishability: every file is tool output for this repository's own RTL. None
carries a license or vendor banner, host name, user path or timestamp. The
standard-cell names (`sky130_fd_sc_hd__*`) come from the open SkyWater
SKY130 PDK.

`fake_orfs/` is a fake native ORFS root: `env.sh` puts `bin/` first on
`PATH`, where `make` copies `orfs/` into `WORK_HOME` for the `finish` and
`run` targets (`FAKE_MAKE=fail|sleep|noextra` simulate a failing, hanging or
incomplete flow; `noclocks|clocksfail|clockssleep` a `clocks`
step that writes no clock, fails or hangs) and `openroad`/`yosys` print versions.
