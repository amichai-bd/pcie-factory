"""Disposable #435 paired compiler experiment. Never merge this file."""
import json
import os
from pathlib import Path
import re
import shutil
import statistics
import subprocess

import yaml

ROOT = Path.cwd()
OUT = ROOT / "workdir/opt-evidence"
OUT.mkdir(parents=True, exist_ok=True)
CASES = {"core": "e2e_bar_traffic", "mac": "arcs_quick", "pcs": "rx_decode_table"}
DRIVER = ROOT / "tools/build/sim.py"
original_driver = DRIVER.read_text()
records = []

# Keep each selected entry byte-for-byte in meaning, including its default seed,
# parameters, timeout and instrumentation. Partial selections are measurements,
# not regression gate/floor evidence; omit aggregate floors on these copies.
for ip, name in CASES.items():
    path = ROOT / f"src/dv/{ip}/regress/level1.yaml"
    original = path.read_text()
    (OUT / f"original-{ip}-level1.yaml").write_text(original)
    entries = yaml.safe_load(original)["tests"]
    selected = [entry for entry in entries if entry["name"] == name]
    assert len(selected) == 1
    path.write_text(yaml.safe_dump({"tests": selected}, sort_keys=False))

def command(args, log, env):
    with log.open("w") as stream:
        result = subprocess.run(args, stdout=stream, stderr=subprocess.STDOUT, env=env)
    print(log.read_text(), flush=True)
    assert result.returncode == 0, f"failed: {args}; {log}"

for variant in ("baseline", "optimized"):
    text = original_driver
    if variant == "optimized":
        # MAKEFLAGS command-line variable assignments override verilated.mk's
        # -Os defaults. Keep an explicit flag in BUILD_ARGS/cache key as well.
        text = text.replace('BUILD_ARGS = ["--assert"]',
                            'BUILD_ARGS = ["--assert", "-CFLAGS", "-O3"]')
        assert text != original_driver
    DRIVER.write_text(text)
    env = os.environ.copy()
    env["CCACHE_DISABLE"] = "1"
    env["MAKEFLAGS"] = ("OPT_FAST=-O3 OPT_SLOW=-O3 OPT_GLOBAL=-O3"
                        if variant == "optimized" else "")
    (OUT / f"{variant}-driver.py").write_text(text)
    for coverage in (False, True):
        mode = "coverage" if coverage else "plain"
        for repeat in range(3):
            dest = OUT / f"{variant}-{mode}-{repeat}"
            dest.mkdir()
            args = ["./build", "regress", "--level", "1", "--jobs", "1"]
            for ip in CASES:
                args.extend(["--ip", ip])
            if coverage:
                args.append("--coverage")
            command(args, dest / "regress.log", env)
            shutil.copy2("workdir/regress/summary.json", dest / "summary.json")
            summary = json.loads((dest / "summary.json").read_text())
            for row in summary["tests"]:
                assert row["status"] == "pass", row
                records.append({"variant": variant, "mode": mode, "repeat": repeat,
                                "ip": row["ip"], "name": row["name"],
                                "seed": row["seed"], "cpu_s": row["cpu_s"]})
            for ip in CASES:
                for pattern in ("**/coverage.dat", "**/funcov.json", "**/sim.log", "**/results.xml"):
                    for path in (ROOT / "workdir" / ip).glob(pattern):
                        target = dest / path.relative_to(ROOT / "workdir")
                        target.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(path, target)
            for row in summary["builds"]:
                path = ROOT / "workdir" / row["log"]
                target = dest / row["log"]
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, target)
            # On first iteration these must be fresh compilations. Inspect the
            # actual compiler command, not only requested Verilator flags.
            if repeat == 0:
                assert summary["build_cache"]["hits"] == 0, summary["build_cache"]
                commands = []
                for path in dest.glob("**/build.log"):
                    commands += [line for line in path.read_text().splitlines()
                                 if re.search(r"(?:g\+\+|c\+\+).* -c ", line)]
                assert commands, "missing compiler commands"
                expected = "-O3" if variant == "optimized" else "-Os"
                observed = []
                for line in commands:
                    opts = re.findall(r"(?<!\S)-O(?:[0-3sgz]|fast)(?!\S)", line)
                    observed.append(opts[-1] if opts else "default-no-O")
                    if variant == "optimized":
                        assert opts and opts[-1] == expected, line
                    else:
                        assert not opts or opts[-1] == expected, line
                assert expected in observed, observed
                (dest / "effective-optimization.json").write_text(json.dumps(observed))
                (dest / "compiler-commands.txt").write_text("\n".join(commands))

# Same checkout paths permit strict pointwise comparisons of raw counters.
for repeat in range(3):
    left = OUT / f"baseline-coverage-{repeat}"
    right = OUT / f"optimized-coverage-{repeat}"
    for pattern in ("**/coverage.dat", "**/funcov.json"):
        files = list(left.glob(pattern))
        assert len(files) == len(CASES), (pattern, files)
        for path in files:
            other = right / path.relative_to(left)
            if path.name == "funcov.json":
                assert json.loads(path.read_text()) == json.loads(other.read_text()), path
            else:
                counts = lambda p: sorted(line for line in p.read_text().splitlines()
                                          if line.startswith("C "))
                assert counts(path) == counts(other), path

(OUT / "records.json").write_text(json.dumps(records, indent=2) + "\n")
for mode in ("plain", "coverage"):
    for ip in CASES:
        medians = {variant: statistics.median(row["cpu_s"] for row in records
                   if row["variant"] == variant and row["mode"] == mode and row["ip"] == ip)
                   for variant in ("baseline", "optimized")}
        print(mode, ip, medians, flush=True)
print("PASS: behavior, effective compiler flags and pointwise coverage equality", flush=True)
