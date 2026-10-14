"""Load and validate ``cfg/ci.yaml``, the ci-gate selection rules."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath

import yaml

CONFIG_NAME = "ci.yaml"
_KEYS = {"global", "shared_dv", "models", "arch", "tools_tests"}
_GLOBAL_KEYS = {"prefixes", "files"}


class CiConfigError(Exception):
    pass


@dataclass(frozen=True)
class CiRules:
    global_prefixes: tuple[str, ...]
    global_files: tuple[str, ...]
    shared_dv: tuple[str, ...]
    models: str
    arch: str
    tools_tests: tuple[str, ...]

    def is_global(self, rel: str) -> bool:
        """True when ``rel`` (relative to the repository root) selects every IP."""
        return rel in self.global_files or rel.startswith(self.global_prefixes)


def _paths(where: str, value, *, trailing_slash: bool = False) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(v, str) and v for v in value):
        raise CiConfigError(f"{where}: expected a list of non-empty strings")
    for v in value:
        p = PurePosixPath(v.rstrip("/"))
        if p.is_absolute() or ".." in p.parts or str(p) in ("", "."):
            raise CiConfigError(f"{where}: {v!r} must be a relative path inside the tree")
        if trailing_slash and not v.endswith("/"):
            raise CiConfigError(f"{where}: prefix {v!r} must end with '/'")
    return tuple(value)


def load_rules(cfg_dir: Path) -> CiRules:
    path = Path(cfg_dir) / CONFIG_NAME
    try:
        data = yaml.safe_load(path.read_text())
    except OSError as e:
        raise CiConfigError(f"{path}: cannot read: {e.strerror}") from None
    except yaml.YAMLError as e:
        raise CiConfigError(f"{path}: invalid YAML: {e}") from None
    if not isinstance(data, dict):
        raise CiConfigError(f"{path}: top level must be a mapping")
    missing, extra = _KEYS - set(data), set(data) - _KEYS
    if missing or extra:
        raise CiConfigError(
            f"{path}: keys must be exactly {sorted(_KEYS)}"
            + (f"; missing {sorted(missing)}" if missing else "")
            + (f"; unknown {sorted(extra)}" if extra else "")
        )
    g = data["global"]
    if not isinstance(g, dict) or set(g) != _GLOBAL_KEYS:
        raise CiConfigError(f"{path}: global: must be a mapping with keys {sorted(_GLOBAL_KEYS)}")
    dirs = {}
    for key in ("models", "arch"):
        value = data[key]
        if not isinstance(value, str):
            raise CiConfigError(f"{path}: {key}: expected a string")
        _paths(f"{path}: {key}", [value])
        dirs[key] = value.rstrip("/")
    return CiRules(
        global_prefixes=_paths(f"{path}: global.prefixes", g["prefixes"], trailing_slash=True),
        global_files=_paths(f"{path}: global.files", g["files"]),
        shared_dv=_paths(f"{path}: shared_dv", data["shared_dv"]),
        models=dirs["models"],
        arch=dirs["arch"],
        tools_tests=_paths(f"{path}: tools_tests", data["tools_tests"]),
    )
