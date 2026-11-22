from pipeline import config
from pipeline.attribution import attribution_lines


def test_one_line_per_licence(repo):
    cfg = config.load(repo)
    lines = attribution_lines(cfg, ["fwa_streams", "fwa_lakes", "osm", "cdem"])
    assert len(lines) == 3
    assert lines[0].startswith("Map data © OpenStreetMap contributors")
    assert "ODbL" in lines[0] or "Open Database License" in lines[0]
    assert "Open Government Licence - British Columbia" in lines[1]
    assert "Stream Network" in lines[1] and "Lakes" in lines[1]
    assert "Open Government Licence - Canada" in lines[2]


def test_only_used_sources(repo):
    cfg = config.load(repo)
    lines = attribution_lines(cfg, ["fwa_streams"])
    assert len(lines) == 1 and "OpenStreetMap" not in lines[0]
