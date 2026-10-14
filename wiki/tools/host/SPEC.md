# Host toolchain

Owns the pinned host toolchain and how each host gets it. Code:
[`cfg/host.yaml`](../../../cfg/host.yaml),
[`tools/requirements.txt`](../../../tools/requirements.txt),
[`.claude/hooks/session-start.sh`](../../../.claude/hooks/session-start.sh) and
[`.claude/settings.json`](../../../.claude/settings.json).

## Hosts and toolchain

### Pins

| Tool | Pin | Source of truth |
| --- | --- | --- |
| Verilator | 5.052, built from tag `v5.052` into `/opt/verilator/5.052` | `cfg/host.yaml` `verilator` |
| Verilator build deps | apt packages listed in `verilator.build_deps` | `cfg/host.yaml` |
| Python | `python3` >= 3.11 | `cfg/host.yaml` `python.minimum` |
| cocotb, pytest, PyYAML, markdown-it-py, Pygments | exact `name==version` | `tools/requirements.txt` |

Change a pin only in its source of truth; the hook reads both files and
hard-codes no version. `tools/requirements.txt` holds only exact `==` pins,
one per line, because the hook's skip check compares installed versions to
them literally.

`cfg/host.yaml` is parsed by a small stdlib parser in the hook (PyYAML may not
be installed yet when it runs). Keep it to nested block mappings, scalars,
inline `[a, b]` lists and `#` comments.

### Hosts

| Host | Role | Provisioned by |
| --- | --- | --- |
| Cloud | Claude Code cloud sessions (Ubuntu container) | `.claude/hooks/session-start.sh` |
| Omarchy | Self-hosted CI runners, FPGA and ASIC flows (Arch Linux) | By hand; versions recorded under `hosts.omarchy` in `cfg/host.yaml` |

The Omarchy entries in `cfg/host.yaml` are intentionally empty until the host
agent reports the installed paths and versions; they must match the pins
above. Quartus Lite and ORFS run only there and are recorded there too.

### Cloud session-start hook

`.claude/settings.json` registers the hook as a synchronous `SessionStart`
command with a 1800 s timeout, so the session starts only after the toolchain
is ready.

1. Exits 0 immediately unless `CLAUDE_CODE_REMOTE=true` (local hosts are
   never touched).
2. Reads the pins with the system `python3` and fails if it is older than
   `python.minimum`.
3. **Verilator:** skipped when `<prefix>/bin/verilator --version` starts with
   `Verilator <version> `. Otherwise, under a per-prefix `flock`, installs
   missing `build_deps` with apt, shallow-clones the tag, runs
   `autoconf; configure --prefix; make -j$(nproc); make install` with output
   in a `/tmp/session-start-verilator.*.log` file (its tail is printed on
   failure), and re-checks the version. `HOST_VERILATOR_PREFIX` overrides the
   prefix, for exercising the build path in a scratch directory.
4. **Python packages:** skipped when every pin in `tools/requirements.txt` is
   installed at exactly that version (`importlib.metadata`). Otherwise
   `python3 -m pip install -r tools/requirements.txt` into the system
   interpreter.
5. Appends `export PATH="<prefix>/bin:$PATH"` to `$CLAUDE_ENV_FILE` when set,
   once per file.
6. Prints `verilator --version` and the Python, cocotb, pytest and PyYAML
   versions.

**Why the system interpreter, not a venv.** `./build`, pytest and cocotb's
runner all use `python3` from `PATH`; one interpreter with every pin is the
simplest thing that works. The cloud container is single-purpose and
disposable. On a PEP 668 system (the hook checks for `EXTERNALLY-MANAGED`)
pip needs `--break-system-packages`; it writes to `/usr/local`, not the
apt-owned path. `--ignore-installed` is also passed because apt-installed
modules such as `python3-yaml` have no pip `RECORD` and cannot be upgraded in
place; the pinned copy in `/usr/local` precedes the apt one on `sys.path`.

**Expected time.** First run in a fresh container: apt deps about 1 min plus
the Verilator build, about 7 min at `-j4` (measured: 6.7 min), so roughly
10 min; the 1800 s timeout leaves headroom for slower machines. Rerun with
tools present: under 1 s and no changes (the no-op path). The container
state is cached after the hook completes, so later sessions take the fast
path.

### Local hosts

Local hosts (including Omarchy) install the same pins by hand: Verilator from
the tag into the pinned prefix with the listed build deps (Arch package names
differ), `python3` >= 3.11, and `python3 -m pip install -r
tools/requirements.txt` in whatever environment `./build` will run.
