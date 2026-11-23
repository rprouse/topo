from pipeline.cli import main


def test_config_command(repo, capsys):
    assert main(["--root", str(repo), "config"]) == 0
    out = capsys.readouterr().out
    assert "EPSG:2955" in out and "side A" in out and "fits" in out


def test_qa_on_empty_build(repo):
    assert main(["--root", str(repo), "qa"]) == 0
    report = (repo / "build" / "qa-report.md").read_text(encoding="utf-8")
    assert "nothing to compare yet" in report and "Still the placeholder" in report


def test_fetch_unknown_source(repo):
    import pytest
    with pytest.raises(SystemExit):
        main(["--root", str(repo), "fetch", "--only", "nope"])
