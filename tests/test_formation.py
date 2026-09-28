from fco_meta.pipeline.formation import FormationTable, line_shape, parse_signature_key, signature, signature_key

GK = [0]
BACK4 = [3, 4, 6, 7]  # RB RCB LCB LB
SUBS = [28] * 7


def test_line_shape_counts_players_per_line():
    assert line_shape(GK + BACK4 + [9, 11, 17, 18, 19, 25] + SUBS) == "4-2-3-1"
    assert line_shape(GK + BACK4 + [13, 14, 15, 23, 25, 27]) == "4-3-3"
    assert line_shape(GK + BACK4 + [12, 13, 15, 16, 24, 26]) == "4-4-2"
    assert line_shape(GK + BACK4 + [10, 13, 15, 18, 24, 26]) == "4-1-2-1-2"
    assert line_shape(GK + [2, 4, 5, 6, 8] + [13, 15, 18, 24, 26]) == "5-2-1-2"


def test_line_shape_needs_ten_outfield_starters():
    assert line_shape(GK + BACK4 + [9, 11]) is None


def test_signature_ignores_goalkeeper_subs_and_order():
    a = GK + BACK4 + [9, 11, 17, 18, 19, 25] + SUBS
    b = list(reversed(a))
    assert signature(a) == signature(b) == (3, 4, 6, 7, 9, 11, 17, 18, 19, 25)
    assert signature_key(signature(a)) == "RB,RCB,LCB,LB,RDM,LDM,RAM,CAM,LAM,ST"
    assert parse_signature_key(signature_key(signature(a))) == signature(a)


def test_table_learns_unambiguous_signatures(tmp_path):
    wide = GK + BACK4 + [9, 11, 12, 16, 18, 25]  # LM/RM + CAM: 라인 기준으로는 4-2-2-1-1
    other = GK + BACK4 + [9, 11, 13, 15, 24, 26]
    table = FormationTable()
    assert table.infer(wide) == "4-2-2-1-1"

    counts = table.learn([(wide, "4-2-3-1")] * 9 + [(wide, "4-2-2-1-1")] + [(other, "4-2-2-2")] * 2 + [(other, "4-2-2-2(2)")] * 2)
    assert table.infer(wide) == "4-2-3-1"  # 90% 일치 → 표에 등록
    assert table.infer(other) == "4-2-2-2"  # 50:50 → 등록하지 않고 라인 기준 추론
    assert signature(other) not in table.known
    assert sum(counts[signature(wide)].values()) == 10

    path = tmp_path / "formations.json"
    table.save(path)
    assert FormationTable.load(path).known == table.known
    assert FormationTable.load(tmp_path / "missing.json").known == {}
