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
from checks import neutrality as nt
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


# Neutral placeholders: "widget" is the design-target word, alpha and beta
# the discovered IP ids.
NEUTRAL = pc.Neutral(nt.word_pattern(("widget",)), nt.ip_pattern(("alpha", "beta")),
                     nt.slug_pattern("o/r"))


def run(rules, standard, body=GOOD, head=HEAD, files=RTL_FILES, labs=GOOD_LABELS,
        added=None, neutral=NEUTRAL):
    pr = pc.Pr(7, body, head, files, labs, {} if added is None else added)
    return pc.check(pr, rules, standard, neutral)


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
    ("stale-sha", "`<lock>` stands for", f"L1 also passed at {OTHER}. `<lock>` stands for",
     f"labelled 'at {OTHER}', not the PR head"),
    ("stale-sha", "`<lock>` stands for", f"CI green @{OTHER}. `<lock>` stands for",
     f"labelled 'at {OTHER}'"),
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
    ("budget", "CPU 300.0 s", "CPU 1300.0 s", "L1 CPU 1300.0 s is over its 1200 s budget"),
    ("mutants", "| Mutant |", "| Change |", "no mutation table"),
    ("mutants", "| --- | --- | --- | --- | --- |\n", "", "mutation table has no separator row"),
    ("mutants", "| M1 | wrap at 14, not 15 | `wrap` | 7 | FAIL at cycle 42: count 14, "
                "expected 15 |\n", "", "mutation table has no rows"),
    ("not-run", "### Not run\nNone.\n", "", "no '### Not run' subsection"),
    ("not-run", "### Not run\nNone.\n", "### Not run\n\n", "'### Not run' list is empty"),
    ("keywords", "Follow-up: #14", "Part 2 closes #3; follow-up: #14",
     "#3 is 'Part of' and also next to a closing keyword"),
    ("keywords", "Closes #12.", "Closes #12, fixes #13.", "more than one issue: #12, #13"),
    ("rescope", "Follow-up: #14 tracks the wider counter.",
     "The wider-counter acceptance item moved to #14.", "closes #12 but says work moved to #14"),
    ("rescope", "Follow-up: #14 tracks the wider counter.",
     "Coverage floors deferred to issue #14.", "add 'Acceptance moved: #14"),
    ("keywords", "Closes #12. Part of epic #3.", "Implements #12.", "no 'Closes #<n>'"),
    ("validation", "\n## Validation\n", "\n## Checks\n", "no '## Validation' section"),
    # HTML comments are stripped: commented-out evidence satisfies no rule.
    ("seeds", "Seeds: per the crc32 seed rule.", "<!-- Seeds: per the crc32 seed rule. -->",
     "no line states the seeds"),
    ("not-run", "### Not run\nNone.\n", "<!--\n### Not run\nNone.\n-->\n",
     "no '### Not run' subsection"),
    ("levels", "L1 10/10 passed", "<!-- L1 10/10 passed -->", "no 'L1 <passed>/<total>' result"),
]


@pytest.mark.parametrize("rule, old, new, msg", MUTANTS)
def test_each_rule_catches_its_mutant(rules, standard, rule, old, new, msg):
    assert GOOD.count(old) == 1, old
    findings, _ = run(rules, standard, GOOD.replace(old, new))
    assert rules_of(findings) == [rule], findings
    assert any(msg in f.message for f in findings), findings


# (old text, new text): input forms the SPEC promises; each edit of the good
# body must still pass every rule.
FORMS = [
    # Exactly what `./build regress` prints: `level<N>:` and a space-less `CPU 60.2s`.
    ("L0 4/4 passed, CPU 20.5 s, wall 9 s; L1 10/10 passed, CPU 300.0 s, wall 120 s",
     "level0: 4/4 passed, CPU 20.5s (budget 120s), test wall time 9.0s; "
     "level1: 10/10 passed, CPU 300.0s (budget 1200s), test wall time 120.0s"),
    ("--level 0 --level 1 --ip a", "--level=0 --level=1 --ip a"),
    ("Seeds: per the crc32 seed rule.",
     "Seeds: the [seed rule](wiki/tools/regress/SPEC.md#seeds)."),
    # A comment may hold anything, even a stale SHA or a broken table.
    ("### Not run", f"<!-- at {OTHER} | Command |\n-->\n### Not run"),
]


@pytest.mark.parametrize("old, new", FORMS)
def test_promised_forms_pass(rules, standard, old, new):
    assert GOOD.count(old) == 1, old
    assert run(rules, standard, GOOD.replace(old, new)) == ([], [])


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
    body = GOOD.replace("CPU 300.0 s", "CPU 1300.0 s").replace(
        "### Not run", "L1 budget: the new sweep adds 40 seeds (#20).\n\n### Not run")
    assert run(rules, standard, body) == ([], [])


def test_budget_comes_from_cfg_levels(rules):
    assert rules.budgets[1] == 1200 and rules.budgets[0] == 120


def test_mutants_only_for_mutation_paths(rules, standard):
    body = GOOD.replace("| Mutant |", "| Change |")
    assert run(rules, standard, body, files=("tools/x.py",)) == ([], [])
    findings, notes = run(rules, standard, body, files=None)
    assert findings == [] and notes == [
        "mutants: changed files unknown (no --files); not checked",
        "neutrality: changed files unknown (no --files or --diff); not checked"]


def test_regression_list_moves_need_no_mutants(rules, standard):
    body = GOOD.replace("| Mutant |", "| Change |")
    files = ("src/dv/a/regress/level0.yaml", "src/dv/a/regress/level1.yaml",
             "wiki/src/dv/a/DV_PLAN.md")
    assert run(rules, standard, body, files=files) == ([], [])
    findings, _ = run(rules, standard, body, files=files + ("src/dv/a/tests/test_a.py",))
    assert rules_of(findings) == ["mutants"]


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
    pr = pc.Pr(7, GOOD, OTHER, RTL_FILES, GOOD_LABELS, {})
    findings, notes = pc.check(pr, rules, standard, NEUTRAL)
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
    ("mutation_paths: []\nmutation_exempt: []\nseeded_commands: [x]\n"
     "versions: [{when: x, need: [y]}]\n", "mutation_paths must be a non-empty list"),
    ("mutation_paths: [a]\nmutation_exempt: x\nseeded_commands: [x]\n"
     "versions: [{when: x, need: [y]}]\n", "mutation_exempt must be a list"),
    ("mutation_paths: [a]\nmutation_exempt: []\nseeded_commands: [x]\nversions: [{when: x}]\n",
     "versions[0] must have"),
    ("mutation_paths: [a]\nmutation_exempt: []\nseeded_commands: [x]\nversions: []\n",
     "versions must be"),
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
    assert capsys.readouterr().out.splitlines() == [
        f"pr check: #7 at {HEAD}",
        "note: #7: neutrality: added lines unknown (no --diff); only paths checked",
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


# --- stale SHA, rescope and neutrality ----------------------------------------------

def test_head_sha_cited_twice_is_not_stale(rules, standard):
    body = GOOD.replace("`<lock>` stands for", f"ci-gate green at {HEAD}. `<lock>` stands for")
    assert run(rules, standard, body) == ([], [])


def test_declared_rescope_passes(rules, standard):
    body = GOOD.replace("Follow-up: #14 tracks the wider counter.",
                        "The wider-counter item moved to #14.\n\nAcceptance moved: #14, agreed "
                        "in https://github.com/o/r/issues/12#issuecomment-99")
    assert run(rules, standard, body) == ([], [])
    # Without the link to the closed issue's comment the declaration does not count.
    body = body.replace("https://github.com/o/r/issues/12#issuecomment-99", "chat")
    assert rules_of(run(rules, standard, body)[0]) == ["rescope"]


def test_rescope_needs_a_closing_keyword(rules, standard):
    body = PARTIAL.replace("Docs only.", "Docs only; the rest moved to #20.")
    assert run(rules, standard, body, files=())[0] == []


@pytest.mark.parametrize("path, line, hit", [
    ("wiki/bkm/x.md", "run `beta` first", "beta"),                 # whole code span
    ("wiki/bkm/x.md", "see src/rtl/alpha/x.sv", "alpha"),          # path segment
    ("tools/t/a.py", "IPS = ['alpha']", "alpha"),                  # quoted string
    ("tools/t/a.py", "x = 'alpha/y'", "alpha"),                    # leading segment
    ("wiki/bkm/x.md", "`./build regress --ip beta`", "beta"),      # selector
    ("wiki/bkm/x.md", "entry `alpha:smoke` failed", "alpha"),      # <ip>:<test>
    ("wiki/bkm/x.md", "the alpha IP and the beta sub-IP", "alpha"),
    (".claude/skills/s/SKILL.md", "a Widget-Bus target", "Widget"),  # word list
])
def test_neutrality_flags_identifiers_and_words(rules, standard, path, line, hit):
    findings, _ = run(rules, standard, files=RTL_FILES + (path,),
                      added={path: ("context", line)})
    assert rules_of(findings) == ["neutrality"], findings
    assert all(f.message.startswith(f"{path}: added line {line.strip()[:60]!r} names")
               for f in findings)
    assert any(repr(hit) in f.message for f in findings)


@pytest.mark.parametrize("path, line", [
    ("wiki/bkm/x.md", "an alpha release and the beta test"),       # plain English
    ("tools/t/a.py", "from labels import alpha"),                  # module, not a string
    ("wiki/bkm/x.md", "die/alpha area, alpha.py"),                 # last segment, stem
    ("src/rtl/alpha/a.sv", "// the widget alpha IP"),              # exempt tree
    ("wiki/src/arch/alpha/HAS.md", "the `alpha` widget"),          # exempt tree
    ("wiki/tracker/data/x.json", '"alpha": 1'),                    # exempt tree
    ("wiki/bkm/x.md", "https://github.com/o/r/blob/main/README.md"),
    ("tools/wiki_site/theme/vendor/x.min.js", "var a='alpha'"),     # vendored, minified
])
def test_neutrality_passes(rules, standard, path, line):
    assert run(rules, standard, files=RTL_FILES + (path,), added={path: (line,)}) == ([], [])


def test_neutrality_flags_paths_and_blanks_the_word_list(rules, standard):
    path = "tools/alpha/run.py"
    findings, _ = run(rules, standard, files=RTL_FILES + (path,), added={path: ()})
    assert [f.message for f in findings] == [f"{path}: path names 'alpha'"]
    # cfg/checks.yaml may hold the word list itself, but nothing else.
    words = "  words: [widget, widget bus]"
    assert run(rules, standard, files=RTL_FILES + (nt.CONFIG,),
               added={nt.CONFIG: (words,)}) == ([], [])
    findings, _ = run(rules, standard, files=RTL_FILES + (nt.CONFIG,),
                      added={nt.CONFIG: (words, "# a widget comment")})
    assert rules_of(findings) == ["neutrality"]


def test_neutrality_notes(rules, standard):
    pr = pc.Pr(7, GOOD, HEAD, RTL_FILES + ("tools/t/blob.bin",), GOOD_LABELS, {},
               ("tools/t/blob.bin",))
    findings, notes = pc.check(pr, rules, standard, NEUTRAL)
    assert findings == [] and notes == [
        "neutrality: tools/t/blob.bin: GitHub gave no patch (binary or too large); "
        "only its path checked"]
    assert run(rules, standard, neutral=None)[1] == [
        "neutrality: no source tree or word list given; not checked"]
    pr = pc.Pr(7, GOOD, HEAD, RTL_FILES, GOOD_LABELS)  # added lines unknown
    assert pc.check(pr, rules, standard, NEUTRAL)[1] == [
        "neutrality: added lines unknown (no --diff); only paths checked"]


DIFF = """diff --git a/wiki/bkm/x.md b/wiki/bkm/x.md
--- a/wiki/bkm/x.md
+++ b/wiki/bkm/x.md
@@ -1,2 +1,2 @@
 kept
-old
+run `alpha` first
+++not a header, an added line starting with ++
diff --git a/tools/gone.py b/tools/gone.py
--- a/tools/gone.py
+++ /dev/null
@@ -1 +0,0 @@
-x
diff --git a/tools/new.py b/tools/new.py
new file mode 100644
--- /dev/null
+++ b/tools/new.py
@@ -0,0 +1 @@
+print(1)
"""


def test_parse_diff():
    assert pc.parse_diff(DIFF) == {"wiki/bkm/x.md": ("run `alpha` first",
                                                     "++not a header, an added line "
                                                     "starting with ++"),
                                   "tools/new.py": ("print(1)",)}


def test_load_neutral_discovers_ips_and_skips_top_level_names(tmp_path):
    (tmp_path / "cfg").mkdir()
    (tmp_path / "cfg" / "checks.yaml").write_text(
        "neutrality:\n  words: [widget]\n  repo_slug: o/r\n")
    for ip in ("alpha", "cfg"):
        d = tmp_path / "src" / "rtl" / ip
        d.mkdir(parents=True)
        (d / f"{ip}.rtl.f").write_text("")
    n = pc.load_neutral(tmp_path, tmp_path / "src")
    assert n.ips.search("`alpha`") and not n.ips.search("`cfg`")
    assert n.words.search("Widget") and n.slug.search("https://github.com/o/r/x")
    assert pc.load_neutral(tmp_path, tmp_path / "nosrc").ips is None
    (tmp_path / "cfg" / "checks.yaml").write_text("nope: 1\n")
    with pytest.raises(pc.PrError, match="neutrality"):
        pc.load_neutral(tmp_path, tmp_path / "src")


def test_fetch_keeps_added_lines_and_notes_missing_patches():
    files = [{"filename": "wiki/bkm/x.md", "status": "modified",
              "patch": "@@ -1 +1 @@\n-old\n+new `alpha`"},
             {"filename": "tools/t/blob.bin", "status": "added"}]
    pr = pc.fetch(FakeGh("", files), 7)
    assert pr.added == {"wiki/bkm/x.md": ("new `alpha`",)}
    assert pr.unpatched == ("tools/t/blob.bin",)


def test_cli_file_mode_with_diff(tmp_path, capsys):
    body = tmp_path / "pr.md"
    body.write_text(PARTIAL)
    diff = tmp_path / "pr.diff"
    diff.write_text(DIFF.replace("alpha", "widget"))
    argv = ["pr", "check", "7", "--body-file", str(body), "--head", HEAD,
            "--diff", str(diff), "--labels", "type:docs,area:wiki"]
    words = nt.load_config(REPO_ROOT).words
    diff.write_text(DIFF.replace("`alpha`", f"`{words[0]}`"))
    assert main(argv) == 1
    out = capsys.readouterr().out
    assert "[neutrality] wiki/bkm/x.md: added line" in out
    diff.write_text(DIFF.replace("`alpha`", "the checks"))
    assert main(argv) == 0
    assert main(["pr", "check", "7", "--diff", str(diff)]) == 2
    assert "need --body-file" in capsys.readouterr().err
