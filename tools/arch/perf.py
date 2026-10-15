"""Architecture-model perf scenarios (wiki/tools/arch/SPEC.md).

A top-level IP's ``arch/<ip>/`` folder holds one model module, ``<prefix><ip>_arch.py``,
that defines ``perf(workload, seed) -> {metric: number}``, and perf scenarios
``perf/<scenario>.yaml``. Each scenario gives the model a workload and a seed
and checks the returned metrics against ``min``/``max`` targets. The result
is written to ``<workdir>/<ip>/arch/perf.json``.
"""

from __future__ import annotations

import importlib.util
import json
import math
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType

import yaml

from build.ip import Ip
from checks.common import read_prefix

ARCH_DIR = "arch"
PERF_DIR = "perf"
OUTPUT = "perf.json"
SCHEMA = 1

_SCENARIO_KEYS = {"description", "seed", "workload", "targets"}
_REQUIRED_KEYS = {"description", "seed", "workload"}
_BOUNDS = ("min", "max")
_NAME = re.compile(r"^[a-z0-9][a-z0-9_]*$")


class ArchError(Exception):
    """A usage or configuration error (exit 2)."""


@dataclass(frozen=True)
class Scenario:
    name: str
    path: Path
    description: str
    seed: int
    workload: dict
    targets: dict[str, dict[str, float]] = field(default_factory=dict)


def arch_dir(ip: Ip) -> Path:
    return ip.dir(ARCH_DIR)


def output_path(ip: Ip, workdir: Path) -> Path:
    return ip.work_dir(workdir) / ARCH_DIR / OUTPUT


def model_name(ip: Ip) -> str:
    """``<prefix><ip>_arch.py``, the prefix from ``src/README.md``."""
    prefix = read_prefix(ip.src_root)
    if prefix is None:
        raise ArchError(f"{ip.src_root / 'README.md'}: declares no module prefix")
    return f"{prefix}{ip.id}_{ARCH_DIR}.py"


def find_model(ip: Ip) -> Path:
    """The IP's one model module: exactly ``arch/<ip>/<prefix><ip>_arch.py``."""
    if not ip.top_level:
        raise ArchError(f"IP {ip.id} is a sub-IP; arch/ models are for top-level IPs only")
    d = arch_dir(ip)
    if not d.is_dir():
        raise ArchError(f"IP {ip.id} has no {ARCH_DIR}/{ip.id}/ directory ({d})")
    model = d / model_name(ip)
    if not model.is_file():
        raise ArchError(f"{d}: no model {model.name}")
    return model


def _number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) \
        and math.isfinite(value)


def load_scenario(path: Path) -> Scenario:
    where = str(path)
    if not _NAME.match(path.stem):
        raise ArchError(f"{where}: scenario name {path.stem!r} must match {_NAME.pattern}")
    try:
        data = yaml.safe_load(path.read_text())
    except OSError as e:
        raise ArchError(f"{where}: cannot read: {e.strerror}") from None
    except yaml.YAMLError as e:
        raise ArchError(f"{where}: invalid YAML: {e}") from None
    if not isinstance(data, dict):
        raise ArchError(f"{where}: top level must be a mapping")
    missing, extra = _REQUIRED_KEYS - set(data), set(data) - _SCENARIO_KEYS
    if missing:
        raise ArchError(f"{where}: missing key(s) {sorted(missing)}")
    if extra:
        raise ArchError(f"{where}: unknown key(s) {sorted(extra)}")
    if not isinstance(data["description"], str) or not data["description"].strip():
        raise ArchError(f"{where}: description: expected a non-empty string")
    seed = data["seed"]
    if not isinstance(seed, int) or isinstance(seed, bool) or seed < 0:
        raise ArchError(f"{where}: seed: expected an integer >= 0")
    if not isinstance(data["workload"], dict):
        raise ArchError(f"{where}: workload: expected a mapping")
    targets = data.get("targets") or {}
    if not isinstance(targets, dict):
        raise ArchError(f"{where}: targets: expected a mapping of metric -> bounds")
    for metric, bounds in targets.items():
        if not isinstance(metric, str) or not isinstance(bounds, dict) or not bounds \
                or set(bounds) - set(_BOUNDS):
            raise ArchError(f"{where}: targets.{metric}: expected a mapping with "
                            f"'min' and/or 'max'")
        for k, v in bounds.items():
            if not _number(v):
                raise ArchError(f"{where}: targets.{metric}.{k}: expected a number")
        if "min" in bounds and "max" in bounds and bounds["min"] > bounds["max"]:
            raise ArchError(f"{where}: targets.{metric}: min > max")
    return Scenario(path.stem, path, data["description"].strip(), seed,
                    data["workload"], targets)


def load_scenarios(ip: Ip, only: str | None = None) -> list[Scenario]:
    d = arch_dir(ip) / PERF_DIR
    files = sorted(d.glob("*.yaml")) if d.is_dir() else []
    if not files:
        raise ArchError(f"IP {ip.id} has no perf scenarios ({d}/*.yaml)")
    if only is not None:
        known = [p.stem for p in files]
        if only not in known:
            raise ArchError(f"IP {ip.id} has no scenario {only!r} "
                            f"(known: {', '.join(known)})")
        files = [p for p in files if p.stem == only]
    return [load_scenario(p) for p in files]


def load_model(path: Path, ip: Ip) -> ModuleType:
    """Import the model by path; its directory is on ``sys.path`` while it
    loads, so it may import sibling modules by bare name."""
    name = f"_arch_model_{ip.id.replace('/', '_')}"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(path.parent))
    try:
        spec.loader.exec_module(module)
    except Exception as exc:  # noqa: BLE001 - any import failure is reported
        raise ArchError(f"{path}: cannot import the model: "
                        f"{type(exc).__name__}: {exc}") from None
    finally:
        sys.path.remove(str(path.parent))
    if not callable(getattr(module, "perf", None)):
        raise ArchError(f"{path}: the model defines no perf(workload, seed) function")
    return module


def run_scenario(model: ModuleType, sc: Scenario) -> dict:
    """Run one scenario; ``status`` is pass, fail (a target missed) or error."""
    rec = {"name": sc.name, "description": sc.description, "seed": sc.seed,
           "workload": sc.workload, "metrics": {}, "targets": [], "status": "pass"}
    try:
        metrics = model.perf(dict(sc.workload), sc.seed)
    except Exception as exc:  # noqa: BLE001 - a model failure fails the scenario
        rec.update(status="error", detail=f"perf() raised {type(exc).__name__}: {exc}")
        return rec
    if not isinstance(metrics, dict) or not all(
            isinstance(k, str) and _number(v) for k, v in metrics.items()):
        rec.update(status="error",
                   detail="perf() must return a mapping of metric name -> finite number")
        return rec
    rec["metrics"] = dict(sorted(metrics.items()))
    missing = sorted(set(sc.targets) - set(metrics))
    if missing:
        rec.update(status="error",
                   detail=f"targets name metric(s) the model did not report: {missing}")
        return rec
    for metric, bounds in sorted(sc.targets.items()):
        value = metrics[metric]
        ok = value >= bounds.get("min", -math.inf) and value <= bounds.get("max", math.inf)
        rec["targets"].append({"metric": metric, **bounds, "value": value,
                               "status": "pass" if ok else "fail"})
        if not ok:
            rec["status"] = "fail"
    return rec


def run(ip: Ip, workdir: Path, scenario: str | None = None, out=None) -> int:
    """Run the scenarios, write perf.json and return 0 (all pass) or 1."""
    out = out or sys.stdout
    model_path = find_model(ip)
    scenarios = load_scenarios(ip, scenario)
    model = load_model(model_path, ip)
    print(f"[arch] {ip.id}: model {model_path.name}, {len(scenarios)} scenario(s)",
          file=out, flush=True)
    records = [run_scenario(model, sc) for sc in scenarios]
    for r in records:
        print(f"[arch] {r['name']}: {r['status'].upper()}"
              + (f" ({r['detail']})" if "detail" in r else ""), file=out)
        for t in r["targets"]:
            bounds = ", ".join(f"{k} {t[k]}" for k in _BOUNDS if k in t)
            print(f"  {t['metric']} = {t['value']} ({bounds}) {t['status'].upper()}", file=out)
        for k, v in r["metrics"].items():
            if not any(t["metric"] == k for t in r["targets"]):
                print(f"  {k} = {v}", file=out)
    status = "pass" if all(r["status"] == "pass" for r in records) else "fail"
    doc = {"schema": SCHEMA, "ip": ip.id, "model": model_path.name,
           "status": status, "scenarios": records}
    path = output_path(ip, workdir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n")
    print(f"[arch] {status.upper()}: {path}", file=out)
    return 0 if status == "pass" else 1
