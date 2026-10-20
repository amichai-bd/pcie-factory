"""The consolidated error phases keep their original independent RNG streams."""

import hashlib
import importlib.util
import random
import sys
from pathlib import Path
from types import SimpleNamespace

_DV = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_DV.parents[2] / "tools"))
sys.path.insert(0, str(_DV))
_spec = importlib.util.spec_from_file_location("tests.test_link_e2e", _DV / "tests/test_link_e2e.py")
m = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = m
_spec.loader.exec_module(m)


def test_original_fullname_seeds_and_payload_driver_streams(monkeypatch):
    base = m._ACCEPTANCE_SEEDS["e2e_throughput_short"]
    monkeypatch.setenv("COCOTB_RANDOM_SEED", str(base))
    for name, original in m._ACCEPTANCE_SEEDS.items():
        expected = original + int(hashlib.sha1(f"tests.test_link_e2e.{name}".encode()).hexdigest(), 16)
        assert m.acceptance_seed(name) == expected
        holders = [SimpleNamespace(rng=random.Random()) for _ in range(6)]
        env = SimpleNamespace(rng=random.Random(), ends=holders[:2],
                              injectors=[holders[2], holders[4]], channels=[holders[3], holders[5]],
                              log=SimpleNamespace(info=lambda *args: None))
        m.acceptance_rng(env, name)
        original_rng = random.Random(expected)
        child_seeds = [original_rng.getrandbits(32) for _ in range(2)]
        original_rng.randrange(64)
        child_seeds += [original_rng.getrandbits(32) for _ in range(4)]
        assert [env.rng.getrandbits(32) for _ in range(8)] == [original_rng.getrandbits(32) for _ in range(8)]
        for obj, child in zip(holders, child_seeds):
            assert obj.rng.getrandbits(32) == random.Random(child).getrandbits(32)
    monkeypatch.setenv("COCOTB_RANDOM_SEED", str(base + 1))
    name = "e2e_disparity"
    expected = m._ACCEPTANCE_SEEDS[name] + int(hashlib.sha1(f"tests.test_link_e2e.{name}".encode()).hexdigest(), 16)
    assert m.acceptance_seed(name) == expected + 1


def test_liveness_counts_busy_time_not_prior_idle(monkeypatch):
    """Actual scoreboard/checker: idle, inclusive bound, new work, progress, drain."""
    env_module = sys.modules[m.Env.__module__]
    monkeypatch.setattr(env_module, "now_ps", lambda: 0)
    monkeypatch.setattr(env_module, "tlp_kind", lambda _: "posted")
    monkeypatch.setattr(env_module, "payload_bytes", lambda _: 0)
    monkeypatch.setattr(env_module.COV_TRAFFIC, "sample", lambda **kw: None)
    a = SimpleNamespace(name="a", cycle=0, violations=[])
    b = SimpleNamespace(name="b", cycle=0)
    sb = env_module.Scoreboard("ba")
    env = SimpleNamespace(ends=[a, b], sb={"ba": sb})
    check = lambda: m.Env._liveness(env, a)
    bound = env_module.LIVENESS
    # Idle far beyond the bound must not consume the next TLP's allowance.
    a.cycle = 3 * bound
    check()
    sb.accept(b"first", a.cycle)
    # The former last-delivery predicate falsely fails this exact state.
    assert a.cycle - max(sb.last_delivery, 0) > bound
    check()
    assert not a.violations
    start = a.cycle
    a.cycle = start + bound
    check()
    assert not a.violations  # preserve the original strict > boundary
    # A later acceptance cannot forgive a genuinely stuck earlier packet.
    sb.accept(b"second", a.cycle)
    a.cycle += 1
    check()
    assert len(a.violations) == 1 and "2 TLPs outstanding" in a.violations[0]
    # Delivery refreshes progress for remaining work, using receiver cycles.
    sb.deliver(b"first", a.cycle)
    a.violations.clear()
    start = a.cycle
    a.cycle += bound
    check()
    assert not a.violations
    a.cycle += 1
    check()
    assert len(a.violations) == 1
    sb.deliver(b"second", a.cycle)
    assert sb.pending_since is None
    a.violations.clear()
    a.cycle += 3 * bound
    check()
    sb.accept(b"new busy epoch", a.cycle)
    check()
    assert not a.violations and sb.pending_since == a.cycle
