from pipeline.cli import main


def test_config_command(repo, capsys):
    assert main(["--root", str(repo), "config"]) == 0
    out = capsys.readouterr().out
    assert "EPSG:2955" in out and "side A" in out and "fits" in out


def test_pending_stage_fails_clearly(repo):
    assert main(["--root", str(repo), "vectors"]) == 3


def test_fetch_unknown_source(repo):
    import pytest
    with pytest.raises(SystemExit):
        main(["--root", str(repo), "fetch", "--only", "nope"])
