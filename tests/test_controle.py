"""Tests voor de lekcheck en het controlerapport, met verzonnen gegevens."""

import sys
from pathlib import Path

import pymupdf

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import anonimiseer as an  # noqa: E402
import controle  # noqa: E402


def _personen(*namen):
    vermeldingen = [an.ontleed_naam(n, (-1, i)) for i, n in enumerate(namen)]
    personen, _ = an.koppel_personen(vermeldingen, alleen_opgegeven=True)
    return personen


def _soorten(bevindingen, ernst):
    return [b.soort for b in bevindingen if b.ernst == ernst]


def test_lek_achternaam_en_andere_voornaam():
    personen = _personen("Richard Pieter Jansen")
    bev = controle.controleer_uitvoer(
        [("pagina 1", "Naam: Richard\nmap fam. jansen, Pieter belt\nzie www.richardjansen.nl")], personen)
    assert _soorten(bev, "lek") == ["achternaam van familielid", "voornaam van familielid"]
    assert _soorten(bev, "verdacht") == ["achternaam van familielid in een langer woord"]
    assert bev[0].plek == "pagina 1, regel 2" and "»jansen«" in bev[0].fragment


def test_label_is_geen_lek():
    personen = _personen("Richard Jansen")
    assert controle.controleer_uitvoer([("", "Naam: Richard, [BSN Richard], [adres Richard]")], personen) == []


def test_gewoon_woord_alleen_verdacht():
    personen = _personen("Anna de Groot")
    bev = controle.controleer_uitvoer([("", "Een groot huis. Brief aan De Groot.")], personen)
    assert _soorten(bev, "verdacht") == ["achternaam van familielid (of het gewone woord 'groot')"]
    assert _soorten(bev, "lek") == ["achternaam van familielid"]


def test_verdachte_plekken():
    bev = controle.controleer_uitvoer(
        [("", "Dhr. Bakker belde. Ref 123456789, rek NL12ABCD0123456789, op 01-02-2024.")], [])
    assert _soorten(bev, "verdacht") == ["naam na titel", "nummer van 9 cijfers (BSN?)",
                                         "lijkt op rekeningnummer", "volledige datum (geboortedatum?)"]


def _pdf(pad, tekst):
    doc = pymupdf.open()
    doc.new_page().insert_text((40, 60), tekst, fontsize=10)
    doc.save(pad)


def test_pdf_met_lek_wordt_gemarkeerd(tmp_path):
    bron = tmp_path / "brief.pdf"
    _pdf(bron, "Naam: Richard Jansen\nmap fam. jansen")
    a = an.Anonimiseerder(namen=["Richard Jansen"], gebruik_ner=False, alleen_familie=True)
    r = a.verwerk(bron, tmp_path / "uit")
    assert r.gemarkeerd and r.pdf.name == "brief_anoniem_CONTROLEREN.pdf"
    assert r.tekst.name == "brief_anoniem_CONTROLEREN.md" and r.tekst.exists()
    rapport = r.rapport.read_text(encoding="utf-8")
    assert r.rapport.parent.name == "controle" and "## Lekken (1)" in rapport and "»jansen«" in rapport


def test_pdf_zonder_lek_niet_gemarkeerd(tmp_path):
    bron = tmp_path / "brief.pdf"
    _pdf(bron, "Naam: Richard Jansen\nGeboren 10-04-1980")
    r = an.Anonimiseerder(namen=["Richard Jansen"], gebruik_ner=False).verwerk(bron, tmp_path / "uit")
    assert not r.gemarkeerd and r.pdf.name == "brief_anoniem.pdf"
    assert "Geen lekken gevonden." in r.rapport.read_text(encoding="utf-8")


def test_geen_rapport(tmp_path):
    bron = tmp_path / "brief.pdf"
    _pdf(bron, "Naam: Richard Jansen")
    r = an.Anonimiseerder(gebruik_ner=False, rapport=False).verwerk(bron, tmp_path / "uit")
    assert r.rapport is None and not (tmp_path / "uit" / "controle").exists()


def test_bestandsnaam_wordt_gecontroleerd(tmp_path):
    bron = tmp_path / "jansen_2023.pdf"
    _pdf(bron, "Naam: Richard Jansen")
    r = an.Anonimiseerder(namen=["Richard Jansen"], gebruik_ner=False).verwerk(bron, tmp_path / "uit")
    assert r.gemarkeerd and r.tekst.name == "jansen_2023_anoniem_CONTROLEREN.md"
    lek = [b for b in r.bevindingen if b.ernst == "lek"][0]
    assert lek.plek == "bestandsnaam" and lek.fragment == "»jansen«_2023"


def test_los_paspoortnummer_is_verdacht():
    bev = controle.controleer_uitvoer([("", "losse code NW1234567 hier")], [])
    assert _soorten(bev, "verdacht") == ["lijkt op paspoort-/ID-kaartnummer"]
