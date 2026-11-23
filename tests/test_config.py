import pytest
import yaml

from pipeline import config


def _edit_map(root, fn):
    p = root / "config" / "map.yaml"
    m = yaml.safe_load(p.read_text())
    fn(m)
    p.write_text(yaml.safe_dump(m))


def test_loads_repo_config(repo):
    cfg = config.load(repo)
    assert cfg.crs == "EPSG:2955"
    assert "UTM zone 11N" in config.crs_name(cfg.crs)
    assert "CSRS" in config.crs_name(cfg.crs)
    xmin, ymin, xmax, ymax = cfg.aoi
    assert xmax - xmin == pytest.approx(28000)
    assert ymax - ymin == pytest.approx(21000)
    # Fernie is around 640 km E, 5484 km N in UTM 11N.
    assert 600_000 < (xmin + xmax) / 2 < 700_000
    assert 5_450_000 < (ymin + ymax) / 2 < 5_520_000


def test_buffer_applied(repo):
    cfg = config.load(repo)
    assert cfg.aoi_buffered[0] == cfg.aoi[0] - 1000
    assert cfg.aoi_buffered[3] == cfg.aoi[3] + 1000


def test_sides_split_aoi(repo):
    cfg = config.load(repo)
    a, b = cfg.sides
    assert a.extent[0] == cfg.aoi[0] and b.extent[2] == cfg.aoi[2]
    assert a.extent[2] == b.extent[0]


def test_explicit_side_extent(repo):
    _edit_map(repo, lambda m: m["sheet"]["sides"][0].update(extent=[1, 2, 3, 4]))
    assert config.load(repo).sides[0].extent == (1, 2, 3, 4)


def test_bad_side_extent(repo):
    _edit_map(repo, lambda m: m["sheet"]["sides"][0].update(extent="middle"))
    with pytest.raises(config.ConfigError):
        config.load(repo)


def test_scale_change_needs_no_code(repo):
    _edit_map(repo, lambda m: m.update(scale=30000))
    assert config.load(repo).scale == 30000


def test_bbox_in_other_crs_encloses(repo):
    cfg = config.load(repo)
    w, s, e, n = cfg.bbox_in("EPSG:4326", buffered=False)
    assert w < -115.06 < e and s < 49.50 < n
    alb = cfg.bbox_in("EPSG:3005")
    assert alb[2] - alb[0] > 28000


def test_rejects_trailforks(repo):
    p = repo / "config" / "sources.yaml"
    s = yaml.safe_load(p.read_text())
    s["sources"]["trailforks"] = {"title": "x", "kind": "wfs", "licence": "odbl"}
    p.write_text(yaml.safe_dump(s))
    with pytest.raises(config.ConfigError, match="Trailforks"):
        config.load(repo)


def test_undefined_licence(repo):
    p = repo / "config" / "sources.yaml"
    s = yaml.safe_load(p.read_text())
    s["sources"]["fwa_lakes"]["licence"] = "nope"
    p.write_text(yaml.safe_dump(s))
    with pytest.raises(config.ConfigError, match="licence"):
        config.load(repo)


def test_terrain_sources_must_exist(repo):
    _edit_map(repo, lambda m: m["terrain"].update(sources=["lidarbc_dem:2022", "nope"]))
    with pytest.raises(config.ConfigError, match="nope"):
        config.load(repo)
