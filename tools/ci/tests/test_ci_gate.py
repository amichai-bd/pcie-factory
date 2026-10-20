"""Exercise the actual gate command, then check the workflow supplies all jobs."""

import itertools

import pytest
import yaml

from build.ip import REPO_ROOT
from cli.main import main

WSL_RUNNER = ["self-hosted", "linux", "x64", "wsl"]


@pytest.mark.parametrize("results", list(itertools.product(
    ("success", "failure", "cancelled", "skipped"), repeat=2)))
def test_gate_requires_every_job_to_succeed(results):
    args = ["ci", "gate"]
    for job, result in zip(("tests", "fpga"), results):
        args += [f"--{job}", result]
    assert main(args) == (0 if results == ("success",) * 2 else 1)


@pytest.mark.parametrize("args", [["--tests", "success", "--fpga", "unexpected"],
                                  ["--tests", "", "--fpga", "success"]])
def test_gate_unknown_result_fails_closed(args):
    assert main(["ci", "gate", *args]) == 1


@pytest.mark.parametrize("args", [["--tests", "success"], ["--fpga", "success"],
                                  ["--tests", "success", "--fpga", "success",
                                   "--asic-synth", "success"]])
def test_gate_requires_exactly_tests_and_fpga(args):
    # ASIC synthesis is a nightly (L2) check: the gate takes no ASIC result
    # (wiki/tools/ci/SPEC.md#build-ci-gate); a missing job is a usage error.
    with pytest.raises(SystemExit) as e:
        main(["ci", "gate", *args])
    assert e.value.code == 2


def test_workflow_wires_required_results_and_preserves_guards():
    workflow = yaml.safe_load((REPO_ROOT / ".github/workflows/ci.yml").read_text())
    jobs = workflow["jobs"]
    gate = jobs["ci-gate"]
    assert set(gate["needs"]) == {"ci-tests", "ci-fpga"}
    assert not any("asic" in name for name in jobs)  # ASIC synthesis is nightly only
    assert "always()" in gate["if"]
    step = next(s for s in gate["steps"] if s.get("name") == "Require every CI job")
    assert step["env"] == {"TESTS": "${{ needs.ci-tests.result }}",
                           "FPGA": "${{ needs.ci-fpga.result }}"}
    assert step["run"] == './build ci gate --tests "$TESTS" --fpga "$FPGA"'
    for name in gate["needs"] + ["ci-gate"]:
        job = jobs[name]
        assert "github.event.pull_request.head.repo.full_name == github.repository" in job["if"]
        assert job["runs-on"] == WSL_RUNNER
    job = jobs["ci-fpga"]
    assert "needs" not in job  # parallel to simulation
    assert any(s.get("run") == "./build ci vendor --kind fpga $AFFECTED" for s in job["steps"])
    assert jobs["ci-gate-status"]["needs"] == "ci-gate"
    assert "github.event_name != 'pull_request'" in jobs["ci-gate-status"]["if"]


def test_every_workflow_job_runs_only_on_the_wsl_host():
    """Every job of every workflow selects the WSL host's label, the only
    supported self-hosted runner (wiki/bkm/devops.md, Continuous integration)."""
    workflows = REPO_ROOT / ".github/workflows"
    paths = sorted([*workflows.glob("*.yml"), *workflows.glob("*.yaml")])
    assert paths
    for path in paths:
        jobs = yaml.safe_load(path.read_text())["jobs"]
        assert jobs, path.name
        for name, job in jobs.items():
            assert job["runs-on"] == WSL_RUNNER, f"{path.name}: {name}"


def test_selection_comes_from_the_data_guard_only():
    """Both work jobs select per event: HEAD^1 on a pull request; on a push or
    dispatch everything, unless the data guard proves a data-only nightly
    branch and selects its merge base (wiki/tools/ci/SPEC.md#the-workflow).
    No job-level AFFECTED, so a branch name alone never selects anything."""
    jobs = yaml.safe_load((REPO_ROOT / ".github/workflows/ci.yml").read_text())["jobs"]
    for name in ("ci-tests", "ci-fpga"):
        job = jobs[name]
        assert "AFFECTED" not in (job.get("env") or {}), name
        runs = [s.get("run", "") for s in job["steps"]]
        select = next(i for i, r in enumerate(runs) if "--select" in r)
        script = runs[select]
        assert 'echo "AFFECTED=--affected HEAD^1" >> "$GITHUB_ENV"' in script
        assert 'echo "AFFECTED=" >> "$GITHUB_ENV"' in script
        assert ('./build metrics guard --branch "$GITHUB_REF_NAME" --pr-base main '
                '--select "$GITHUB_ENV"') in script
        assert script.index('"AFFECTED="') < script.index("--select")  # the guard alone overrides
        assert all("$AFFECTED" not in r for r in runs[:select]), name
        assert any("$AFFECTED" in r for r in runs[select + 1:]), name
