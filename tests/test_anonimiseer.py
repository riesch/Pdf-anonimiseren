"""Tests met uitsluitend verzonnen gegevens."""

import sys
from pathlib import Path

import pymupdf
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import anonimiseer as an  # noqa: E402


@pytest.fixture(scope="module")
def anon():
    return an.Anonimiseerder(gebruik_ner=False)


def test_elfproef():
    assert an.elfproef("111222333")
    assert not an.elfproef("111222334")


def test_iban():
    assert an.iban_geldig("NL91 ABNA 0417 1643 00")
    assert not an.iban_geldig("NL91 ABNA 0417 1643 01")


def test_bsn_en_iban(anon):
    uit = anon.anonimiseer_tekst("BSN: 111.222.333, rekening NL91ABNA0417164300 en NL91 ABNA 0417 1643 00.")
    assert "111" not in uit
    assert uit.count("**************4300") == 2


def test_iban_zelfde_laatste_cijfers_blijven_verschillend(anon):
    uit = anon.anonimiseer_tekst("NL91ABNA0417164300, NL39RABO0000004300 en weer NL91ABNA0417164300")
    assert uit == "**************4300, **************4300 (2) en weer **************4300"


def test_geboortedatum_wordt_jaar(anon):
    uit = anon.anonimiseer_tekst("Geboortedatum: 12-03-1985\nDatum brief: 14-02-2024")
    assert "Geboortedatum: 1985" in uit
    assert "14-02-2024" in uit  # gewone datum blijft staan
    uit = an.Anonimiseerder(gebruik_ner=False, alle_datums=True).anonimiseer_tekst("Datum: 14-02-2024")
    assert uit == "Datum: 2024"


@pytest.mark.parametrize("tekst", [
    "Kerkstraat 12a", "Burgemeester de Withstraat 5", "Laan van Meerdervoort 120",
    "1234 AB Utrecht", "2511 CV Den Haag",
])
def test_adressen(anon, tekst):
    assert anon.anonimiseer_tekst(f"Woont op {tekst}.") == "Woont op [adres]."


def test_namen_consistent(anon):
    tekst = ("Naam: Jan Jansen\nGeachte heer Jansen,\nUw partner mevrouw A. de Vries "
             "en J. Jansen. Jan belt.")
    uit = anon.anonimiseer_tekst(tekst)
    assert "Jansen" not in uit and "Vries" not in uit
    assert uit == ("Naam: Jan\nGeachte heer Jan,\nUw partner mevrouw Persoon 1 "
                   "en Jan. Jan belt.")   # van "A. de Vries" is geen voornaam bekend


def test_zelfde_achternaam_wordt_familie(anon):
    uit = anon.anonimiseer_tekst("Naam: Jan Jansen\nNaam kind: Piet Jansen\nDhr. Jansen")
    assert uit == "Naam: Jan\nNaam kind: Piet\nDhr. Familie A"


def test_zelfde_voornaam_krijgt_volgnummer(anon):
    uit = anon.anonimiseer_tekst("Naam: Jan Jansen\nNaam partner: Jan de Vries")
    assert uit == "Naam: Jan\nNaam partner: Jan (2)"


def test_roepnaam_uit_namenlijst():
    a = an.Anonimiseerder(namen=["Johannes Petrus Jansen = Hans"], gebruik_ner=False)
    assert a.anonimiseer_tekst("Johannes Petrus JANSEN en Hans Jansen") == "Hans en Hans"


def test_notariele_akte():
    a = an.Anonimiseerder(gebruik_ner=False)
    uit = a.anonimiseer_tekst(
        "de heer Johannes Petrus Maria JANSEN, geboren te Amersfoort op vijf april "
        "negentienhonderdvijfentachtig, wonende te Utrecht, zich legitimerende met paspoort "
        "nummer NW1234567. Het woonhuis, kadastraal bekend gemeente Utrecht, sectie A, nummer 1234. "
        "De heer Jansen tekent.")
    assert uit == (
        "de heer Johannes, geboren te [geboorteplaats] op 1985, wonende te [woonplaats], zich "
        "legitimerende met paspoort nummer [documentnummer]. Het woonhuis, kadastraal bekend "
        "[kadastrale aanduiding]. De heer Johannes tekent.")


def test_datum_in_woorden():
    assert an.jaar_in_woorden("negentienhonderdvijfentachtig") == 1985
    assert an.jaar_in_woorden("tweeduizend") == 2000
    assert an.woord_naar_getal("drieëntwintig") == 23


def test_behoud_en_extra_namen():
    a = an.Anonimiseerder(namen=["Karin Visser"], behoud={"Bakker"}, gebruik_ner=False)
    uit = a.anonimiseer_tekst("Groet, Karin Visser. Mevr. Bakker belde.")
    assert "Visser" not in uit and "Bakker" in uit


def test_pdf(tmp_path, anon):
    bron = tmp_path / "Jan Jansen brief.pdf"
    doc = pymupdf.open()
    doc.new_page().insert_text((40, 60), "Naam: Jan Jansen\nBSN: 111222333\nAdres: Kerkstraat 1", fontsize=11)
    doc.set_metadata({"author": "Jan Jansen"})
    doc.save(bron)

    r = anon.verwerk(bron, tmp_path / "uit")
    assert r.pdf.name == "Jan brief_anoniem.pdf"
    assert not r.lekken
    with pymupdf.open(r.pdf) as d:
        tekst = d[0].get_text()
        assert "Jansen" not in tekst and "111222333" not in tekst and "Kerkstraat" not in tekst
        assert not d.metadata["author"]
    assert "Naam: Jan" in r.tekst.read_text(encoding="utf-8")
