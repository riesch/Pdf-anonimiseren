"""Tests voor gescande pagina's (OCR) en tabellen, met verzonnen gegevens."""

import sys
from pathlib import Path

import pymupdf
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import anonimiseer as an  # noqa: E402

ocr_nodig = pytest.mark.skipif(an.ocr_talen() is None, reason="Tesseract is niet geïnstalleerd")


def _tabel_pdf(pad, rijen):
    doc = pymupdf.open()
    pagina = doc.new_page()
    pagina.insert_text((50, 70), "Overzicht gezinsleden", fontsize=10)
    x0, y0, b, h = 50, 100, 110, 20
    for r in range(len(rijen) + 1):
        pagina.draw_line((x0, y0 + r * h), (x0 + b * len(rijen[0]), y0 + r * h))
    for c in range(len(rijen[0]) + 1):
        pagina.draw_line((x0 + c * b, y0), (x0 + c * b, y0 + h * len(rijen)))
    for r, rij in enumerate(rijen):
        for c, waarde in enumerate(rij):
            pagina.insert_text((x0 + c * b + 3, y0 + r * h + 14), waarde, fontsize=9)
    doc.save(pad)


def test_tabel_wordt_markdown_tabel(tmp_path):
    bron = tmp_path / "overzicht.pdf"
    _tabel_pdf(bron, [["Naam", "Geboortedatum", "BSN"],
                      ["Richard Jansen", "10-04-1980", "111222333"],
                      ["Anna de Vries", "03-05-1982", "123456782"]])
    r = an.Anonimiseerder(gebruik_ner=False).verwerk(bron, tmp_path / "uit")
    tekst = r.tekst.read_text(encoding="utf-8")
    assert ("| Naam | Geboortedatum | BSN |\n|---|---|---|\n"
            "| Richard | 1980 | 111222333 |\n| Anna | 1982 | 123456782 |") in tekst   # BSN alleen via familie
    assert r.tabellen == 1 and r.datums_blijven == 0 and not r.gemarkeerd


def test_zonder_tabel_gelijk_aan_pas_toe():
    pt = an.PaginaTekst("Jan woont hier.\n", [(pymupdf.Rect(i, 0, i + 1, 1), 0) for i in range(15)] + [None])
    vondsten = [an.Vondst(0, 3, "naam", "Persoon 1")]
    assert an.pagina_markdown(pt, vondsten) == an.pas_toe(pt.tekst, vondsten)


def _scan_pdf(pad, tekst):
    bron = pymupdf.open()
    bron.new_page().insert_text((50, 80), tekst, fontsize=12)
    pix = bron[0].get_pixmap(dpi=200, colorspace=pymupdf.csGRAY)
    scan = pymupdf.open()
    pagina = scan.new_page(width=bron[0].rect.width, height=bron[0].rect.height)
    pagina.insert_image(pagina.rect, pixmap=pix)
    scan.save(pad)


@ocr_nodig
def test_scan_wordt_met_ocr_geanonimiseerd(tmp_path):
    bron = tmp_path / "scan.pdf"
    _scan_pdf(bron, "Naam: Richard Pieter Jansen\nBSN: 111222333\nIBAN: NL91 ABNA 0417 1643 00")
    r = an.Anonimiseerder(gebruik_ner=False).verwerk(bron, tmp_path / "uit")
    assert r.ocr == [1] and not r.scans and not r.gemarkeerd
    tekst = r.tekst.read_text(encoding="utf-8")
    assert "Naam: Richard" in tekst and "Jansen" not in tekst and "ABNA4300" in tekst
    # Ook in de afbeelding zelf is het weg: opnieuw lezen met OCR vindt het niet meer.
    with pymupdf.open(r.pdf) as d:
        pagina = d[0]
        tp = pagina.get_textpage_ocr(language=an.ocr_talen(), dpi=300, full=True)
        gelezen = pagina.get_text(textpage=tp)
    assert "Jansen" not in gelezen and "0417" not in gelezen
    assert "OCR" in r.rapport.read_text(encoding="utf-8")


def test_scan_zonder_ocr_is_lek(tmp_path):
    bron = tmp_path / "scan.pdf"
    _scan_pdf(bron, "Naam: Richard Jansen")
    r = an.Anonimiseerder(gebruik_ner=False, ocr=False).verwerk(bron, tmp_path / "uit")
    assert r.scans == [1] and r.gemarkeerd and "_CONTROLEREN" in r.pdf.name


def _brief_met_scan(pad):
    """Digitale brief met een ingevoegde scan (zoals een kopie van een identiteitsbewijs)."""
    bron = pymupdf.open()
    bron.new_page().insert_text((50, 80), "Naam: Richard Jansen\nBSN: 111222333", fontsize=14)
    pix = bron[0].get_pixmap(dpi=150, clip=pymupdf.Rect(40, 60, 300, 120))
    doc = pymupdf.open()
    pagina = doc.new_page()
    pagina.insert_text((50, 60), "Hierbij de gevraagde kopie van het identiteitsbewijs.", fontsize=10)
    pagina.insert_image(pymupdf.Rect(50, 100, 350, 175), pixmap=pix)
    doc.save(pad)


@ocr_nodig
def test_ingevoegde_scan_wordt_gelezen(tmp_path):
    bron = tmp_path / "brief.pdf"
    _brief_met_scan(bron)
    r = an.Anonimiseerder(gebruik_ner=False).verwerk(bron, tmp_path / "uit")
    tekst = r.tekst.read_text(encoding="utf-8")
    assert "Hierbij de gevraagde kopie" in tekst and "Naam: Richard" in tekst
    assert "Jansen" not in tekst and r.ocr == [1] and not r.gemarkeerd


def test_ingevoegde_scan_zonder_ocr_is_lek(tmp_path):
    bron = tmp_path / "brief.pdf"
    _brief_met_scan(bron)
    r = an.Anonimiseerder(gebruik_ner=False, ocr=False).verwerk(bron, tmp_path / "uit")
    assert r.gemarkeerd and any("afbeelding" in b.soort and b.ernst == "lek" for b in r.bevindingen)


def test_ingevoegde_scan_met_zonder_afbeeldingen_geen_lek(tmp_path):
    bron = tmp_path / "brief.pdf"
    _brief_met_scan(bron)
    r = an.Anonimiseerder(gebruik_ner=False, ocr=False, zonder_afbeeldingen=True).verwerk(bron, tmp_path / "uit")
    assert not r.gemarkeerd


def test_tekst_in_tabelvak_buiten_cellen_blijft_bewaard():
    # Eén regel "A B noot" in een tabelvak; alleen "A" en "B" vallen in een cel.
    tekst = "A B noot\n"
    vakken = [(pymupdf.Rect(10 * i, 0, 10 * i + 8, 10), 0) for i in range(len(tekst) - 1)] + [None]
    tabel = an.Tabel(pymupdf.Rect(0, 0, 100, 10),
                     [[pymupdf.Rect(0, 0, 9, 10), pymupdf.Rect(19, 0, 29, 10)],
                      [None, None]])
    pt = an.PaginaTekst(tekst, vakken, tabellen=[tabel])
    md = an.pagina_markdown(pt, [])
    assert "| A | B |" in md and "noot" in md


def test_verborgen_laag_wordt_gelezen_en_weggepoetst(tmp_path):
    bron = tmp_path / "lagen.pdf"
    doc = pymupdf.open()
    pagina = doc.new_page()
    laag = doc.add_ocg("Verborgen laag", on=False)
    pagina.insert_text((40, 60), "Naam: Jan Jansen", fontsize=11)
    pagina.insert_text((40, 90), "Rekening NL91ABNA0417164300", fontsize=11, oc=laag)
    doc.save(bron)
    r = an.Anonimiseerder(gebruik_ner=False).verwerk(bron, tmp_path / "uit")
    with pymupdf.open(r.pdf) as d:
        ruw = b"".join(d.xref_stream(x) or b"" for x in d[0].get_contents())
    assert b"0417164300" not in ruw and "ABNA4300" in r.tekst.read_text(encoding="utf-8")
    assert r.tellingen.get("verborgen laag (aangezet)") == 1


def test_tekst_buiten_pagina_verwijderd(tmp_path):
    bron = tmp_path / "buiten.pdf"
    doc = pymupdf.open()
    pagina = doc.new_page()
    pagina.insert_text((40, 60), "Naam: Jan Jansen", fontsize=11)
    pagina.insert_text((700, 900), "Karel Klaassen", fontsize=11)   # buiten het paginavlak
    doc.save(bron)
    r = an.Anonimiseerder(gebruik_ner=False).verwerk(bron, tmp_path / "uit")
    with pymupdf.open(r.pdf) as d:
        assert "Klaassen" not in d[0].get_text(clip=pymupdf.INFINITE_RECT())
    assert r.tellingen.get("tekst buiten pagina (verwijderd)") == 1


def _doorzoekbare_scan(pad, tekst):
    bron = pymupdf.open()
    bron.new_page().insert_text((50, 80), tekst, fontsize=12)
    pix = bron[0].get_pixmap(dpi=200, colorspace=pymupdf.csGRAY)
    doc = pymupdf.open()
    pagina = doc.new_page(width=bron[0].rect.width, height=bron[0].rect.height)
    pagina.insert_image(pagina.rect, pixmap=pix)
    pagina.insert_text((50, 80), tekst, fontsize=12, render_mode=3)   # onzichtbare tekstlaag
    doc.save(pad)


@ocr_nodig
def test_doorzoekbare_scan_niet_dubbel(tmp_path):
    bron = tmp_path / "scan.pdf"
    _doorzoekbare_scan(bron, "Naam: Richard Jansen\nWoonplaats: Utrecht")
    r = an.Anonimiseerder(gebruik_ner=False).verwerk(bron, tmp_path / "uit")
    tekst = r.tekst.read_text(encoding="utf-8")
    assert tekst.count("Naam: Richard") == 1 and "Jansen" not in tekst and not r.gemarkeerd
    with pymupdf.open(r.pdf) as d:   # ook de afbeelding zelf is schoongepoetst
        pagina = d[0]
        tp = pagina.get_textpage_ocr(language=an.ocr_talen(), dpi=300, full=True)
        assert "Jansen" not in pagina.get_text(textpage=tp)


def test_ocr_fout_markeert_pagina_in_plaats_van_crash(tmp_path, monkeypatch):
    bron = tmp_path / "scan.pdf"
    _scan_pdf(bron, "Naam: Richard Jansen")
    a = an.Anonimiseerder(gebruik_ner=False)
    a.ocr_taal = "nld"

    def kapot(*args, **kwargs):
        raise RuntimeError("tesseract kapot")
    monkeypatch.setattr(pymupdf.Page, "get_textpage_ocr", kapot)
    r = a.verwerk(bron, tmp_path / "uit")
    assert r.scans == [1] and r.gemarkeerd
