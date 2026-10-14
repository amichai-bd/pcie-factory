# Hand-made Quartus report fixtures

Every file here was written by hand to mimic the text layout of Quartus
Prime Lite 23.1 (Cyclone IV E) reports. None is real Quartus output and the
numbers describe no real design. The parser tests in `../test_fpga_reports.py`
use them; when real reports reveal a layout difference, fix the fixture and
the parser together.

| File | Mimics |
| --- | --- |
| `output_files/widget_board.fit.summary` | Fitter summary |
| `output_files/widget_board.fit.rpt` | Fitter report, resource usage panel only |
| `output_files/widget_board.sta.rpt` | Timing analyzer report: Fmax, setup and hold summaries for three corners, plus unrelated panels |
| `worst_path.txt` | Output of the generated `worst_path.tcl` |
