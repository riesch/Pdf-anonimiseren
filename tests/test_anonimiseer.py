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
    uit = anon.anonimiseer_tekst("BSN: 111.222.333, rekening NL91ABNA0417164300 en NL91ABNA0417164300.")
    assert "111" not in uit
    assert uit.count("[IBAN 1]") == 2


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
    assert "Jansen" not in uit and "Vries" not in uit and "Jan " not in uit
    assert uit.count("Persoon 1") == 4
    assert "Persoon 2" in uit


def test_zelfde_achternaam_wordt_familie(anon):
    uit = anon.anonimiseer_tekst("Naam: Jan Jansen\nNaam kind: Piet Jansen\nDhr. Jansen")
    assert "Persoon 1" in uit and "Persoon 2" in uit and "Familie A" in uit


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
    assert r.pdf.name == "Persoon 1 brief_anoniem.pdf"
    assert not r.lekken
    with pymupdf.open(r.pdf) as d:
        tekst = d[0].get_text()
        assert "Jansen" not in tekst and "111222333" not in tekst and "Kerkstraat" not in tekst
        assert not d.metadata["author"]
    assert "Persoon 1" in r.tekst.read_text(encoding="utf-8")
