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
