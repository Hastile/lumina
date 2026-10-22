from lumina.sourcedb import catalog, db


def test_model_written_names_are_split_and_scraps_dropped():
    assert catalog.clean_names(["フリーレン (Frieren)", "A / Bee", "렌", "Fern"]) == ["フリーレン", "Frieren", "Bee", "Fern"]


def test_latin_named_entity_takes_the_first_korean_name(tmp_path):
    con = db.connect(tmp_path / "s.db")
    eid = db.entity(con, "character", "gotoh_hitori", aliases=["Gotoh Hitori"], catalog_ref="danbooru:gotoh_hitori")
    again = catalog.resolve_entity(con, "character", ["고토 히토리", "後藤ひとり", "Gotoh Hitori"])
    assert again == eid
    row = con.execute("SELECT name, aliases FROM entity WHERE entity_id = ?", (eid,)).fetchone()
    assert row["name"] == "고토 히토리" and "gotoh_hitori" in row["aliases"].split("\n")


def test_a_folder_name_never_invents_a_work(tmp_path):
    con = db.connect(tmp_path / "s.db")
    assert catalog.find_entity(con, "work", ["수익창출 항소"]) is None
    known = db.entity(con, "work", "퓨키 가디스 샷 트릭", aliases=["PUKEY GODDESS SHOT TRICK"])
    assert catalog.find_entity(con, "work", ["PUKEY GODDESS SHOT TRICK"]) == known
    assert con.execute("SELECT count(*) FROM entity").fetchone()[0] == 1


def test_episode_from_the_channels_clip_names():
    from lumina.sourcedb.ingest import episode_of
    assert episode_of("S2 E04 0931 저 구름 고기만두") == ("S2 E04", 571)
    assert episode_of("S1 E3 3253 공부할 시간이야 앉아") == ("S1 E03", 1973)
    assert episode_of("E01 0005 청산싫어") == ("E01", 5)
    assert episode_of("EP1 001256 재의 왕을 쓰러뜨리는 자가") == ("E01", 776)
    assert episode_of("리제로 4기 11화 고점") == ("S4 E11", None)
    assert episode_of("『チェンソーマン』第４話ノンクレジットエンディング") == ("E04", None)
    assert episode_of("Escape the Walls 원작 (pandorioltd)") is None
    assert episode_of("화려한 무대") is None


def test_more_episode_formats():
    from lumina.sourcedb.ingest import episode_of
    assert episode_of("귀멸의 칼날 3기 대장장이 마을 편 BD 😜 11 - Anime - Linkkf 애니 TV (자막 - 더빙)") == ("S3 E11", None)
    assert episode_of("귀멸의 칼날_ 유곽 편 (2기) BD 😜 1 - Anime - Linkkf 애니 TV (자") == ("S2 E01", None)
    assert episode_of("드래곤볼 카이(改) 😜 123 - Anime - Linkkf 애니 TV (자막 - 더빙)") == ("E123", None)
    assert episode_of("JoJo's Bizarre Adventure - Opening 4 v3 [4K 60FPS") == ("OP4", None)
    assert episode_of("[Neobrane] JoJo's Bizarre Adventure - Ending 3 [4K") == ("ED3", None)
    assert episode_of("demon_slayer_op_5") == ("OP5", None)
    assert episode_of("1기 영역전개") == ("S1", None)
    assert episode_of("4부 손가락") == ("Part 4", None)
    assert episode_of("(스포) 스타레일 3.3 컷신 모음 - YouTube - Chrome 2025-07-03") == ("v3.3", None)
    assert episode_of("akaza_1") is None
    assert episode_of("vs 아카이누 2") is None
    assert episode_of("2026-07-28 23-54-08") is None
    assert episode_of("4115174-uhd_4096_2160_25fps") is None
    assert episode_of("부산 여행") is None


def test_hashes_and_films_are_not_episodes():
    from lumina.sourcedb.ingest import episode_of
    assert episode_of("0ef71181ed7db912a1acd8676c741a4d") is None
    assert episode_of("극장판 애니 _ 체인소 맨 레제편 😜 01 - Anime - Linkkf 애니 TV") == ("Film", None)


def test_long_shots_split_at_their_strongest_soft_change():
    from fractions import Fraction as F
    from lumina.sourcedb.ingest import split_long
    scores = [(F(i, 10), 0.01) for i in range(0, 300)]
    scores[90] = (F(9), 0.2)      # inside the 0-22 s "shot"
    scores[150] = (F(15), 0.25)
    scores[50] = (F(5), 0.11)     # below the soft threshold
    edges = split_long([F(22)], scores, F(30))
    assert edges == [F(9), F(15), F(22)]
    assert split_long([F(5)], scores, F(10)) == [F(5)]  # nothing long
