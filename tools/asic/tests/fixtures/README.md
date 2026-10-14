# asic test fixtures

`orfs/` holds report files from a real `./build asic` run of the pinned ORFS
image (`openroad/orfs:26Q4-142-gd4b96380b`, sky130hd, a 10 ns clock) on a
small synthesis target, laid out as ORFS writes them under
`WORK_HOME/{logs,reports}/<platform>/<nick>/base/`:

| File | ORFS source | Used for |
| --- | --- | --- |
| `logs/1_synth.json`, `2_1_floorplan.json`, `4_1_cts.json`, `5_2_route.json`, `6_report.json` | Per-step metrics | Stage merge (`synth` vs `finish` counts), timing, area, utilization |
| `reports/6_finish.rpt` | `report_metrics` at finish | Worst setup path (input port to register, 4 cells) |
| `reports/synth_stat.txt` | Yosys `stat` (0.68 table format) | Synthesis cell count and area fallback |
| `reports/asic_extra.json` | `tools/asic/extra_metrics.tcl` | Max fanout and logic depth |

Edits from the real files, to keep `tools/` design-neutral: the top module
and instance names were renamed to `counter` and `u_inc` (the fixture IP in
`tools/build/tests/fixtures/src/`). Nothing else was changed, so the parsers
see real formatting.

`fake_orfs/` is a fake native ORFS root: `env.sh` puts `bin/` first on
`PATH`, where `make` copies `orfs/` into `WORK_HOME` for the `finish` and
`run` targets (`FAKE_MAKE=fail|sleep|noextra` simulate a failing, hanging or
incomplete flow) and `openroad`/`yosys` print versions.
