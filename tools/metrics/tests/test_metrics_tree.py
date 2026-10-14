from metrics import commits
from metrics.tree import TreeReader, count_lines, discover_ips, owner

from conftest import commit, git


def test_count_lines_c_style(cfg):
    text = "// c\nmodule m; // t\n/* a\n b */ wire w;\n\n  /* x */\nendmodule /* y */\n"
    # non-blank: 6; code: module, "wire w;", endmodule
    assert count_lines(text, ".sv", cfg) == (6, 3)


def test_count_lines_hash_and_unknown(cfg):
    assert count_lines("# c\nx = 1\n\n  # d\ny\n", ".py", cfg) == (4, 2)
    assert count_lines("# Title\n\ntext\n", ".md", cfg) == (2, 2)


def test_discover_and_owner():
    paths = ["src/a/rtl/a.rtl.f", "src/a/b/rtl/b.rtl.f", "src/a/rtl/x/rtl/x.rtl.f",
             "src/c/rtl/wrong.rtl.f", "lib/d/rtl/d.rtl.f"]
    ips = discover_ips(paths, "src")
    assert ips == ["a", "a/b"]
    assert owner("src/a/b/dv/t.py", ips, "src") == "a/b"
    assert owner("src/a/dv/t.py", ips, "src") == "a"
    assert owner("src/README.md", ips, "src") is None


def test_code_stats_from_tree(repo, cfg):
    stats, files = TreeReader(repo, cfg).stats("HEAD")
    paths = [f.path for f in files]
    assert "wiki/tracker/data/old.json" not in paths  # excluded
    assert stats["total"]["files"] == len(files) == 8
    assert set(stats["ips"]) == {"blk", "blk/sub"}
    blk = stats["ips"]["blk"]
    assert blk["files"] == 3  # sub-IP files are not counted in the parent
    assert blk["kinds"]["rtl_sv"] == {"files": 1, "lines": 5, "code": 2}
    assert blk["kinds"]["dv_py"]["files"] == 1
    assert stats["ips"]["blk/sub"]["kinds"]["rtl_sv"]["code"] == 1
    assert stats["kinds"]["tools_py"] == {"files": 1, "lines": 3, "code": 2}
    assert stats["by_ext"][".bin"]["lines"] == 0  # binary
    assert stats["by_dir"]["."]["files"] == 2  # README.md, logo.bin
    assert stats["by_dir"]["src"]["files"] == 5


def test_stats_deterministic(repo, cfg):
    a = TreeReader(repo, cfg).stats("HEAD")[0]
    b = TreeReader(repo, cfg).stats("HEAD")[0]
    assert a == b


def test_series_backfill_incremental_and_ci_refresh(repo, cfg):
    reader = TreeReader(repo, cfg)
    shas = git(repo, "rev-list", "--reverse", "HEAD").split()
    lines, added, refreshed = commits.update(reader, "HEAD", [], None)
    assert (added, refreshed) == (2, 0)
    assert [x["sha"] for x in lines] == shas
    assert [x["index"] for x in lines] == [0, 1]
    assert lines[0]["date"] == "2026-01-01T00:00:00Z"
    assert lines[0]["code"]["total"]["files"] == 4
    assert all(x["ci"] == {"status": "unavailable"} for x in lines)

    text = commits.dumps(lines)
    path = repo.parent / "commits.jsonl"
    path.write_text(text)
    new = commit(repo, {"tools/x/more.py": "a = 1\n"}, "third", "2026-01-03T00:00:00")
    ci = {shas[0]: {"conclusion": "success", "duration_s": 60.0}}
    lines2, added, refreshed = commits.update(reader, "HEAD", commits.load(path), ci)
    assert added == 1 and refreshed == 2
    assert lines2[0]["ci"] == {"status": "ok", "conclusion": "success", "duration_s": 60.0}
    assert lines2[1]["ci"] == {"status": "none"}
    assert lines2[2]["sha"] == new and lines2[2]["index"] == 2
    # Existing lines are kept byte for byte apart from the ci refresh.
    assert lines2[1]["code"] == lines[1]["code"]
    # Idempotent.
    again, added, refreshed = commits.update(reader, "HEAD", [dict(x) for x in lines2], ci)
    assert (added, refreshed) == (0, 0) and commits.dumps(again) == commits.dumps(lines2)


def test_series_limit(repo, cfg):
    lines, added, _ = commits.update(TreeReader(repo, cfg), "HEAD", [], None, limit=1)
    assert added == 1 and len(lines) == 1


def test_series_bad_line(tmp_path):
    import pytest
    from metrics.config import MetricsError
    p = tmp_path / "c.jsonl"
    p.write_text('{"sha": "a"}\nnot json\n')
    with pytest.raises(MetricsError, match="c.jsonl:2"):
        commits.load(p)


def test_series_marks_data_only_commits(repo, cfg):
    reader = TreeReader(repo, cfg)
    bot = cfg.data_author
    data = commit(repo, {"wiki/tracker/data/2026-01-03.json": "{}\n"}, "data", "2026-01-03T00:00:00",
                  email=bot)
    mixed = commit(repo, {"wiki/tracker/data/x.json": "{}\n", "tools/x/y.py": "y = 1\n"}, "mixed",
                   "2026-01-04T00:00:00", email=bot)
    human = commit(repo, {"wiki/tracker/data/z.json": "{}\n"}, "human data", "2026-01-05T00:00:00")
    ci = {data: {"conclusion": "success", "duration_s": 1.0}}
    lines, _, _ = commits.update(reader, "HEAD", [], ci)
    by = {x["sha"]: x for x in lines}
    assert by[data]["data_only"] is True and by[data]["ci"] == {"status": "n/a"}
    assert by[mixed]["data_only"] is False and by[mixed]["ci"] == {"status": "none"}
    assert by[human]["data_only"] is False
    # n/a is never refreshed.
    again, _, refreshed = commits.update(reader, "HEAD", lines, ci)
    assert refreshed == 0 and {x["sha"]: x for x in again}[data]["ci"] == {"status": "n/a"}
