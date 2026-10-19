# Hand-made Quartus report fixtures

Every file here was written by hand to mimic the text layout of Quartus
Prime Lite 25.1std.0 Build 1129 (Cyclone IV E) reports, checked against the
reports of the first `fpga` workflow runs on the CI host. None is real
Quartus output: panels are trimmed, the legal notice is left out, and the
names and numbers describe no real design. The parser tests in `../test_fpga_reports.py`
use them; when real reports reveal a layout difference, fix the fixture and
the parser together.

| File | Mimics |
| --- | --- |
| `output_files/widget_board.map.summary` | Analysis and synthesis summary (`--synth-only`): plain counts, no totals and no device line |
| `output_files/widget_board.fit.summary` | Fitter summary |
| `output_files/widget_board.fit.rpt` | Fitter report: resource usage panel, a trimmed per-entity table (its `M9Ks` column must not be read as the block count) and a Fitter RAM Summary with one simple dual-port RAM (its columns follow the Quartus 25.1std Cyclone IV E panel; the first CI fit with RAMs (PR #276) filled every field) |
| `output_files/widget_board.sta.rpt` | Timing analyzer report: Fmax (slow models only, as Quartus prints), setup and hold summaries for three corners, recovery and removal summaries (one empty), the unconstrained paths summary, plus unrelated panels including one with blank rows |
| `worst_path.txt` | Output of the generated `worst_path.tcl` |
| `map_fail.log` | A failed `quartus_map` log: one `Critical Warning (…)` and three `Error (…)` messages more than 30 lines before the end, then the closing `Error:` summary block |
| `map_warnings.log` | A passing `quartus_map` log with warnings: two known IDs (one twice), an indented sub-message, a `Critical Warning (…)`, and an unknown warning that names a user, a host, a path outside the checkout and one inside it (checkout `/var/lib/ci/work/checkout`), longer than the published message length |
