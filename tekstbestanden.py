"""Word-documenten (.docx) en e-mails (.eml) lezen als tekst, zodat ze net als PDF's
geanonimiseerd kunnen worden. De uitvoer is een Markdown-bestand (geen nieuw .docx/.eml).

  * .docx: de hoofdtekst (tabellen als Markdown-tabel), en apart de kop- en voetteksten,
    voetnoten, eindnoten en opmerkingen, want ook daarin kunnen namen staan.
  * .eml:  afzender, ontvangers, onderwerp, datum en de tekst van het bericht. Bijlagen
    worden alleen bij naam genoemd; sla ze op en verwerk ze apart.
"""

from __future__ import annotations

import email
import email.policy
import html
import re
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

TEKST_EXTENSIES = {".docx", ".eml"}
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def _alinea(p: ET.Element) -> str:
    delen = []
    for el in p.iter():
        if el.tag == f"{W}t" and el.text:
            delen.append(el.text)
        elif el.tag == f"{W}tab":
            delen.append("\t")
        elif el.tag in (f"{W}br", f"{W}cr"):
            delen.append("\n")
    return "".join(delen)


def _tabel(tbl: ET.Element) -> str:
    rijen = []
    for tr in tbl.iter(f"{W}tr"):
        rij = [" ".join(_alinea(p) for p in tc.iter(f"{W}p")).strip().replace("|", "\\|")
               for tc in tr.findall(f"{W}tc")]
        rijen.append(rij)
    if not rijen:
        return ""
    breedte = max(len(r) for r in rijen)
    rijen = [r + [""] * (breedte - len(r)) for r in rijen]
    regels = ["| " + " | ".join(rijen[0]) + " |", "|" + "---|" * breedte]
    return "\n".join(regels + ["| " + " | ".join(r) + " |" for r in rijen[1:]])


def _blokken(lichaam: ET.Element) -> str:
    uit = []
    for el in lichaam:
        if el.tag == f"{W}p":
            uit.append(_alinea(el))
        elif el.tag == f"{W}tbl":
            uit.append("\n" + _tabel(el) + "\n")
        elif el.tag == f"{W}sdt":   # inhoudsbesturingselementen (bijv. formuliervelden)
            inhoud = el.find(f"{W}sdtContent")
            if inhoud is not None:
                uit.append(_blokken(inhoud))
    return "\n".join(uit)


def lees_docx(pad: Path) -> list[tuple[str, str]]:
    """Geeft [(kopje, tekst)]: de hoofdtekst en eventueel kop-/voetteksten, noten, opmerkingen."""
    uit = []
    with zipfile.ZipFile(pad) as z:
        namen = z.namelist()
        hoofd = ET.fromstring(z.read("word/document.xml")).find(f"{W}body")
        uit.append(("Tekst", _blokken(hoofd) if hoofd is not None else ""))
        for patroon, kopje in ((r"word/header\d*\.xml", "Koptekst"), (r"word/footer\d*\.xml", "Voettekst"),
                               (r"word/footnotes\.xml", "Voetnoten"), (r"word/endnotes\.xml", "Eindnoten"),
                               (r"word/comments\.xml", "Opmerkingen")):
            for naam in sorted(n for n in namen if re.fullmatch(patroon, n)):
                wortel = ET.fromstring(z.read(naam))
                tekst = "\n".join(_alinea(p) for p in wortel.iter(f"{W}p")).strip()
                if tekst:
                    uit.append((kopje, tekst))
    return uit


def _html_naar_tekst(h: str) -> str:
    h = re.sub(r"(?is)<(script|style)\b.*?</\1>", "", h)
    h = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</tr>|</li>|</h\d>", "\n", h)
    h = re.sub(r"(?s)<[^>]+>", "", h)
    return re.sub(r"\n{3,}", "\n\n", html.unescape(h)).strip()


def lees_eml(pad: Path) -> list[tuple[str, str]]:
    bericht = email.message_from_bytes(pad.read_bytes(), policy=email.policy.default)
    kop = [f"{label}: {bericht[veld]}" for veld, label in
           (("From", "Van"), ("To", "Aan"), ("Cc", "Cc"), ("Reply-To", "Antwoord aan"),
            ("Subject", "Onderwerp"), ("Date", "Datum")) if bericht[veld]]
    tekst = ""
    lichaam = bericht.get_body(preferencelist=("plain", "html"))
    if lichaam is not None:
        tekst = lichaam.get_content()
        if lichaam.get_content_type() == "text/html":
            tekst = _html_naar_tekst(tekst)
    bijlagen = [b.get_filename() or "(zonder naam)" for b in bericht.iter_attachments()]
    uit = [("Kop", "\n".join(kop)), ("Bericht", tekst.strip())]
    if bijlagen:
        uit.append(("Bijlagen (niet geanonimiseerd – sla ze op en verwerk ze apart)",
                    "\n".join(f"- {b}" for b in bijlagen)))
    return uit


def lees_tekstbestand(pad: Path) -> list[tuple[str, str]]:
    return lees_docx(pad) if pad.suffix.lower() == ".docx" else lees_eml(pad)
