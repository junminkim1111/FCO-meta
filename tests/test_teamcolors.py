from fco_meta.crawler import TeamColor, TeamColorCatalog

ENTRIES = [
    TeamColor(1004, "아스널", "club", 13),
    TeamColor(1318, "잉글랜드", "club", 78),
    TeamColor(2006, "잉글랜드", "nationality", 2),
    TeamColor(1016, "FC 바르셀로나", "club", 53),
]


def test_resolve_by_name_ignores_spacing_and_case():
    catalog = TeamColorCatalog(ENTRIES)
    assert catalog.resolve("fc바르셀로나").id == 1016
    assert catalog.resolve(" 아스널 ").id == 1004
    assert catalog.resolve("없는팀") is None


def test_resolve_prefers_club_on_name_clash():
    catalog = TeamColorCatalog(ENTRIES)
    assert catalog.resolve("잉글랜드").category == "club"
    assert [tc.category for tc in catalog.find("잉글랜드")] == ["club", "nationality"]


def test_resolve_by_id():
    catalog = TeamColorCatalog(ENTRIES)
    assert catalog.resolve(1004).name == "아스널"
    assert catalog.resolve("2006").name == "잉글랜드"


def test_save_and_load_roundtrip(tmp_path):
    path = tmp_path / "tc.json"
    TeamColorCatalog(ENTRIES).save(path)
    assert sorted(TeamColorCatalog.load(path).entries, key=lambda t: t.id) == sorted(ENTRIES, key=lambda t: t.id)


def test_bundled_catalog_loads():
    assert TeamColorCatalog.load().resolve("아스널").id == 1004
