Closes #12. Part of epic #3.

## What
- `src/rtl/a/a_core.sv`: a counter wrap fix.

## Notes for review
- Follow-up: #14 tracks the wider counter.

## Validation
At head 0123456789abcdef0123456789abcdef01234567. Python 3.13.16, pytest 9.1.1, Verilator 5.052, cocotb 2.1.0.
Seeds: per the crc32 seed rule.

| Command | Result |
| --- | --- |
| `./build check` | PASS (10 s) |
| `python3 -m pytest tools -q` | 1300 passed |
| `flock <lock> ./build regress --level 0 --level 1 --ip a --jobs 4` | L0 4/4 passed, CPU 20.5 s, wall 9 s; L1 10/10 passed, CPU 300.0 s, wall 120 s |

`<lock>` stands for the host's shared lock path, elided.

| # | Mutant | Entry | Seed | Result |
| --- | --- | --- | --- | --- |
| M1 | wrap at 14, not 15 | `wrap` | 7 | FAIL at cycle 42: count 14, expected 15 |

```
error: a log line | with a bar, closes #3 and ## Validation inside a fence
```

### Not run
None.

## Review
Independent reviewer: assigned by the firstmate. Verdict: pending.
