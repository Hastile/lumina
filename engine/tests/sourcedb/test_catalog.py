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
