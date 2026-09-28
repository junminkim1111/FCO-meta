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


def test_table_learns_only_safe_signatures(tmp_path):
    wide = GK + BACK4 + [9, 11, 12, 16, 18, 25]  # LM/RM + CAM: 라인 기준으로는 4-2-2-1-1
    other = GK + BACK4 + [9, 11, 13, 15, 24, 26]
    table = FormationTable()
    assert table.infer(wide) == "4-2-2-1-1"

    odd = GK + [4, 6] + [12, 14, 16, 21, 23, 24, 26, 27]  # 라인 기준 "2-3-1-4": 실제 포메이션 이름이 아님
    counts = table.learn(
        [(wide, "4-2-3-1")] * 9 + [(wide, "4-2-2-1-1")]  # 라인 기준이 실제 이름(4-2-2-1-1)인데 다른 관측 → 학습 안 함
        + [(other, "4-2-2-2")] * 2 + [(other, "4-2-2-2(2)")] * 6  # 변형 이름 75% → min_share 미달
        + [(odd, "4-2-3-1")] * 2  # min_count 미달
    )
    assert table.known == {}
    assert table.infer(wide) == "4-2-2-1-1"
    assert sum(counts[signature(wide)].values()) == 10

    counts = table.learn([(other, "4-2-2-2(2)")] * 4 + [(odd, "3-4-3")] * 3)
    assert table.infer(other) == "4-2-2-2(2)"  # 같은 라인 모양의 변형 이름은 학습
    assert table.infer(odd) == "3-4-3"  # 이름 없는 라인 모양은 학습

    path = tmp_path / "formations.json"
    table.save(path)
    assert FormationTable.load(path).known == table.known
    assert FormationTable.load(tmp_path / "missing.json").known == {}
