"""PDF-pagina's lezen (ook met OCR), tabellen herkennen, gegevens wegpoetsen en de
geanonimiseerde tekst als Markdown opbouwen."""

from __future__ import annotations

import contextlib
import io
import re
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf as fitz  # PyMuPDF

from patronen import *  # noqa: F401,F403


# ---------------------------------------------------------------------------
# PDF lezen, redigeren en schrijven
# ---------------------------------------------------------------------------

@dataclass
class Tabel:
    bbox: fitz.Rect
    rijen: list[list[fitz.Rect | None]]          # per rij de cellen (None = samengevoegde cel)


@dataclass
class PaginaTekst:
    tekst: str
    vakken: list[tuple[fitz.Rect, int] | None]   # per teken: (rechthoek, regel-id)
    tabellen: list[Tabel] = field(default_factory=list)
    ocr: bool = False                            # hele pagina gelezen met tekstherkenning (scan)
    ocr_regels: set[int] = field(default_factory=set)   # regels uit OCR van een afbeelding


def lees_pagina(page: fitz.Page, textpage=None) -> PaginaTekst:
    tekens: list[str] = []
    vakken: list[tuple[fitz.Rect, int] | None] = []
    vlaggen = fitz.TEXT_PRESERVE_WHITESPACE | fitz.TEXT_MEDIABOX_CLIP
    raw = page.get_text("rawdict", flags=vlaggen, sort=True, textpage=textpage)
    regel_id = 0
    ocr_regels: set[int] = set()
    for blok in raw["blocks"]:
        if blok.get("type") != 0:
            continue
        for regel in blok["lines"]:
            for span in regel["spans"]:
                if span.get("font", "").startswith("GlyphLess"):   # tekst uit OCR
                    ocr_regels.add(regel_id)
                for ch in span["chars"]:
                    tekens.append(ch["c"])
                    vakken.append((fitz.Rect(ch["bbox"]), regel_id))
            tekens.append("\n")
            vakken.append(None)
            regel_id += 1
        tekens.append("\n")
        vakken.append(None)
    return PaginaTekst("".join(tekens), vakken, ocr_regels=ocr_regels)


def ocr_talen() -> str | None:
    """Talen voor tekstherkenning ("nld+eng"), of None als Tesseract niet geïnstalleerd is."""
    try:
        map_ = fitz.get_tessdata()
    except Exception:
        return None
    if not map_:
        return None
    talen = [t for t in ("nld", "eng") if (Path(map_) / f"{t}.traineddata").is_file()]
    return "+".join(talen) or None


def lijkt_scan(page: fitz.Page, tekst: str) -> bool:
    """Een pagina die (vrijwel) alleen uit een afbeelding bestaat."""
    if len(tekst.strip()) >= 50:
        return False
    oppervlak = sum(abs(fitz.Rect(i["bbox"]) & page.rect) for i in page.get_image_info())
    return oppervlak >= 0.5 * abs(page.rect) or (not tekst.strip() and oppervlak > 0)


def grote_afbeeldingen(page: fitz.Page) -> list[tuple[fitz.Rect, float]]:
    """Afbeeldingen waarin tekst kan staan (bijv. een ingevoegde scan van een paspoort),
    met hun aandeel van de pagina. Kleine logo's en iconen tellen niet mee."""
    uit = []
    for info in page.get_image_info():
        r = fitz.Rect(info["bbox"]) & page.rect
        if r.width >= 60 and r.height >= 30 and abs(r) >= 0.03 * abs(page.rect):
            uit.append((r, abs(r) / abs(page.rect)))
    return uit


def zoek_tabellen(page: fitz.Page) -> list[Tabel]:
    """Tabellen met lijnen (zoals op loonstroken en jaaropgaven)."""
    try:
        with contextlib.redirect_stdout(io.StringIO()):   # PyMuPDF print soms een tip
            gevonden = page.find_tables()
    except Exception:
        return []
    return [Tabel(fitz.Rect(t.bbox), [[fitz.Rect(c) if c else None for c in rij.cells] for rij in t.rows])
            for t in gevonden.tables if len(t.rows) >= 2]


def _midden(r: fitz.Rect) -> fitz.Point:
    return fitz.Point((r.x0 + r.x1) / 2, (r.y0 + r.y1) / 2)


def cel_tekens(pt: PaginaTekst, cel: fitz.Rect) -> list[int]:
    return [i for i, vak in enumerate(pt.vakken) if vak and cel.contains(_midden(vak[0]))]


def tabel_vondsten(pt: PaginaTekst) -> list[Vondst]:
    """Datums in een tabelkolom met kop "Geboren"/"Geboortedatum" zijn geboortedata,
    ook al staat het woord "geboren" niet vlak ervoor."""
    uit = []
    for tabel in pt.tabellen:
        kop = tabel.rijen[0]
        for k, kopcel in enumerate(kop):
            koptekst = "".join(pt.tekst[i] for i in cel_tekens(pt, kopcel)) if kopcel else ""
            m = GEBOORTE_RE.search(koptekst)
            if not m:
                continue
            soort = "overlijdensdatum" if OVERLIJDEN_RE.match(m[0]) else "geboortedatum"
            for rij in tabel.rijen[1:]:
                if k >= len(rij) or rij[k] is None:
                    continue
                idx = cel_tekens(pt, rij[k])
                tekst = "".join(pt.tekst[i] for i in idx)
                for start, eind, jaar, _ in datum_kandidaten(tekst):
                    uit.append(Vondst(idx[start], idx[eind - 1] + 1, soort, jaar, 6))
    return uit


NAAM_KOP_RE = re.compile(r"(?i)^\s*(naam|volledige naam|achternaam|geslachtsnaam|geboortenaam|voornamen|"
                         r"voornaam|roepnaam|naam [a-zé]+)\s*$")


def tabel_namen(pt: PaginaTekst) -> list[tuple[str, int, str | None]]:
    """Cellen in een kolom met kop "Naam", "Achternaam", "Voornamen", ... als (naam, positie, hint)."""
    uit = []
    for tabel in pt.tabellen:
        for k, kopcel in enumerate(tabel.rijen[0]):
            koptekst = "".join(pt.tekst[i] for i in cel_tekens(pt, kopcel)) if kopcel else ""
            if not (m := NAAM_KOP_RE.match(koptekst)):
                continue
            soort = m[1].lower()
            hint = ("achternaam" if soort in ("achternaam", "geslachtsnaam", "geboortenaam")
                    else "voornaam" if soort in ("voornamen", "voornaam", "roepnaam") else None)
            for rij in tabel.rijen[1:]:
                if k < len(rij) and rij[k] is not None and (idx := cel_tekens(pt, rij[k])):
                    uit.append(("".join(pt.tekst[i] for i in idx), idx[0], hint))
    return uit


def pagina_markdown(pt: PaginaTekst, vondsten: list[Vondst]) -> str:
    """De geanonimiseerde tekst van een pagina, met tabellen als Markdown-tabel.

    Zonder tabellen is dit precies pas_toe(pt.tekst, vondsten).
    """
    vondst_van: list[int | None] = [None] * len(pt.tekst)
    for k, v in enumerate(vondsten):
        for i in range(v.start, v.eind):
            vondst_van[i] = k
    getoond: set[int] = set()

    def teken(i: int) -> str:
        k = vondst_van[i]
        if k is None:
            return pt.tekst[i]
        if k in getoond:
            return ""
        getoond.add(k)
        return vondsten[k].vervanging

    if not pt.tabellen:
        return "".join(teken(i) for i in range(len(pt.tekst)))

    gebruikt: set[int] = set()

    def cel(rect: fitz.Rect | None) -> str:
        if rect is None:
            return ""
        delen, vorige = [], None
        for i in cel_tekens(pt, rect):
            gebruikt.add(i)
            if vorige is not None and pt.vakken[i][1] != vorige:
                delen.append(" ")
            vorige = pt.vakken[i][1]
            delen.append(teken(i))
        return re.sub(r"\s+", " ", "".join(delen)).strip().replace("|", "\\|")

    def tabel_md(t: int) -> str:
        tabel = pt.tabellen[t]
        breedte = max(len(r) for r in tabel.rijen)
        rijen = [[cel(c) for c in r] + [""] * (breedte - len(r)) for r in tabel.rijen]
        regels = ["| " + " | ".join(rijen[0]) + " |", "|" + "---|" * breedte]
        regels += ["| " + " | ".join(r) + " |" for r in rijen[1:]]
        # Tekst binnen het tabelvak maar buiten de cellen (bijv. een voetnoot) niet kwijtraken.
        rest, vorige = [], None
        for i, vak in enumerate(pt.vakken):
            if vak and regel_tabel.get(vak[1]) == t and i not in gebruikt:
                if vorige is not None and vak[1] != vorige:
                    rest.append("\n")
                vorige = vak[1]
                rest.append(teken(i))
        overig = re.sub(r"[ \t]+", " ", "".join(rest)).strip()
        return "\n".join(regels) + (f"\n\n{overig}" if overig else "")

    # Welke tekstregels vallen in een tabel?
    regel_vak: dict[int, fitz.Rect] = {}
    for vak in pt.vakken:
        if vak:
            regel_vak[vak[1]] = regel_vak[vak[1]] | vak[0] if vak[1] in regel_vak else fitz.Rect(vak[0])
    regel_tabel = {rid: t for rid, r in regel_vak.items()
                   for t, tabel in enumerate(pt.tabellen) if tabel.bbox.contains(_midden(r))}

    uit, gehad, in_tabel = [], set(), False
    for i, ch in enumerate(pt.tekst):
        vak = pt.vakken[i]
        if vak is None:
            if not in_tabel:
                uit.append(ch)
            continue
        t = regel_tabel.get(vak[1])
        in_tabel = t is not None
        if in_tabel:
            if t not in gehad:
                gehad.add(t)
                uit.append("\n" + tabel_md(t) + "\n\n")
            continue
        uit.append(teken(i))
    return "".join(uit)


def _lettergrootte(rect: fitz.Rect, tekst: str) -> float:
    breedte = fitz.get_text_length(tekst, fontname="helv", fontsize=1) or 1
    return max(5.0, min(rect.height * 0.75, rect.width / breedte))


def redigeer_pagina(page: fitz.Page, pt: PaginaTekst, vondsten: list[Vondst]) -> None:
    labels: list[tuple[fitz.Rect, str]] = []
    for v in vondsten:
        if v.soort == "behouden":
            continue
        per_regel: dict[int, fitz.Rect] = {}
        for vak in pt.vakken[v.start:v.eind]:
            if vak is None:
                continue
            rect, rid = vak
            if rid in per_regel:
                per_regel[rid] |= rect
            else:
                per_regel[rid] = fitz.Rect(rect)
        for i, rect in enumerate(per_regel.values()):
            if pt.ocr or rid in pt.ocr_regels:   # OCR-vakken zijn iets krapper dan de inkt
                marge = rect.height * 0.25
                rect = fitz.Rect(rect.x0 - 1.5, rect.y0 - marge, rect.x1 + 1.5, rect.y1 + marge)
            page.add_redact_annot(rect, fill=(1, 1, 1), cross_out=False)
            if i == 0:
                labels.append((rect, v.vervanging))
    page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_PIXELS)
    # Het label pas na de redactie plaatsen: een redactie-annotatie laat tekst
    # weg die niet in het vak past, en een korte naam heeft een smal vak.
    for rect, tekst in labels:
        grootte = _lettergrootte(rect, tekst)
        if grootte < rect.height * 0.6:   # past niet goed: korte vorm, "P1" i.p.v. "Persoon 1"
            tekst = re.sub(r"^Persoon (\d+)$", r"P\1", re.sub(r"^Familie ", "Fam. ", tekst))
            grootte = _lettergrootte(rect, tekst)
        page.insert_text((rect.x0, rect.y1 - rect.height * 0.2), tekst,
                         fontname="helv", fontsize=grootte, color=(0.25, 0.25, 0.25))


def verwijder_afbeeldingen(page: fitz.Page) -> int:
    """Haal foto's, handtekeningen en logo's weg; tekst eromheen blijft staan."""
    vakken = [fitz.Rect(i["bbox"]) for i in page.get_image_info()]
    vakken = [r for r in vakken if not r.is_empty]
    for r in vakken:
        page.add_redact_annot(r, fill=(0.92, 0.92, 0.92))
    if vakken:
        page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_REMOVE,
                              graphics=fitz.PDF_REDACT_LINE_ART_NONE, text=fitz.PDF_REDACT_TEXT_NONE)
        for r in vakken:
            page.insert_text((r.x0 + 2, r.y0 + 9), "[afbeelding verwijderd]", fontname="helv",
                             fontsize=7, color=(0.4, 0.4, 0.4))
    return len(vakken)


def schoon_document(doc: fitz.Document) -> None:
    """Verwijder wat buiten de zichtbare pagina persoonsgegevens kan bevatten: metadata,
    bladwijzers, bijlagen, paginaminiaturen (een plaatje van de originele pagina) en de
    toegankelijkheidsstructuur (kan alternatieve teksten met de originele inhoud hebben)."""
    doc.set_metadata({})
    doc.del_xml_metadata()
    doc.set_toc([])
    for naam in list(doc.embfile_names()):
        doc.embfile_del(naam)
    for page in doc:
        for sleutel in ("Thumb", "PieceInfo", "Metadata"):
            if doc.xref_get_key(page.xref, sleutel)[0] != "null":
                doc.xref_set_key(page.xref, sleutel, "null")
    catalogus = doc.pdf_catalog()
    for sleutel in ("StructTreeRoot", "MarkInfo", "PieceInfo", "SpiderInfo"):
        if doc.xref_get_key(catalogus, sleutel)[0] != "null":
            doc.xref_set_key(catalogus, sleutel, "null")
