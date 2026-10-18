"""Controle van de uitvoer: lekken opsporen en een controlerapport schrijven.

Na het anonimiseren wordt de *uitvoer* (niet het origineel) nagekeken:

  * lekken    – gegevens die er zeker niet meer in mogen staan: achternamen en andere
                voornamen van mensen uit de familie-/namenlijst, hun BSN, adres of
                geboortedatum, of gegevens die het script zelf vond maar die in de PDF
                bleven staan. Een bestand met een lek krijgt "_CONTROLEREN" in de naam.
  * verdacht  – dingen die een persoonsgegeven kúnnen zijn: namen achter een titel,
                nummers van 9 cijfers, rekeningnummer-achtige reeksen, datums.

Het rapport komt in <uitvoermap>/controle/ en bevat alleen tekst uit de uitvoer, nooit
de originele gegevens.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import anonimiseer as an

MARKERING = "_CONTROLEREN"
LABEL_RE = re.compile(r"\[[^\]\n]{1,40}\]|\*{4,}\w{4}(?: \(\d+\))?")
TITEL_RE = re.compile(
    rf"(?i:\b(?:dhr|mevr|mw|de heer|heer|mevrouw|meneer)\.?)[ ]+((?:[{an.HOOFD}]\.[ ]?)*"
    rf"(?:{an.TV_I}[ ])*[{an.HOOFD}][{an.KLEIN}{an.HOOFD}'-]+)"
)
NEGEN_CIJFERS_RE = re.compile(r"(?<![\d.,\w-])(\d{4}\.\d{2}\.\d{3}|\d{3}[ .]\d{3}[ .]\d{3}|\d{9})(?![\d\w]|[.,]\d)")
# Achternamen die ook een gewoon woord zijn: in kleine letters is dat meestal het woord.
GEWONE_WOORDEN = {
    "bakker", "visser", "smit", "boer", "mulder", "dekker", "vos", "kok", "koster", "groot",
    "brouwer", "jong", "meer", "berg", "bos", "dijk", "kramer", "schouten", "hoek", "veen",
    "koning", "wit", "molenaar", "haan", "beek", "bosch", "graaf", "lange", "klein", "vries",
    "smid", "wolf", "vogel", "visscher", "schipper", "timmerman", "kuiper", "verkerk", "post",
    "zwart", "blom", "haas", "kort", "lang", "hout", "laan", "brink", "kamp", "veld", "vink",
    "poort", "huis", "hof", "berger", "kaal", "schaap", "stam", "rijk", "goede", "groen",
}
IBAN_ACHTIG_RE = re.compile(r"(?<![A-Za-z0-9])[A-Z]{2}\d{2}[ ]?[A-Z]{4}(?:[ ]?\d){6,}")


@dataclass
class Bevinding:
    ernst: str        # "lek" of "verdacht"
    soort: str
    plek: str
    fragment: str


def _fragment(tekst: str, start: int, eind: int, breedte: int = 50) -> str:
    begin = max(tekst.rfind("\n", 0, start) + 1, start - breedte)
    einde = tekst.find("\n", eind)
    einde = min(einde if einde >= 0 else len(tekst), eind + breedte)
    return (("…" if begin > 0 and tekst[begin - 1] != "\n" else "") + tekst[begin:start]
            + "»" + tekst[start:eind] + "«" + tekst[eind:einde]).strip()


def _plek(deel: str, tekst: str, pos: int) -> str:
    regel = f"regel {tekst.count(chr(10), 0, pos) + 1}"
    return f"{deel}, {regel}" if deel else regel


def _in_label(tekst: str, start: int, eind: int) -> bool:
    return any(m.start() <= start and eind <= m.end() for m in LABEL_RE.finditer(tekst, max(0, start - 45), eind + 45))


def verboden_woorden(personen: list[an.Persoon], behoud: set[str]) -> list[tuple[re.Pattern, re.Pattern, str]]:
    """Woorden van opgegeven personen die na het anonimiseren nergens meer mogen staan.

    Per woord twee patronen, strenger dan bij het anonimiseren zelf: als los woord in
    elke hoofd-/kleine letter ("jansen") is het een lek; als deel van een langer woord
    ("richardjansen.nl", "Jansenstraat") is het verdacht.
    """
    labels = {(p.label or "").lower() for p in personen}
    woorden: dict[str, str] = {}
    for p in personen:
        if not p.opgegeven or not p.label:
            continue
        for w in (p.achternaam or "").split():
            if not an._tv(w):
                woorden.setdefault(w, "achternaam van familielid")
        for w in p.alle_voornamen:
            if w.lower() != p.label.lower():
                woorden.setdefault(w, "voornaam van familielid")
    return [(re.compile(rf"(?i){an.NW}{re.escape(w)}{an.NA}"),
             re.compile(rf"(?i){re.escape(w)}") if len(w) >= 5 else None, soort)
            for w, soort in woorden.items()
            if len(w) >= 3 and w.lower() not in labels and an._norm(w) not in behoud]


def controleer_uitvoer(delen: list[tuple[str, str]], personen: list[an.Persoon], familie=None,
                       behoud: set[str] | None = None, nlp=None, namen_verdacht: bool = True,
                       datums_verdacht: bool = True) -> list[Bevinding]:
    """Kijk de uitvoer na. delen = [(plek, tekst)], bijv. [("pagina 1", "...")]."""
    uit: list[Bevinding] = []
    verboden = verboden_woorden(personen, behoud or set())
    labels = {p.label for p in personen if p.label}
    for deel, tekst in delen:
        def voeg_toe(ernst, soort, start, eind):
            uit.append(Bevinding(ernst, soort, _plek(deel, tekst, start), _fragment(tekst, start, eind)))

        # --- lekken
        gemeld: set[int] = set()
        for los, deel_van, soort in verboden:
            for m in los.finditer(tekst):
                if not _in_label(tekst, m.start(), m.end()):
                    if m[0].islower() and m[0] in GEWONE_WOORDEN:
                        voeg_toe("verdacht", f"{soort} (of het gewone woord '{m[0]}')", m.start(), m.end())
                    else:
                        voeg_toe("lek", soort, m.start(), m.end())
                    gemeld.add(m.start())
            for m in (deel_van.finditer(tekst) if deel_van else ()):
                if m.start() not in gemeld and not _in_label(tekst, m.start(), m.end()):
                    voeg_toe("verdacht", f"{soort} in een langer woord", m.start(), m.end())
        if familie is not None:
            for v in familie.vind(tekst):
                soort = {"bsn": "BSN", "adres": "adres", "geboortedatum": "geboortedatum"}[v.soort]
                voeg_toe("lek", f"{soort} van familielid", v.start, v.eind)

        # --- verdacht
        gezien: set[tuple[int, int]] = set()

        def verdacht(soort, start, eind):
            if (start, eind) not in gezien and not _in_label(tekst, start, eind):
                gezien.add((start, eind))
                voeg_toe("verdacht", soort, start, eind)

        if namen_verdacht:
            for m in TITEL_RE.finditer(tekst):
                if m[1] not in labels and not m[1].startswith("Persoon") and not m[1].startswith("Familie"):
                    verdacht("naam na titel", m.start(1), m.end(1))
            if nlp is not None:
                for ent in nlp(tekst).ents:
                    woorden = ent.text.split()
                    if ent.label_ in ("PERSON", "PER") and len(woorden) >= 2 \
                            and not any(w.strip("[]") in labels for w in woorden):
                        verdacht("mogelijke naam", ent.start_char, ent.end_char)
        for m in NEGEN_CIJFERS_RE.finditer(tekst):
            verdacht("nummer van 9 cijfers (BSN?)", m.start(1), m.end(1))
        for m in IBAN_ACHTIG_RE.finditer(tekst):
            verdacht("lijkt op rekeningnummer", m.start(), m.end())
        if datums_verdacht:
            for start, eind, _, _ in an.datum_kandidaten(tekst):
                verdacht("volledige datum (geboortedatum?)", start, eind)
    return uit


def markeer(pad: Path | None) -> Path | None:
    """Zet _CONTROLEREN in de bestandsnaam, zodat het niet per ongeluk gedeeld wordt."""
    if pad is None or MARKERING in pad.stem:
        return pad
    nieuw = pad.with_name(f"{pad.stem}{MARKERING}{pad.suffix}")
    pad.replace(nieuw)
    return nieuw


def schrijf_rapport(pad: Path, bron: str, r: an.Resultaat, bevindingen: list[Bevinding]) -> None:
    lekken = [b for b in bevindingen if b.ernst == "lek"]
    verdacht = [b for b in bevindingen if b.ernst == "verdacht"]
    regels = [f"# Controlerapport: {bron}", ""]
    regels.append("Dit rapport toont alleen tekst uit de geanonimiseerde uitvoer. Deel het niet met een AI.")
    regels.append("")
    regels.append("## Uitvoer")
    for uit in (r.pdf, r.tekst):
        if uit:
            regels.append(f"- {uit.name}")
    regels += ["", "## Vervangen", ""]
    regels += [f"- {s}: {n}x" for s, n in sorted(r.tellingen.items())] or ["- niets"]
    regels += ["", f"## Lekken ({len(lekken)})", ""]
    if lekken:
        regels.append("Deze gegevens horen niet meer in de uitvoer te staan. Het bestand heeft daarom "
                      f"`{MARKERING}` in de naam. Pas het bestand aan (of de familie-/namenlijst) en "
                      "draai het script opnieuw.")
        regels.append("")
        regels += [f"- **{b.soort}** – {b.plek}" + (f": `{b.fragment}`" if b.fragment else "") for b in lekken]
    else:
        regels.append("Geen lekken gevonden.")
    regels += ["", f"## Verdacht – even nakijken ({len(verdacht)})", ""]
    if verdacht:
        regels.append("Dit kán een persoonsgegeven zijn, maar hoeft niet. Kijk of het weg moet.")
        regels.append("")
        per_soort: dict[str, list[Bevinding]] = {}
        for b in verdacht:
            per_soort.setdefault(b.soort, []).append(b)
        for soort, lijst in per_soort.items():
            regels.append(f"### {soort} ({len(lijst)})")
            regels += [f"- {b.plek}: `{b.fragment}`" for b in lijst[:50]]
            if len(lijst) > 50:
                regels.append(f"- … en nog {len(lijst) - 50}")
            regels.append("")
    else:
        regels.append("Niets verdachts gevonden.")
    if r.scans:
        regels += ["", "## Gescande pagina's", "",
                   f"Pagina('s) {', '.join(map(str, r.scans))} bevatten geen tekst (scan) en zijn niet "
                   "geanonimiseerd."]
    pad.parent.mkdir(parents=True, exist_ok=True)
    pad.write_text("\n".join(regels) + "\n", encoding="utf-8")


def afronden(r: an.Resultaat, bron: Path, uitmap: Path, bevindingen: list[Bevinding],
             rapport: bool = True) -> an.Resultaat:
    """Markeer bij lekken, schrijf het rapport en geef het bijgewerkte resultaat terug."""
    # Eerdere controles (bijv. gegevens die in de PDF bleven staan) tellen ook als lek.
    bevindingen = [Bevinding("lek", f"{soort} stond nog in de PDF",
                             f"pagina {pagina}" if pagina else "", "") for pagina, soort in r.lekken] + bevindingen
    if r.scans and r.pdf:
        bevindingen.append(Bevinding("lek", "gescande pagina niet geanonimiseerd",
                                     f"pagina('s) {', '.join(map(str, r.scans))}", ""))
    r.bevindingen = bevindingen
    if any(b.ernst == "lek" for b in bevindingen):
        r.pdf, r.tekst = markeer(r.pdf), markeer(r.tekst)
        r.gemarkeerd = True
    if rapport:
        stam = (r.tekst or r.pdf).stem.replace(MARKERING, "").removesuffix("_anoniem")
        r.rapport = uitmap / "controle" / f"{stam}_controle.md"
        schrijf_rapport(r.rapport, bron.name, r, bevindingen)
    return r
