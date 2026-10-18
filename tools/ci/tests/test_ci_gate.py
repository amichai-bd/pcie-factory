"""Exercise the actual gate command, then check the workflow supplies all jobs."""

import itertools

import pytest
import yaml

from build.ip import REPO_ROOT
from cli.main import main

WSL_RUNNER = ["self-hosted", "linux", "x64", "wsl"]


@pytest.mark.parametrize("results", list(itertools.product(
    ("success", "failure", "cancelled", "skipped"), repeat=3)))
def test_gate_requires_every_job_to_succeed(results):
    args = ["ci", "gate"]
    for job, result in zip(("tests", "fpga", "asic-synth"), results):
        args += [f"--{job}", result]
    assert main(args) == (0 if results == ("success",) * 3 else 1)


def test_gate_unknown_result_fails_closed():
    assert main(["ci", "gate", "--tests", "success", "--fpga", "success",
                 "--asic-synth", "unexpected"]) == 1


def test_workflow_wires_required_results_and_preserves_guards():
    workflow = yaml.safe_load((REPO_ROOT / ".github/workflows/ci.yml").read_text())
    jobs = workflow["jobs"]
    gate = jobs["ci-gate"]
    assert set(gate["needs"]) == {"ci-tests", "ci-fpga", "ci-asic-synth"}
    assert "always()" in gate["if"]
    step = next(s for s in gate["steps"] if s.get("name") == "Require every CI job")
    assert step["env"] == {"TESTS": "${{ needs.ci-tests.result }}",
                           "FPGA": "${{ needs.ci-fpga.result }}",
                           "ASIC_SYNTH": "${{ needs.ci-asic-synth.result }}"}
    assert step["run"] == './build ci gate --tests "$TESTS" --fpga "$FPGA" --asic-synth "$ASIC_SYNTH"'
    for name in gate["needs"] + ["ci-gate"]:
        job = jobs[name]
        assert "github.event.pull_request.head.repo.full_name == github.repository" in job["if"]
        assert job["runs-on"] == WSL_RUNNER
    for name, kind in (("ci-fpga", "fpga"), ("ci-asic-synth", "asic")):
        job = jobs[name]
        assert "needs" not in job  # parallel to simulation
        assert any(s.get("run") == f"./build ci vendor --kind {kind} $AFFECTED"
                   for s in job["steps"])
    assert jobs["ci-gate-status"]["needs"] == "ci-gate"
    assert "github.event_name != 'pull_request'" in jobs["ci-gate-status"]["if"]


def test_every_workflow_job_runs_only_on_the_wsl_host():
    """Every job of every workflow selects the WSL host's label, the only
    supported self-hosted runner (wiki/bkm/devops.md, Continuous integration)."""
    paths = sorted((REPO_ROOT / ".github/workflows").glob("*.yml"))
    assert paths
    for path in paths:
        jobs = yaml.safe_load(path.read_text())["jobs"]
        assert jobs, path.name
        for name, job in jobs.items():
            assert job["runs-on"] == WSL_RUNNER, f"{path.name}: {name}"
