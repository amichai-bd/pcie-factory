"""The shared acceptance phases retain the standalone stimulus seed domains."""

import hashlib
import importlib.util
import random
import sys
from pathlib import Path
from types import SimpleNamespace

_DV = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_DV.parents[2] / "tools"))
sys.path.insert(0, str(_DV))
_spec = importlib.util.spec_from_file_location("tests.test_core_e2e", _DV / "tests/test_core_e2e.py")
m = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = m
_spec.loader.exec_module(m)


def test_original_fullname_seed_domains_and_alternate_seed(monkeypatch):
    for seeds in (m._FUNCTIONAL_SEEDS, m._ERROR_SEEDS):
        base = next(iter(seeds.values()))
        monkeypatch.setenv("COCOTB_RANDOM_SEED", str(base))
        for name, original in seeds.items():
            expected = original + int(hashlib.sha1(f"tests.test_core_e2e.{name}".encode()).hexdigest(), 16)
            assert m.scenario_seed(name, seeds) == expected
            env = SimpleNamespace(rng=random.Random(), app=SimpleNamespace(rng=random.Random()),
                                  host=SimpleNamespace(model=SimpleNamespace()),
                                  log=SimpleNamespace(info=lambda *args: None))
            m.scenario_rng(env, name, seeds)
            original_rng = random.Random(expected)
            app_rng = random.Random(original_rng.getrandbits(32))
            assert [env.rng.getrandbits(32) for _ in range(8)] == [original_rng.getrandbits(32) for _ in range(8)]
            assert [env.app.rng.getrandbits(32) for _ in range(8)] == [app_rng.getrandbits(32) for _ in range(8)]
        monkeypatch.setenv("COCOTB_RANDOM_SEED", str(base + 1))
        for name, original in seeds.items():
            expected = original + int(hashlib.sha1(f"tests.test_core_e2e.{name}".encode()).hexdigest(), 16)
            assert m.scenario_seed(name, seeds) == expected + 1


def test_error_phase_delta_preserves_raw_history():
    env = SimpleNamespace(tl_err_count=[9, 4, 0, 0, 0, 0, 0, 0, 0])
    assert m.tl_errs(env, 0) == 9
    env.phase_tl_baseline = list(env.tl_err_count)
    assert m.tl_errs(env, 0) == 0
    env.tl_err_count[0] += 1
    assert m.tl_errs(env, 0) == 1 and m.tl_errs(env, 1) == 0
    assert env.tl_err_count[:2] == [10, 4]
