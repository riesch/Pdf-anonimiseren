#!/usr/bin/env python3
"""Anonimiseer PDF-bestanden zodat ze geschikt zijn om aan een AI-model te geven.

Wat er gebeurt:
  * namen          -> vaste pseudoniemen ("Persoon 1", "Persoon 2", ...), zodat
                      een AI nog kan volgen wie wie is;
  * BSN            -> "[BSN]";
  * adressen       -> "[adres]" (straat + huisnummer, postcode + woonplaats);
  * geboortedata   -> alleen het geboortejaar;
  * rekeningnummers-> "[IBAN 1]", "[IBAN 2]", ... (vast per rekeningnummer);
  * e-mail/telefoon-> "[e-mail]" / "[telefoon]".

Per invoerbestand worden een geanonimiseerde PDF (echte redactie: de originele
tekst wordt uit het bestand verwijderd) en een Markdown-tekstbestand gemaakt.
Het tekstbestand is het handigst om aan een AI te voeren.

Gebruik:
    python anonimiseer.py brief.pdf
    python anonimiseer.py map_met_pdfs/ -o uitvoer/ --namen namen.txt
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import pymupdf as fitz  # PyMuPDF

# ---------------------------------------------------------------------------
# Bouwstenen voor reguliere expressies
# ---------------------------------------------------------------------------

HOOFD = "A-ZÀ-ÖØ-Þ"
KLEIN = "a-zß-öø-ÿ"
# Eén naamdeel: "Jan", "Anne-Marie", "Bakker-Smit", "JANSEN", "O'Brien"
CAP = rf"[{HOOFD}](?:[{KLEIN}'’]+|[{HOOFD}'’]+)(?:-[{HOOFD}][{KLEIN}{HOOFD}'’]+)*"
INITIALEN = rf"(?:[{HOOFD}]\.[ ]?){{1,5}}"
TUSSENVOEGSELS = [
    "van", "de", "der", "den", "het", "ten", "ter", "te", "'t", "von", "zu",
    "vd", "v.d.", "la", "le", "du", "da", "di", "del", "des", "dos", "el",
]
TV = "(?:" + "|".join(re.escape(t) for t in sorted(TUSSENVOEGSELS, key=len, reverse=True)) + ")"
TV_I = rf"(?i:{TV})"
NAAM = rf"(?:{INITIALEN}[ ]?|(?:{CAP}[ ]){{0,3}})(?:{TV_I}[ ]){{0,3}}{CAP}"
NAAM_OMGEKEERD = rf"(?:{TV_I}[ ]){{0,3}}{CAP},[ ]?(?:{INITIALEN}|{CAP}(?:[ ]{CAP}){{0,3}})(?:[ ]{TV}){{0,3}}"

TITELS = r"(?:de[ ]heer|heer|mevrouw|meneer|mevr|mw|mvr|dhr|hr|mr|dr|drs|ir|ing|prof|ds)\.?"
GROETEN = {
    "geachte", "beste", "hoi", "hallo", "lieve", "beste", "hoogachtend",
    "groet", "groeten", "vriendelijke", "met", "namens", "aan", "van",
}
TITEL_WOORDEN = {
    "heer", "mevrouw", "meneer", "mevr", "mw", "mvr", "dhr", "hr", "mr", "dr",
    "drs", "ir", "ing", "prof", "ds",
}
GEEN_NAAM = {
    # Woorden die NER soms als naam ziet, maar dat (bijna) nooit zijn.
    "bsn", "iban", "datum", "betreft", "onderwerp", "kenmerk", "pagina",
    "bijlage", "telefoon", "adres", "postcode", "woonplaats", "naam",
    "geboortedatum", "handtekening", "nederland", "gemeente",
}

NW = r"(?<![\w])"   # geen woordteken ervoor
NA = r"(?![\w])"    # geen woordteken erna


def _tv(token: str) -> bool:
    return token.lower() in TUSSENVOEGSELS


# ---------------------------------------------------------------------------
# Validatie
# ---------------------------------------------------------------------------

def elfproef(cijfers: str) -> bool:
    """Controleer een BSN met de elfproef (8 of 9 cijfers)."""
    cijfers = cijfers.zfill(9)
    if len(cijfers) != 9 or not cijfers.isdigit() or cijfers == "000000000":
        return False
    som = sum(int(c) * w for c, w in zip(cijfers[:8], range(9, 1, -1)))
    som -= int(cijfers[8])
    return som % 11 == 0


def iban_geldig(iban: str) -> bool:
    iban = iban.replace(" ", "").upper()
    if not re.fullmatch(r"[A-Z]{2}\d{2}[A-Z0-9]{10,30}", iban):
        return False
    omgezet = "".join(str(int(c, 36)) for c in iban[4:] + iban[:4])
    return int(omgezet) % 97 == 1


IBAN_LENGTE = {
    "NL": 18, "BE": 16, "DE": 22, "FR": 27, "GB": 22, "ES": 24, "IT": 27,
    "LU": 20, "AT": 20, "CH": 21, "DK": 18, "PL": 28, "PT": 25, "IE": 22,
    "SE": 24, "NO": 15, "FI": 18,
}


# ---------------------------------------------------------------------------
# Vondsten
# ---------------------------------------------------------------------------

@dataclass
class Vondst:
    start: int
    eind: int
    soort: str        # naam, bsn, iban, adres, geboortedatum, datum, email, telefoon
    vervanging: str
    prioriteit: int = 0

    @property
    def lengte(self) -> int:
        return self.eind - self.start


def kies_vondsten(vondsten: list[Vondst]) -> list[Vondst]:
    """Los overlappende vondsten op: langste (dan hoogste prioriteit) wint."""
    gekozen: list[Vondst] = []
    for v in sorted(vondsten, key=lambda v: (-v.lengte, -v.prioriteit, v.start)):
        if all(v.eind <= g.start or v.start >= g.eind for g in gekozen):
            gekozen.append(v)
    return sorted(gekozen, key=lambda v: v.start)


def pas_toe(tekst: str, vondsten: list[Vondst]) -> str:
    delen, pos = [], 0
    for v in vondsten:
        delen.append(tekst[pos:v.start])
        delen.append(v.vervanging)
        pos = v.eind
    delen.append(tekst[pos:])
    return "".join(delen)


# ---------------------------------------------------------------------------
# Detectie van BSN, IBAN, contactgegevens, data en adressen
# ---------------------------------------------------------------------------

BSN_LABEL_RE = re.compile(
    r"(?i)\b(?:bsn|burgerservicenummer|burger[ -]service[ -]nummer|sofi[- ]?nummer|sofinr|persoonsnummer)"
    r"\b[^\d\n]{0,25}?(\d[\d .]{6,12}\d)"
)
BSN_RE = re.compile(r"(?<![\d.,\w-])(\d{4}\.\d{2}\.\d{3}|\d{3}[ .]\d{3}[ .]\d{3}|\d{9})(?![\d\w]|[.,]\d)")

IBAN_RE = re.compile(r"(?<![A-Za-z0-9])([A-Z]{2}\d{2}(?:[ ]?[A-Z0-9]){10,30})")
REKENING_LABEL_RE = re.compile(
    r"(?i)\b(?:rekening(?:nummer)?|bankrekening(?:nummer)?|reknr|rek\.?[ ]?nr|girorekening|giro)"
    r"\b\.?[ \t]*[:.]?[ \t]*(\d{1,3}(?:[ .]?\d){5,9})(?!\d)"
)
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
TELEFOON_RE = re.compile(
    r"(?<![\w+])(?:(?:\+|00)31[ \t-]?(?:\(0\)[ \t-]?)?|0)[1-9](?:[ \t-]?\d){8}(?!\d)"
)

MAANDEN = {
    "januari": 1, "jan": 1, "februari": 2, "feb": 2, "febr": 2, "maart": 3,
    "mrt": 3, "mar": 3, "april": 4, "apr": 4, "mei": 5, "juni": 6, "jun": 6,
    "juli": 7, "jul": 7, "augustus": 8, "aug": 8, "september": 9, "sep": 9,
    "sept": 9, "oktober": 10, "okt": 10, "oct": 10, "november": 11, "nov": 11,
    "december": 12, "dec": 12,
}
_MAAND_ALT = "|".join(sorted(MAANDEN, key=len, reverse=True))
DATUM_NUM_RE = re.compile(r"(?<![\d.])(\d{1,2})[-/.](\d{1,2})[-/.](\d{4}|\d{2})(?![\d]|\.\d)")
DATUM_ISO_RE = re.compile(r"(?<!\d)(\d{4})-(\d{1,2})-(\d{1,2})(?!\d)")
DATUM_TXT_RE = re.compile(rf"(?i)(?<!\d)(\d{{1,2}})[ ]+({_MAAND_ALT})\.?[ ]+(\d{{4}})(?!\d)")
GEBOORTE_RE = re.compile(
    r"(?i)(geb(?:oren|oorte\w*)?\b|geb\.|date of birth|birth|d\.o\.b\.?|\bdob\b)"
)

STRAAT_SUFFIX = (
    "straat|straatweg|laan|weg|plein|gracht|kade|singel|dijk|dreef|hof|pad|steeg|baan|"
    "wal|markt|plantsoen|erf|ring|allee|boulevard|dam|haven|park|oord|rade|zijde|"
    "veld|akker|hoven|kamp|wijk|burcht|poort|brink|horst|donk|hoek|weide|polder|"
    "berg|bosch|dal|gaarde|tuin|hout|kwartier|vliet|wetering|vaart|stede|hoeve|"
    "kanaal|promenade|passage|galerij|steenweg|heuvel|lindeboom|zoom|meent"
)
HUISNUMMER = r"[ ]+\d{1,5}(?:[ ]?-?[ ]?(?:[A-Za-z]{1,3}|hs|bis)(?![\w]))?(?:[ ]?-[ ]?\d{1,4})?(?![\w])"
STRAAT_RE = re.compile(
    rf"{NW}(?:(?:[{HOOFD}][\w'.-]*|{TV_I})[ ]){{0,3}}[{HOOFD}][{KLEIN}'-]*(?:{STRAAT_SUFFIX}){HUISNUMMER}"
    rf"|{NW}(?:Laan|Weg|Plein|Kade|Dijk|Singel|Gracht|Straat|Dreef|Hof|Markt|Oude|Nieuwe)"
    rf"(?:[ ](?:van|der|de|den|het|ten|ter|op|aan|'t))*[ ][{HOOFD}][\w'-]*{HUISNUMMER}"
)
STAD_TWEEDELIG = r"(?:Den|De|Sint|St\.|Nieuw|Oud|Hoog|Laag|Groot|Klein|Bergen|Hendrik|Ter|Ten)"
POSTCODE_RE = re.compile(
    rf"{NW}[1-9]\d{{3}}[ ]?(?!SA|SD|SS)[A-Z]{{2}}{NA}"
    rf"(?:[ ,]{{1,3}}(?:'[sS]-|'[tT][ ])?(?:{STAD_TWEEDELIG}[ ])?[{HOOFD}][\w'-]+"
    rf"(?:[ ](?:aan|a/d|op|bij|in)[ ](?:den[ ]|de[ ])?[{HOOFD}][\w'-]+)?)?"
)
ADRES_LABEL_RE = re.compile(
    r"(?im)\b(?:woon)?(?:adres(?:gegevens)?|straat(?:naam)?|huisnummer|postcode|woonplaats|"
    r"geboorteplaats|verblijfplaats|vestigingsadres|correspondentieadres|postadres)"
    r"[ \t]*:[ \t]*([^\n]*?\S)(?=[ \t]{2,}|\t|$)"
)


def _jaar(jj: str) -> str:
    if len(jj) == 4:
        return jj
    eeuw = 2000 if int(jj) <= date.today().year % 100 else 1900
    return str(eeuw + int(jj))


def vind_data(tekst: str, alle_datums: bool) -> tuple[list[Vondst], int]:
    """Geef vondsten voor datums terug, plus het aantal datums dat bleef staan."""
    kandidaten = []
    for m in DATUM_NUM_RE.finditer(tekst):
        d, mnd, j = int(m[1]), int(m[2]), m[3]
        if 1 <= d <= 31 and 1 <= mnd <= 12:
            kandidaten.append((m.start(), m.end(), _jaar(j)))
    for m in DATUM_ISO_RE.finditer(tekst):
        if 1 <= int(m[2]) <= 12 and 1 <= int(m[3]) <= 31:
            kandidaten.append((m.start(), m.end(), m[1]))
    for m in DATUM_TXT_RE.finditer(tekst):
        if 1 <= int(m[1]) <= 31:
            kandidaten.append((m.start(), m.end(), m[3]))
    kandidaten.sort()

    vondsten, blijft, vorige_eind = [], 0, 0
    for start, eind, jaar in kandidaten:
        if start < vorige_eind:
            continue
        venster = tekst[max(vorige_eind, start - 60):start]
        if GEBOORTE_RE.search(venster):
            vondsten.append(Vondst(start, eind, "geboortedatum", jaar, 5))
        elif alle_datums:
            vondsten.append(Vondst(start, eind, "datum", jaar, 5))
        else:
            blijft += 1
        vorige_eind = eind
    return vondsten, blijft


class Register:
    """Houdt vaste labels bij voor terugkerende waarden (zoals IBAN's)."""

    def __init__(self):
        self.labels: dict[tuple[str, str], str] = {}
        self.teller: dict[str, int] = {}

    def label(self, soort: str, sleutel: str, voorvoegsel: str) -> str:
        k = (soort, sleutel)
        if k not in self.labels:
            self.teller[soort] = self.teller.get(soort, 0) + 1
            self.labels[k] = f"[{voorvoegsel} {self.teller[soort]}]"
        return self.labels[k]


def vind_overig(tekst: str, register: Register, alle_datums: bool) -> tuple[list[Vondst], int]:
    vondsten: list[Vondst] = []

    for m in BSN_LABEL_RE.finditer(tekst):
        vondsten.append(Vondst(m.start(1), m.end(1), "bsn", "[BSN]", 9))
    for m in BSN_RE.finditer(tekst):
        if elfproef(re.sub(r"\D", "", m[1])):
            vondsten.append(Vondst(m.start(1), m.end(1), "bsn", "[BSN]", 9))

    for m in IBAN_RE.finditer(tekst):
        ruw = m[1]
        land = ruw[:2]
        # Bepaal het eind: precies de lengte van het land, anders inkorten tot geldig.
        posities = [i for i, c in enumerate(ruw) if c != " "]
        lengtes = [IBAN_LENGTE[land]] if land in IBAN_LENGTE else range(len(posities), 14, -1)
        for n in lengtes:
            if n > len(posities):
                continue
            stuk = ruw[: posities[n - 1] + 1]
            if iban_geldig(stuk):
                sleutel = stuk.replace(" ", "")
                vondsten.append(Vondst(m.start(1), m.start(1) + len(stuk), "iban",
                                       register.label("iban", sleutel, "IBAN"), 9))
                break
    for m in REKENING_LABEL_RE.finditer(tekst):
        sleutel = re.sub(r"\D", "", m[1])
        vondsten.append(Vondst(m.start(1), m.end(1), "iban",
                               register.label("iban", sleutel, "rekening"), 8))

    for m in EMAIL_RE.finditer(tekst):
        vondsten.append(Vondst(m.start(), m.end(), "email", "[e-mail]", 7))
    for m in TELEFOON_RE.finditer(tekst):
        vondsten.append(Vondst(m.start(), m.end(), "telefoon", "[telefoon]", 6))

    for regex in (STRAAT_RE, POSTCODE_RE):
        for m in regex.finditer(tekst):
            vondsten.append(Vondst(m.start(), m.end(), "adres", "[adres]", 4))
    for m in ADRES_LABEL_RE.finditer(tekst):
        vondsten.append(Vondst(m.start(1), m.end(1), "adres", "[adres]", 3))

    data, blijft = vind_data(tekst, alle_datums)
    vondsten.extend(data)
    return vondsten, blijft


# ---------------------------------------------------------------------------
# Namen
# ---------------------------------------------------------------------------

@dataclass
class Vermelding:
    tekst: str                        # de naam zonder titel/aanhef
    voornamen: list[str]
    initialen: str
    achternaam: str | None
    positie: tuple[int, int]          # (pagina, teken) voor de volgorde van labels
    hint: str | None = None           # "achternaam" / "voornaam" bij labels als "Achternaam:"


@dataclass
class Persoon:
    voornamen: list[str] = field(default_factory=list)
    initialen: str = ""
    achternaam: str | None = None
    positie: tuple[int, int] = (10**9, 0)
    label: str = ""


def _norm(s: str | None) -> str:
    return re.sub(r"\s+", " ", s or "").strip().lower()


def ontleed_naam(tekst: str, positie=(0, 0), hint=None) -> Vermelding | None:
    """Splits een naam in voornamen, initialen en achternaam."""
    s = re.sub(r"\s+", " ", tekst).strip(" ,.;:")
    if not s:
        return None
    voor, achter = s, None
    if "," in s:  # "Jansen, J." of "Berg, Jan van den"
        achter, voor = (x.strip() for x in s.split(",", 1))
        delen = voor.split()
        tv_eind = [d for d in delen if _tv(d)]
        voor = " ".join(d for d in delen if not _tv(d))
        if tv_eind:
            achter = " ".join(tv_eind + [achter])
    tokens = voor.split()
    # Titels en aanhef aan het begin weghalen.
    while tokens and (tokens[0].lower().rstrip(".") in TITEL_WOORDEN | GROETEN
                      or (tokens[0].lower() == "de" and len(tokens) > 1 and tokens[1].lower() == "heer")):
        tokens.pop(0)
    if not tokens and not achter:
        return None
    kern = " ".join(tokens) + (f", {achter}" if achter else "")
    if achter is None:
        if len(tokens) == 1:
            tok = tokens[0]
            if hint == "voornaam":
                return Vermelding(tok, [tok], "", None, positie, hint)
            if hint == "achternaam":
                return Vermelding(tok, [], "", tok, positie, hint)
            return Vermelding(tok, [tok], "", None, positie, "enkel") if not _tv(tok) else None
        if hint == "voornaam":
            return Vermelding(kern, tokens, "", None, positie, hint)
        if hint == "achternaam":
            return Vermelding(kern, [], "", kern, positie, hint)
        i = next((k for k, t in enumerate(tokens[:-1]) if _tv(t) and k > 0), None)
        if i is None and _tv(tokens[0]):
            i = 0
        if i is None:
            i = len(tokens) - 1
        achter, tokens = " ".join(tokens[i:]), tokens[:i]
        kern = " ".join(tokens + [achter])
    voornamen, initialen = [], ""
    for t in tokens:
        if "." in t:
            initialen += re.sub(r"[^A-Za-zÀ-ÿ]", "", t).upper()
        else:
            voornamen.append(t)
    return Vermelding(kern, voornamen, initialen, achter, positie, hint)


TITEL_NAAM_RE = re.compile(rf"(?i:{NW}{TITELS})[ ]+({NAAM})")
LABEL_NAAM_RE = re.compile(
    rf"(?i:\b(naam|volledige naam|achternaam|geslachtsnaam|geboortenaam|voornamen|voornaam|roepnaam|"
    rf"naam (?:aanvrager|partner|echtgeno(?:ot|te)|kind|vader|moeder|cliënt|client|patiënt|patient|"
    rf"klant|verzekerde|werknemer|huurder|ouder|voogd))\b)"
    rf"[ \t]*:[ \t]*\n?[ \t]*({NAAM_OMGEKEERD}|{NAAM})"
)


class NaamHerkenner:
    def __init__(self, gebruik_ner: bool, behoud: set[str], extra_namen: list[str]):
        self.behoud = {_norm(b) for b in behoud}
        self.extra_namen = extra_namen
        self.nlp = None
        if gebruik_ner:
            try:
                import spacy
                self.nlp = spacy.load("nl_core_news_md", disable=["parser", "lemmatizer"])
            except Exception as e:  # pragma: no cover - afhankelijk van installatie
                print(f"Let op: spaCy-model niet geladen ({e.__class__.__name__}); "
                      "alleen patronen en --namen worden gebruikt.\n"
                      "  Installeer met: python -m spacy download nl_core_news_md",
                      file=sys.stderr)

    def _toegestaan(self, v: Vermelding) -> bool:
        delen = v.voornamen + ([v.achternaam] if v.achternaam else [])
        if v.hint == "enkel":
            delen = [v.tekst.strip()]
        if not delen:
            return False
        if _norm(v.tekst) in self.behoud or any(_norm(d) in self.behoud for d in delen):
            return False
        if any(_norm(d) in GEEN_NAAM for d in delen):
            return False
        return all(len(d) >= 2 and not any(c.isdigit() for c in d) for d in delen)

    def vermeldingen(self, paginas: list[str]) -> list[Vermelding]:
        uit: list[Vermelding] = []

        def voeg_toe(tekst, pos, hint=None):
            v = ontleed_naam(tekst, pos, hint)
            if v and self._toegestaan(v):
                uit.append(v)

        for regel in self.extra_namen:
            voeg_toe(regel, (-1, 0))
        for p, tekst in enumerate(paginas):
            for m in TITEL_NAAM_RE.finditer(tekst):
                # Na een titel is één woord een achternaam ("Dhr. Jansen").
                hint = "achternaam" if " " not in m[1].strip() else None
                voeg_toe(m[1], (p, m.start(1)), hint)
            for m in LABEL_NAAM_RE.finditer(tekst):
                soort = m[1].lower()
                hint = ("achternaam" if soort in ("achternaam", "geslachtsnaam", "geboortenaam")
                        else "voornaam" if soort in ("voornamen", "voornaam", "roepnaam") else None)
                voeg_toe(m[2], (p, m.start(2)), hint)
            if self.nlp is not None:
                for ent in self.nlp(tekst).ents:
                    if ent.label_ in ("PERSON", "PER"):
                        voeg_toe(ent.text, (p, ent.start_char))
        return uit


def koppel_personen(vermeldingen: list[Vermelding]) -> tuple[list[Persoon], dict[str, str]]:
    """Voeg vermeldingen samen tot personen en ken labels toe.

    Geeft de personen terug plus een tabel achternaam -> familielabel voor
    achternamen die bij meerdere personen horen.
    """
    personen: list[Persoon] = []

    def met_achternaam(a):
        return [p for p in personen if _norm(p.achternaam) == _norm(a)]

    def bijwerken(p: Persoon, v: Vermelding):
        p.positie = min(p.positie, v.positie)
        for vn in v.voornamen:
            if vn.lower() not in (x.lower() for x in p.voornamen):
                p.voornamen.append(vn)
        if v.initialen and len(v.initialen) > len(p.initialen):
            p.initialen = v.initialen

    volledig = [v for v in vermeldingen if v.achternaam and v.voornamen]
    initiaal = [v for v in vermeldingen if v.achternaam and not v.voornamen and v.initialen]
    achter = [v for v in vermeldingen if v.achternaam and not v.voornamen and not v.initialen]
    voor = [v for v in vermeldingen if not v.achternaam and v.voornamen and v.hint != "enkel"]
    enkel = [v for v in vermeldingen if v.hint == "enkel"]

    for v in sorted(volledig, key=lambda v: v.positie):
        kand = [p for p in met_achternaam(v.achternaam)
                if p.voornamen and p.voornamen[0].lower() == v.voornamen[0].lower()]
        if kand:
            bijwerken(kand[0], v)
        else:
            p = Persoon(achternaam=v.achternaam)
            bijwerken(p, v)
            personen.append(p)

    for v in sorted(initiaal, key=lambda v: v.positie):
        kand = [p for p in met_achternaam(v.achternaam)
                if (p.voornamen and p.voornamen[0][0].upper() == v.initialen[0])
                or (p.initialen and p.initialen[0] == v.initialen[0])]
        if len(kand) == 1:
            bijwerken(kand[0], v)
        elif not kand:
            p = Persoon(achternaam=v.achternaam)
            bijwerken(p, v)
            personen.append(p)

    for v in sorted(achter, key=lambda v: v.positie):
        kand = met_achternaam(v.achternaam)
        if len(kand) == 1:
            bijwerken(kand[0], v)
        elif not kand:
            p = Persoon(achternaam=v.achternaam)
            bijwerken(p, v)
            personen.append(p)

    def met_voornaam(n):
        return [p for p in personen if n.lower() in (x.lower() for x in p.voornamen)]

    for v in sorted(voor, key=lambda v: v.positie):
        kand = met_voornaam(v.voornamen[0])
        if len(kand) == 1:
            bijwerken(kand[0], v)
        elif not kand:
            p = Persoon()
            bijwerken(p, v)
            personen.append(p)

    for v in sorted(enkel, key=lambda v: v.positie):
        woord = v.voornamen[0]
        bekend = met_voornaam(woord) + met_achternaam(woord)
        if len(bekend) == 1:
            bekend[0].positie = min(bekend[0].positie, v.positie)
        if bekend:
            continue
        p = Persoon(voornamen=[woord], positie=v.positie)
        personen.append(p)

    for i, p in enumerate(sorted(personen, key=lambda p: p.positie), 1):
        p.label = f"Persoon {i}"

    families: dict[str, str] = {}
    letters = iter("ABCDEFGHIJKLMNOPQRSTUVWXYZ")
    for p in sorted(personen, key=lambda p: p.positie):
        a = _norm(p.achternaam)
        if a and a not in families and len(met_achternaam(p.achternaam)) > 1:
            families[a] = f"Familie {next(letters, '?')}"
    return personen, families


def _tok(t: str) -> str:
    if _tv(t):
        return f"(?i:{re.escape(t)})"
    return f"(?:{re.escape(t)}|{re.escape(t.upper())})"


def _achternaam_patroon(a: str) -> str:
    return r"[ ]+".join(_tok(t) for t in a.split())


def naam_patronen(personen: list[Persoon], families: dict[str, str],
                  vermeldingen: list[Vermelding]) -> list[tuple[re.Pattern, str]]:
    patronen: list[tuple[str, str]] = []
    voornaam_telling: dict[str, int] = {}
    for p in personen:
        for vn in p.voornamen:
            voornaam_telling[vn.lower()] = voornaam_telling.get(vn.lower(), 0) + 1

    for p in personen:
        vn_alt = "|".join(_tok(v) for v in p.voornamen)
        inits = {v[0].upper() for v in p.voornamen} | ({p.initialen[0]} if p.initialen else set())
        init_pat = (rf"(?:{'|'.join(sorted(inits))})\.[ ]?(?:[{HOOFD}]\.[ ]?){{0,4}}" if inits else None)
        if p.achternaam:
            ach = _achternaam_patroon(p.achternaam)
            # Tussenvoegsel aan het begin mag ook met hoofdletter ("Van den Berg").
            voor_deel = []
            if vn_alt:
                voor_deel.append(rf"(?:{vn_alt})(?:[ ]+(?:{vn_alt}))*")
            if init_pat:
                voor_deel.append(init_pat)
            if voor_deel:
                patronen.append((rf"(?:{'|'.join(voor_deel)})[ ]*{ach}", p.label))
                patronen.append((rf"{ach},[ ]?(?:{'|'.join(voor_deel)})(?:[ ]{TV_I}){{0,3}}", p.label))
            fam = families.get(_norm(p.achternaam))
            patronen.append((ach, fam or p.label))
        for vn in p.voornamen:
            if voornaam_telling[vn.lower()] == 1:
                patronen.append((_tok(vn), p.label))
            else:
                patronen.append((_tok(vn), "[voornaam]"))

    # Letterlijke vermeldingen als vangnet.
    for v in vermeldingen:
        if v.hint == "enkel" or not (v.voornamen or v.initialen):
            continue  # losse woorden en achternamen worden al door de patronen hierboven gedekt
        label = next((p.label for p in personen
                      if (v.achternaam and _norm(p.achternaam) == _norm(v.achternaam))
                      or (not v.achternaam and v.voornamen and v.voornamen[0].lower()
                          in (x.lower() for x in p.voornamen))), None)
        tekst = re.sub(r"\s+", " ", v.tekst).strip(" ,.;:")
        if label and len(tekst) >= 2:
            patronen.append((r"[ ]+".join(re.escape(t) for t in tekst.split()), label))

    return [(re.compile(rf"{NW}(?:{pat}){NA}"), label) for pat, label in patronen]


def vind_namen(tekst: str, patronen: list[tuple[re.Pattern, str]]) -> list[Vondst]:
    uit = []
    for regex, label in patronen:
        for m in regex.finditer(tekst):
            uit.append(Vondst(m.start(), m.end(), "naam", label, 2))
    return uit


# ---------------------------------------------------------------------------
# PDF lezen, redigeren en schrijven
# ---------------------------------------------------------------------------

@dataclass
class PaginaTekst:
    tekst: str
    vakken: list[tuple[fitz.Rect, int] | None]   # per teken: (rechthoek, regel-id)


def lees_pagina(page: fitz.Page) -> PaginaTekst:
    tekens: list[str] = []
    vakken: list[tuple[fitz.Rect, int] | None] = []
    vlaggen = fitz.TEXT_PRESERVE_WHITESPACE | fitz.TEXT_MEDIABOX_CLIP
    raw = page.get_text("rawdict", flags=vlaggen, sort=True)
    regel_id = 0
    for blok in raw["blocks"]:
        if blok.get("type") != 0:
            continue
        for regel in blok["lines"]:
            for span in regel["spans"]:
                for ch in span["chars"]:
                    tekens.append(ch["c"])
                    vakken.append((fitz.Rect(ch["bbox"]), regel_id))
            tekens.append("\n")
            vakken.append(None)
            regel_id += 1
        tekens.append("\n")
        vakken.append(None)
    return PaginaTekst("".join(tekens), vakken)


def _lettergrootte(rect: fitz.Rect, tekst: str) -> float:
    breedte = fitz.get_text_length(tekst, fontname="helv", fontsize=1) or 1
    return max(5.0, min(rect.height * 0.75, rect.width / breedte))


def redigeer_pagina(page: fitz.Page, pt: PaginaTekst, vondsten: list[Vondst]) -> None:
    labels: list[tuple[fitz.Rect, str]] = []
    for v in vondsten:
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


def schoon_document(doc: fitz.Document) -> None:
    """Verwijder metadata, bladwijzers en bijlagen (kunnen ook namen bevatten)."""
    doc.set_metadata({})
    doc.del_xml_metadata()
    doc.set_toc([])
    for naam in list(doc.embfile_names()):
        doc.embfile_del(naam)


# ---------------------------------------------------------------------------
# Hoofdlogica
# ---------------------------------------------------------------------------

@dataclass
class Resultaat:
    pdf: Path | None
    tekst: Path | None
    tellingen: dict[str, int]
    datums_blijven: int
    scans: list[int]
    lekken: list[tuple[int, str]]


class Anonimiseerder:
    def __init__(self, namen: list[str] | None = None, behoud: set[str] | None = None,
                 gebruik_ner: bool = True, alle_datums: bool = False):
        self.herkenner = NaamHerkenner(gebruik_ner, behoud or set(), namen or [])
        self.alle_datums = alle_datums

    def analyseer(self, paginas: list[str]):
        """Vind alle vondsten per pagina (namen worden over het hele document gekoppeld)."""
        vermeldingen = self.herkenner.vermeldingen(paginas)
        personen, families = koppel_personen(vermeldingen)
        patronen = naam_patronen(personen, families, vermeldingen)
        register = Register()
        per_pagina, blijft = [], 0
        for tekst in paginas:
            overig, b = vind_overig(tekst, register, self.alle_datums)
            blijft += b
            per_pagina.append(kies_vondsten(overig + vind_namen(tekst, patronen)))
        return per_pagina, personen, patronen, register, blijft

    def anonimiseer_tekst(self, tekst: str) -> str:
        per_pagina, *_ = self.analyseer([tekst])
        return pas_toe(tekst, per_pagina[0])

    def verwerk(self, invoer: Path, uitmap: Path, maak_pdf=True, maak_tekst=True,
                sleutelbestand: Path | None = None) -> Resultaat:
        doc = fitz.open(invoer)
        if doc.needs_pass:
            raise ValueError("PDF is met een wachtwoord beveiligd")
        # Formuliervelden en annotaties omzetten naar gewone pagina-inhoud,
        # zodat ook ingevulde formulieren geanonimiseerd worden.
        if hasattr(doc, "bake"):
            doc.bake(annots=True, widgets=True)
        lezingen = [lees_pagina(p) for p in doc]
        paginas = [l.tekst for l in lezingen]
        scans = [i + 1 for i, (p, l) in enumerate(zip(doc, lezingen))
                 if not l.tekst.strip() and p.get_images()]

        per_pagina, personen, patronen, register, blijft = self.analyseer(paginas)

        # Ook de bestandsnaam kan namen bevatten.
        stam_vondsten = kies_vondsten(vind_namen(invoer.stem, patronen))
        stam = re.sub(r"[^\w\- ]+", "", pas_toe(invoer.stem, stam_vondsten)).strip() or "document"
        uitmap.mkdir(parents=True, exist_ok=True)

        tellingen: dict[str, int] = {}
        for vs in per_pagina:
            for v in vs:
                tellingen[v.soort] = tellingen.get(v.soort, 0) + 1

        tekst_pad = pdf_pad = None
        if maak_tekst:
            tekst_pad = uitmap / f"{stam}_anoniem.md"
            delen = [f"# {stam} (geanonimiseerd)\n"]
            for i, (tekst, vs) in enumerate(zip(paginas, per_pagina), 1):
                schoon = re.sub(r"\n{3,}", "\n\n", pas_toe(tekst, vs)).strip()
                delen.append(f"## Pagina {i}\n\n{schoon}\n")
            tekst_pad.write_text("\n".join(delen), encoding="utf-8")

        lekken: list[tuple[int, str]] = []
        if maak_pdf:
            for page, pt, vs in zip(doc, lezingen, per_pagina):
                if vs:
                    redigeer_pagina(page, pt, vs)
            schoon_document(doc)
            pdf_pad = uitmap / f"{stam}_anoniem.pdf"
            doc.save(pdf_pad, garbage=4, deflate=True, clean=True)
            lekken = controleer(pdf_pad, paginas, per_pagina)
        doc.close()

        if sleutelbestand:
            sleutel = {
                "personen": {p.label: {"voornamen": p.voornamen, "initialen": p.initialen,
                                       "achternaam": p.achternaam} for p in personen},
                "rekeningen": {label: waarde for (_, waarde), label in register.labels.items()},
            }
            sleutelbestand.write_text(json.dumps(sleutel, indent=2, ensure_ascii=False), encoding="utf-8")

        return Resultaat(pdf_pad, tekst_pad, tellingen, blijft, scans, lekken)


def controleer(pdf_pad: Path, paginas: list[str], per_pagina: list[list[Vondst]]) -> list[tuple[int, str]]:
    """Kijk of gevonden gegevens nog letterlijk in de nieuwe PDF staan."""
    lekken = []
    with fitz.open(pdf_pad) as doc:
        for i, (page, tekst, vs) in enumerate(zip(doc, paginas, per_pagina), 1):
            nieuw = re.sub(r"\s+", " ", page.get_text())
            for v in vs:
                origineel = re.sub(r"\s+", " ", tekst[v.start:v.eind]).strip()
                if v.soort in ("datum", "geboortedatum") or len(origineel) < 3:
                    continue
                if re.search(rf"{NW}{re.escape(origineel)}{NA}", nieuw):
                    lekken.append((i, v.soort))
    return lekken


def lees_lijst(pad: Path | None) -> list[str]:
    if not pad:
        return []
    return [r.strip() for r in pad.read_text(encoding="utf-8").splitlines()
            if r.strip() and not r.lstrip().startswith("#")]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Anonimiseer PDF's (namen, BSN, adressen, geboortedata, rekeningnummers) "
                    "zodat ze aan een AI gegeven kunnen worden.")
    ap.add_argument("invoer", nargs="+", type=Path, help="PDF-bestand(en) of map(pen) met PDF's")
    ap.add_argument("-o", "--uitvoer", type=Path, default=Path("geanonimiseerd"),
                    help="uitvoermap (standaard: ./geanonimiseerd)")
    ap.add_argument("--namen", type=Path,
                    help="tekstbestand met namen die zeker vervangen moeten worden (één per regel)")
    ap.add_argument("--behoud", type=Path,
                    help="tekstbestand met woorden/namen die NIET vervangen mogen worden (één per regel)")
    ap.add_argument("--alle-datums", action="store_true",
                    help="vervang álle datums door het jaartal, niet alleen geboortedata")
    ap.add_argument("--geen-ner", action="store_true",
                    help="gebruik geen spaCy-taalmodel voor naamherkenning (alleen patronen en --namen)")
    ap.add_argument("--alleen-tekst", action="store_true", help="maak alleen het Markdown-tekstbestand")
    ap.add_argument("--alleen-pdf", action="store_true", help="maak alleen de geanonimiseerde PDF")
    ap.add_argument("--sleutel", action="store_true",
                    help="schrijf ook een sleutelbestand (pseudoniem -> echte gegevens). "
                         "Bevat persoonsgegevens: deel dit nooit met een AI!")
    args = ap.parse_args(argv)

    bestanden: list[Path] = []
    for pad in args.invoer:
        if pad.is_dir():
            bestanden += sorted(p for p in pad.rglob("*") if p.suffix.lower() == ".pdf")
        elif pad.suffix.lower() == ".pdf" and pad.exists():
            bestanden.append(pad)
        else:
            print(f"Overgeslagen (geen PDF of bestaat niet): {pad}", file=sys.stderr)
    if not bestanden:
        print("Geen PDF-bestanden gevonden.", file=sys.stderr)
        return 1

    anon = Anonimiseerder(lees_lijst(args.namen), set(lees_lijst(args.behoud)),
                          gebruik_ner=not args.geen_ner, alle_datums=args.alle_datums)
    fouten = 0
    for i, pad in enumerate(bestanden, 1):
        sleutel = args.uitvoer / f"sleutel_{i}.json" if args.sleutel else None
        try:
            r = anon.verwerk(pad, args.uitvoer, maak_pdf=not args.alleen_tekst,
                             maak_tekst=not args.alleen_pdf, sleutelbestand=sleutel)
        except Exception as e:
            print(f"[{i}] FOUT bij {pad.name}: {e}", file=sys.stderr)
            fouten += 1
            continue
        samenvatting = ", ".join(f"{n}x {s}" for s, n in sorted(r.tellingen.items())) or "niets gevonden"
        print(f"[{i}] {pad.name}: {samenvatting}")
        for uit in (r.pdf, r.tekst):
            if uit:
                print(f"     -> {uit}")
        if r.datums_blijven:
            print(f"     ! {r.datums_blijven} datum(s) zonder geboorte-context niet aangepast "
                  "(gebruik --alle-datums om ook die te vervangen)")
        if r.scans:
            print(f"     ! pagina('s) {', '.join(map(str, r.scans))} lijken gescand (geen tekst): "
                  "NIET geanonimiseerd. Laat eerst OCR uitvoeren.")
        for pagina, soort in r.lekken:
            print(f"     ! pagina {pagina}: een {soort} staat mogelijk nog in de PDF, controleer handmatig")
        if sleutel:
            print(f"     sleutel: {sleutel} (bevat persoonsgegevens, niet delen!)")
    print("\nControleer het resultaat altijd zelf voordat je het met een AI deelt.")
    return 1 if fouten else 0


if __name__ == "__main__":
    sys.exit(main())
