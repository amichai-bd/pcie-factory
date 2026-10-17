"""Unit tests for ./build pr check: no network, fixture PR bodies.

Each rule is pinned by a mutation of the passing fixture body that must
raise exactly that rule (see wiki/tools/pr/SPEC.md#tests).
"""

import io
from pathlib import Path

import pytest

from build.ip import REPO_ROOT
from cli.main import main
from labels import core as labels
from pr import check as pc

FIXTURES = Path(__file__).resolve().parent / "fixtures"
GOOD = (FIXTURES / "good.md").read_text()
PARTIAL = (FIXTURES / "partial.md").read_text()
HEAD = "0123456789abcdef0123456789abcdef01234567"
OTHER = "fedcba9876543210fedcba9876543210fedcba98"
CFG = REPO_ROOT / "cfg"
RTL_FILES = ("src/rtl/a/a_core.sv", "wiki/src/rtl/a/MAS.md")
GOOD_LABELS = ("type:feat", "area:rtl", "ip:a")


@pytest.fixture(scope="module")
def rules():
    return pc.load_rules(CFG)


@pytest.fixture(scope="module")
def standard():
    return labels.load(CFG / "labels.yaml", ["a"])


def run(rules, standard, body=GOOD, head=HEAD, files=RTL_FILES, labs=GOOD_LABELS):
    pr = pc.Pr(7, body, head, files, labs)
    return pc.check(pr, rules, standard)


def rules_of(findings):
    return sorted({f.rule for f in findings})


def test_good_body_passes(rules, standard):
    assert run(rules, standard) == ([], [])


def test_partial_body_passes(rules, standard):
    assert run(rules, standard, PARTIAL, files=("wiki/bkm/x.md",)) == ([], [])


# (rule, old text, new text, message substring): each edit of the good body
# must raise exactly that rule, with that message.
MUTANTS = [
    ("head-sha", f"At head {HEAD}.", "Ran at the head.", "no 'At head <sha>' line"),
    ("head-sha", f"At head {HEAD}.", "At head 0123456.", "full 40-character SHA"),
    ("commands", "| Command | Result |", "| Step | Result |", "no '| Command | Result |' table"),
    ("commands", "| Command | Result |\n| --- | --- |\n", "| Command | Result |\n",
     "no separator row"),
    ("commands", "| `./build check` |", "| ./build check |", "not quoted exactly"),
    ("commands", "| `./build check` | PASS (10 s) |", "| `./build check` |  |", "no result"),
    ("versions", " cocotb 2.1.0.", ".", "no cocotb version"),
    ("versions", " pytest 9.1.1,", "", "no pytest version"),
    ("seeds", "Seeds: per the crc32 seed rule.", "", "no line states the seeds"),
    ("levels", "L1 10/10 passed", "L1 passed", "no 'L1 <passed>/<total>' result"),
    ("levels", "CPU 20.5 s, ", "", "no 'CPU <s> s' after L0"),
    ("budget", "CPU 300.0 s", "CPU 700.0 s", "L1 CPU 700.0 s is over its 600 s budget"),
    ("mutants", "| Mutant |", "| Change |", "no mutation table"),
    ("mutants", "| --- | --- | --- | --- | --- |\n", "", "mutation table has no separator row"),
    ("mutants", "| M1 | wrap at 14, not 15 | `wrap` | 7 | FAIL at cycle 42: count 14, "
                "expected 15 |\n", "", "mutation table has no rows"),
    ("not-run", "### Not run\nNone.\n", "", "no '### Not run' subsection"),
    ("not-run", "### Not run\nNone.\n", "### Not run\n\n", "'### Not run' list is empty"),
    ("keywords", "Follow-up: #14", "Part 2 closes #3; follow-up: #14",
     "#3 is 'Part of' and also next to a closing keyword"),
    ("keywords", "Closes #12.", "Closes #12, fixes #13.", "more than one issue: #12, #13"),
    ("keywords", "Closes #12. Part of epic #3.", "Implements #12.", "no 'Closes #<n>'"),
    ("validation", "\n## Validation\n", "\n## Checks\n", "no '## Validation' section"),
]


@pytest.mark.parametrize("rule, old, new, msg", MUTANTS)
def test_each_rule_catches_its_mutant(rules, standard, rule, old, new, msg):
    assert GOOD.count(old) == 1, old
    findings, _ = run(rules, standard, GOOD.replace(old, new))
    assert rules_of(findings) == [rule], findings
    assert any(msg in f.message for f in findings), findings


def test_partial_pr_that_closes_its_parent(rules, standard):
    body = PARTIAL.replace("Docs only.", "Docs only; this closes #12.")
    findings, _ = run(rules, standard, body, files=())
    assert [f.message for f in findings] == [
        "#12 is 'Part of' and also next to a closing keyword (close/fix/resolve), "
        "so merging would close it"]


def test_head_mismatch(rules, standard):
    findings, _ = run(rules, standard, head=OTHER)
    assert [f.message for f in findings] == \
        [f"'At head {HEAD}' does not match the PR head {OTHER}"]


def test_budget_overrun_with_explanation_passes(rules, standard):
    body = GOOD.replace("CPU 300.0 s", "CPU 700.0 s").replace(
        "### Not run", "L1 budget: the new sweep adds 40 seeds (#20).\n\n### Not run")
    assert run(rules, standard, body) == ([], [])


def test_budget_comes_from_cfg_levels(rules):
    assert rules.budgets[1] == 600 and rules.budgets[0] == 120


def test_mutants_only_for_mutation_paths(rules, standard):
    body = GOOD.replace("| Mutant |", "| Change |")
    assert run(rules, standard, body, files=("tools/x.py",)) == ([], [])
    findings, notes = run(rules, standard, body, files=None)
    assert findings == [] and notes == ["mutants: changed files unknown (no --files); "
                                        "not checked"]


def test_keywords_in_code_are_not_counted(rules, standard):
    body = GOOD.replace("Follow-up: #14", "Follow-up: #14; the skill says `closes #3`")
    assert run(rules, standard, body) == ([], [])


def test_versions_only_for_tools_run(rules, standard):
    # The partial body ran only ./build check: no version is required.
    assert rules_of(run(rules, standard, PARTIAL, files=())[0]) == []
    body = PARTIAL.replace("| `./build check` | PASS |",
                           "| `./build sim --ip a --test t --seed 3` | PASS |")
    findings, _ = run(rules, standard, body, files=())
    assert [f.message for f in findings] == [
        "no Verilator version stated ('Verilator <x.y>')",
        "no cocotb version stated ('cocotb <x.y>')",
        "no Python version stated ('Python <x.y>')"]


def test_labels_rule(rules, standard):
    findings, _ = run(rules, standard, labs=("type:feat",))
    assert [(f.rule, f.message) for f in findings] == \
        [("labels", "needs at least one area:* label")]
    findings, notes = run(rules, standard, labs=None)
    assert findings == [] and notes == ["labels: PR labels unknown (no --labels); not checked"]


def test_cells_respect_code_spans_and_escapes():
    assert pc.cells("| `a | b` | c \\| d |") == ["`a | b`", "c | d"]


def test_tables_detect_the_separator():
    t = pc.tables("| A | B |\n| --- | :-: |\n| 1 | 2 |\n\n| C |\n| 3 |")
    assert [(x.header, x.separator, x.rows) for x in t] == \
        [(["A", "B"], True, [["1", "2"]]), (["C"], False, [["3"]])]


def test_report_format(rules, standard, capsys):
    pr = pc.Pr(7, GOOD, OTHER, RTL_FILES, GOOD_LABELS)
    findings, notes = pc.check(pr, rules, standard)
    buf = io.StringIO()
    assert pc.report(pr, findings, notes, buf) == 1
    assert buf.getvalue().splitlines() == [
        f"pr check: #7 at {OTHER}",
        f"error: #7: [head-sha] 'At head {HEAD}' does not match the PR head {OTHER}",
        "#7: evidence FAIL"]


# --- configuration --------------------------------------------------------------

@pytest.mark.parametrize("text, msg", [
    ("[", "invalid YAML"),
    ("mutation_paths: [a]\n", "exactly the keys"),
    ("mutation_paths: []\nseeded_commands: [x]\nversions: [{when: x, need: [y]}]\n",
     "mutation_paths must be a non-empty list"),
    ("mutation_paths: [a]\nseeded_commands: [x]\nversions: [{when: x}]\n",
     "versions[0] must have"),
    ("mutation_paths: [a]\nseeded_commands: [x]\nversions: []\n", "versions must be"),
])
def test_malformed_config(tmp_path, text, msg):
    (tmp_path / "pr.yaml").write_text(text)
    (tmp_path / "levels.yaml").write_text((CFG / "levels.yaml").read_text())
    with pytest.raises(pc.PrError, match=msg.replace("[", r"\[")):
        pc.load_rules(tmp_path)


def test_missing_config(tmp_path):
    with pytest.raises(pc.PrError, match="cannot read"):
        pc.load_rules(tmp_path)


# --- GitHub (read-only) --------------------------------------------------------

class FakeGh:
    repo = "o/r"

    def __init__(self, body, files):
        self.calls = []
        self.body, self.files = body, files

    def api(self, method, path, fields=None):
        self.calls.append((method, path, fields))
        if path == "repos/o/r/pulls/7":
            return {"body": self.body, "head": {"sha": HEAD},
                    "labels": [{"name": n} for n in GOOD_LABELS]}
        page = int(path.rsplit("page=", 1)[1])
        return self.files[(page - 1) * 100:page * 100]


def test_fetch_paginates_skips_removed_and_only_reads():
    files = [{"filename": f"f{i}", "status": "modified"} for i in range(150)]
    files[3]["status"] = "removed"
    gh = FakeGh(None, files)
    pr = pc.fetch(gh, 7)
    assert pr.body == "" and pr.head_sha == HEAD and pr.labels == GOOD_LABELS
    assert len(pr.files) == 149 and "f3" not in pr.files
    assert [c[1] for c in gh.calls] == ["repos/o/r/pulls/7",
                                        "repos/o/r/pulls/7/files?per_page=100&page=1",
                                        "repos/o/r/pulls/7/files?per_page=100&page=2"]
    assert {c[0] for c in gh.calls} == {"GET"} and all(c[2] is None for c in gh.calls)


def test_cli_github_mode(monkeypatch, tmp_path, capsys):
    gh = FakeGh(GOOD, [{"filename": f, "status": "added"} for f in RTL_FILES])
    monkeypatch.setattr(labels, "Gh", lambda repo: gh)
    argv = ["pr", "check", "7", "--repo", "o/r", "--src-root", str(FIXTURES / "nosrc")]
    # ip:a is unknown without a source tree: the labels rule fails, nothing else.
    assert main(argv) == 1
    out = capsys.readouterr().out
    assert "error: #7: [labels] label 'ip:a' is not a known ip value" in out
    assert "#7: evidence FAIL" in out
    assert {c[0] for c in gh.calls} == {"GET"}


def test_cli_file_mode(tmp_path, capsys):
    body = tmp_path / "pr.md"
    body.write_text(GOOD)
    argv = ["pr", "check", "7", "--body-file", str(body), "--head", HEAD,
            "--files", *RTL_FILES, "--labels", "type:feat,area:rtl"]
    assert main(argv) == 0
    assert capsys.readouterr().out.splitlines() == [f"pr check: #7 at {HEAD}",
                                                    "#7: evidence OK"]
    assert main(argv[:6] + [OTHER] + argv[7:]) == 1


@pytest.mark.parametrize("extra, msg", [
    ([], "--body-file needs --head"),
    (["--head", "abc"], "full 40-character"),
])
def test_cli_file_mode_errors(tmp_path, capsys, extra, msg):
    body = tmp_path / "pr.md"
    body.write_text(GOOD)
    assert main(["pr", "check", "7", "--body-file", str(body), *extra]) == 2
    assert msg in capsys.readouterr().err


def test_cli_offline_options_need_body_file(capsys):
    assert main(["pr", "check", "7", "--head", HEAD]) == 2
    assert "need --body-file" in capsys.readouterr().err


def test_cli_missing_body_file(tmp_path, capsys):
    assert main(["pr", "check", "7", "--body-file", str(tmp_path / "no.md"),
                 "--head", HEAD]) == 2
    assert "cannot read" in capsys.readouterr().err
