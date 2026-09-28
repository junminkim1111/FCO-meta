from fco_meta.crawler import RankQuery


def test_unfiltered_query_matches_initial_page_load():
    assert RankQuery().to_params(3) == {"rt": "1vs1", "n4seasonno": "0", "n4pageno": "3"}
    assert not RankQuery(mode="manager").is_filtered


def test_filtered_query_sends_full_form():
    params = RankQuery(team_color_id=1004, formation="4-2-3-1").to_params(1)

    assert params["tc_01"] == "1004"
    assert params["formation_01"] == "4-2-3-1"
    assert params["formation_02"] == "-"
    assert (params["tier_s"], params["tier_e"]) == ("3100", "800")
    assert (params["rank_s"], params["rank_e"]) == ("1", "10000")
    assert params["cv_e"] == "18000000000000000000"
