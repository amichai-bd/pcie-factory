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
| Verilator | 5.052, built from tag `v5.052` (commit `ea338be98e1e838d3518809ce8899f85a009963c`, verified after clone) into `/opt/verilator/5.052` | `cfg/host.yaml` `verilator` |
| Verilator build deps | apt packages listed in `verilator.build_deps` | `cfg/host.yaml` |
| Python | `python3` >= 3.11 | `cfg/host.yaml` `python.minimum` |
| cocotb, pytest, PyYAML, markdown-it-py, Pygments | exact `name==version` | `tools/requirements.txt` |

Change a pin only in its source of truth; the hook reads both files and
hard-codes no version. `tools/requirements.txt` holds only exact `==` pins,
one per line, because the hook's skip check compares installed versions to
them literally.

Known limitation: only the direct Python packages are pinned. Their transitive
dependencies (for example pluggy, iniconfig, mdurl, find_libpython) resolve to
whatever pip picks on the first install and then stay cached in the container.

`cfg/host.yaml` is parsed by a small stdlib parser in the hook (PyYAML may not
be installed yet when it runs). Keep it to nested block mappings, scalars,
inline `[a, b]` lists and `#` comments.

### Hosts

| Host | Role | Provisioned by |
| --- | --- | --- |
| Cloud | Claude Code cloud sessions (Ubuntu container) | `.claude/hooks/session-start.sh` |
| Omarchy | Self-hosted CI runners, FPGA and ASIC flows (Arch Linux) | By hand; versions recorded under `hosts.omarchy` in `cfg/host.yaml` |

The Omarchy entries in `cfg/host.yaml` record what the host agent reported
and must match the pins above; an entry not yet reported stays empty.
`venv: runner` means the runner service's own virtual environment (its
`$VIRTUAL_ENV`, first on `PATH`); its path is not recorded. Quartus Lite and
ORFS run only there and are recorded there too:
Quartus Prime Lite `25.1std.0 Build 1129` at `/opt/intelFPGA_lite/25.1/quartus`
(`quartus_root`, `quartus_version`), as printed by the fpga workflow's
discovery step. The [fpga tool](../fpga/SPEC.md#finding-quartus) falls back to
`<quartus_root>/bin`.

### Cloud session-start hook

`.claude/settings.json` registers the hook as a synchronous `SessionStart`
command with a 1800 s timeout, so the session starts only after the toolchain
is ready.

1. Exits 0 immediately unless `CLAUDE_CODE_REMOTE=true` (local hosts are
   never touched).
2. Reads the pins with the system `python3` and fails if it is older than
   `python.minimum`.
3. Rejects a Verilator prefix that is not a plain absolute path
   (`^/[A-Za-z0-9._/+-]+$`, no `..`), because the prefix is written into
   `$CLAUDE_ENV_FILE`, which the session sources.
4. **Verilator:** skipped when `<prefix>/bin/verilator --version` starts with
   `Verilator <version> `. Otherwise, under a per-prefix `flock`, a separate
   `bash -euo pipefail` (so any failing step aborts at once) installs missing
   `build_deps` with apt, shallow-clones the tag into a temporary directory,
   checks that `HEAD` equals `verilator.commit`, then runs `autoconf;
   configure --prefix; make -j$(nproc); make install`. Output goes to a
   `/tmp/session-start-verilator.*.log` file, and its tail is printed on
   failure. An `EXIT` trap removes the temporary clone whether the build
   succeeds or fails. After the build the hook checks the version again.
   Test-only overrides: `HOST_VERILATOR_PREFIX` (install prefix) and
   `HOST_VERILATOR_TAG` (tag to clone; the commit check still applies).
5. **Python packages:** skipped when every pin in `tools/requirements.txt` is
   installed at exactly that version (`importlib.metadata`). Otherwise
   `python3 -m pip install -r tools/requirements.txt` into the system
   interpreter.
6. Appends `export PATH="<prefix>/bin:$PATH"` to `$CLAUDE_ENV_FILE` when set,
   once per file.
7. Prints `verilator --version` and the versions of Python, cocotb, pytest,
   PyYAML, markdown-it-py and Pygments. The imports double as a self-check.

**Why the system interpreter, not a venv.** `./build`, pytest and cocotb's
runner all use `python3` from `PATH`; one interpreter with every pin is the
simplest thing that works. The cloud container is single-purpose and
disposable. On a PEP 668 system (the hook checks for `EXTERNALLY-MANAGED`)
pip needs `--break-system-packages`; it writes to `/usr/local`, not the
apt-owned path. `--ignore-installed` is also passed because apt-installed
modules such as `python3-yaml` have no pip `RECORD` and cannot be upgraded in
place; the pinned copy in `/usr/local` precedes the apt one on `sys.path`.

**Expected time.** The Verilator build through the hook takes about 6.5 min
at `-j4` with an empty ccache (measured 6m31s); a rebuild with a warm ccache
took 3m35s. On a fresh container, add the
apt install of the build deps (not measured here) and the pip install (about
5 s). The 1800 s timeout leaves headroom for slower machines. With the tools
present, a rerun takes under 1 s and changes nothing. The container state is
cached after the hook completes, so later sessions take this fast path.

### Local hosts

Local hosts (including Omarchy) install the same pins by hand: Verilator from
the tag into the pinned prefix with the listed build deps (Arch package names
differ), `python3` >= 3.11, and `python3 -m pip install -r
tools/requirements.txt` in whatever environment `./build` will run.
