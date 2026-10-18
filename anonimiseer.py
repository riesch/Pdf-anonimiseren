#!/usr/bin/env python3
"""Anonimiseer PDF-bestanden zodat ze geschikt zijn om aan een AI-model te geven.

Wat er gebeurt:
  * namen          -> alleen de (roep)voornaam: "Jan Pieter Jansen" -> "Jan";
                      zonder bekende voornaam "Persoon 1", "Persoon 2", ...;
  * BSN            -> "[BSN]";
  * adressen       -> "[adres]" (straat + huisnummer, postcode + woonplaats);
  * geboortedata   -> alleen het geboortejaar (ook voluit geschreven, zoals in aktes);
  * rekeningnummers-> bankcode + laatste 4 cijfers ("NL91ABNA0417164300" -> "ABNA4300");
  * notariële akten: geboorteplaats, woonplaats, legitimatienummers en
                      kadastrale aanduidingen worden ook verwijderd;
  * verder: kentekens, klant-/polis-/dossier-/patiëntnummers, btw-nummers,
                      IP-adressen en overlijdensdata (-> jaar).

Met een familie.txt (of --familie) worden alleen de namen van je familie- en
gezinsleden vervangen; andere namen blijven staan.
  * e-mail/telefoon-> "[e-mail]" / "[telefoon]".

Per invoerbestand worden een geanonimiseerde PDF (echte redactie: de originele
tekst wordt uit het bestand verwijderd) en een Markdown-tekstbestand gemaakt.
Het tekstbestand is het handigst om aan een AI te voeren.

Gebruik:
    python anonimiseer.py brief.pdf
    python anonimiseer.py map_met_pdfs/ -o uitvoer/ --namen namen.txt
    python anonimiseer.py NL00BANK0123456789_2024.csv     # bankexport, zie bankdata.py
    python anonimiseer.py afschrift.xml                     # CAMT.053, zie camt.py
"""

from __future__ import annotations

import sys

if sys.version_info < (3, 10):
    sys.exit(f"Dit script heeft Python 3.10 of nieuwer nodig; je gebruikt {sys.version.split()[0]}.\n"
             "Maak de omgeving opnieuw met een nieuwere Python, zie 'Installatie' in README.md.")

import argparse
import contextlib
import io
import json
import re
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
# Notariële akten noemen alle voornamen voluit, vaak vier of meer.
NAAM = rf"(?:{INITIALEN}[ ]?|(?:{CAP}[ ]){{0,6}})(?:{TV_I}[ ]){{0,3}}{CAP}"
NAAM_OMGEKEERD = rf"(?:{TV_I}[ ]){{0,3}}{CAP},[ ]?(?:{INITIALEN}|{CAP}(?:[ ]{CAP}){{0,6}})(?:[ ]{TV}){{0,3}}"

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

# Tussen de groepjes mogen spaties en ook een regelovergang staan: in aangiften en akten
# loopt een rekeningnummer soms door op de volgende regel. Lengte per land en de
# controlesom voorkomen dat losse regels aan elkaar geplakt worden.
# (In een lookahead, zodat een rekeningnummer op de volgende regel niet wordt overgeslagen.)
IBAN_RE = re.compile(r"(?<![A-Za-z0-9])(?=([A-Z]{2}\d{2}(?:[ \t]*\n?[ \t]*[A-Z0-9]){10,30}))")
REKENING_LABEL_RE = re.compile(
    r"(?i)\b(?:rekening(?:nummer)?|bankrekening(?:nummer)?|reknr|rek\.?[ ]?nr|girorekening|giro)"
    r"\b\.?[ \t]*[:.]?[ \t]*(\d{1,3}(?:[ .]?\n?[ \t]*\d){5,9})(?!\d)"
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
# "10 februari 1980", "10 feb. 1980", "10-feb-1980", "10 februari, 1980", "10e februari 1980"
DATUM_TXT_RE = re.compile(
    rf"(?i)(?<!\d)(\d{{1,2}})(?:e|ste|de)?(?:[ ]+|-)({_MAAND_ALT})\.?(?:,?[ ]+|-)(\d{{4}})(?!\d)"
)
GETAL_WOORDEN = {
    "een": 1, "één": 1, "twee": 2, "drie": 3, "vier": 4, "vijf": 5, "zes": 6, "zeven": 7,
    "acht": 8, "negen": 9, "tien": 10, "elf": 11, "twaalf": 12, "dertien": 13, "veertien": 14,
    "vijftien": 15, "zestien": 16, "zeventien": 17, "achttien": 18, "negentien": 19,
}
TIENTALLEN = {"twintig": 20, "dertig": 30, "veertig": 40, "vijftig": 50, "zestig": 60,
              "zeventig": 70, "tachtig": 80, "negentig": 90}


RANGTELWOORDEN = {"eerste": 1, "derde": 3}


def woord_naar_getal(w: str) -> int | None:
    """'twaalf' -> 12, 'vijfentachtig' -> 85, 'drieëntwintig' -> 23, 'negen en twintig' -> 29,
    en rangtelwoorden zoals in akten: 'tiende' -> 10, 'eenentwintigste' -> 21 (1 t/m 99)."""
    w = re.sub(r"\s+", "", w.lower().replace("ë", "e").replace("é", "e"))
    if w in RANGTELWOORDEN:
        return RANGTELWOORDEN[w]
    if w.endswith("ste") and len(w) > 5:     # achtste, twintigste, eenentwintigste
        return woord_naar_getal(w[:-3])
    if w.endswith("de") and w[:-2] in GETAL_WOORDEN:   # tweede, vierde, tiende, elfde, dertiende
        return GETAL_WOORDEN[w[:-2]]
    if w in GETAL_WOORDEN:
        return GETAL_WOORDEN[w]
    if w in TIENTALLEN:
        return TIENTALLEN[w]
    m = re.fullmatch(r"(een|twee|drie|vier|vijf|zes|zeven|acht|negen)en(\w+)", w)
    if m and m[2] in TIENTALLEN:
        return GETAL_WOORDEN[m[1]] + TIENTALLEN[m[2]]
    return None


def jaar_in_woorden(w: str) -> int | None:
    """'negentienhonderdvijfentachtig' -> 1985, 'tweeduizend twee' -> 2002,
    'negentienhonderd en tachtig' -> 1980."""
    w = re.sub(r"\s+", "", w.lower())
    for basis, waarde in (("negentienhonderd", 1900), ("achttienhonderd", 1800), ("tweeduizend", 2000)):
        if w.startswith(basis):
            rest = w[len(basis):]
            if rest.startswith("en") and woord_naar_getal(rest[2:]):
                rest = rest[2:]
            if not rest:
                return waarde
            n = woord_naar_getal(rest)
            return waarde + n if n else None
    return None


# "twaalf maart negentienhonderdvijfentachtig", "de tiende februari negentienhonderd en tachtig",
# "negen en twintig mei tweeduizend twee"
# Woorden mogen over regels verspreid staan ("negentienhonderd\ntweeënzeventig"), zoals in akten.
DATUM_WOORD_RE = re.compile(
    rf"(?i)(?<![\w])(?:de\s+)?([a-zëé]+(?:\s+en\s+[a-zëé]+)?)\s+({_MAAND_ALT})\s+"
    rf"((?:negentienhonderd|achttienhonderd|tweeduizend)[a-zëé]*(?:\s+[a-zëé]+){{0,3}})"
)
GEBOORTE_RE = re.compile(
    r"(?i)(geb(?:oren|oorte\w*)?\b|geb\.|date of birth|birth|d\.o\.b\.?|\bdob\b"
    r"|overle\w*|overlijd\w*|sterf\w*|†)"
)
OVERLIJDEN_RE = re.compile(r"(?i)overl|sterf|†")

STRAAT_SUFFIX = (
    "straat|straatweg|laan|weg|plein|gracht|kade|singel|dijk|dreef|hof|pad|steeg|baan|"
    "wal|markt|plantsoen|erf|ring|allee|boulevard|dam|haven|park|oord|rade|zijde|"
    "veld|akker|hoven|kamp|wijk|burcht|poort|brink|horst|donk|hoek|weide|polder|"
    "berg|bosch|dal|gaarde|tuin|hout|kwartier|vliet|wetering|vaart|stede|hoeve|"
    "kanaal|promenade|passage|galerij|steenweg|heuvel|lindeboom|zoom|meent"
)
# Toevoeging: vast aan het nummer ("12a", "12-hs") of een losse hoofdletter ("12 A"); niet "12 te".
HUISNUMMER = (r"[ ]+(?:(?i:nummer|nr\.?|no\.?)[ ]+)?\d{1,5}"
              r"(?:-?(?:[A-Za-z]{1,2}|hs|bis)(?![\w])|[ ]-?[ ]?(?:[A-Z]{1,2}|hs|bis)(?![\w]))?"
              r"(?:[ ]?-[ ]?\d{1,4})?(?![\w])")
STRAAT_RE = re.compile(
    rf"{NW}(?:(?:[{HOOFD}][\w'.-]*|{TV_I})[ ]){{0,3}}[{HOOFD}][{KLEIN}'-]*(?:{STRAAT_SUFFIX}){HUISNUMMER}"
    rf"|{NW}(?:Laan|Weg|Plein|Kade|Dijk|Singel|Gracht|Straat|Dreef|Hof|Markt|Oude|Nieuwe)"
    rf"(?:[ ](?:van|der|de|den|het|ten|ter|op|aan|'t))*[ ][{HOOFD}][\w'-]*{HUISNUMMER}"
    # in hoofdletters: "KERKSTRAAT 12"
    rf"|{NW}(?:[{HOOFD}'.-]+[ ]){{0,3}}[{HOOFD}'-]*(?:{STRAAT_SUFFIX.upper()}){HUISNUMMER}"
)
# Een straatnaam zonder herkenbaar achtervoegsel ("Vredenburg 4", "Achter Clarenburg 2"):
# alleen als adres herkend als de context het zegt (naast een postcode, na "wonende te").
STRAAT_ALGEMEEN = rf"(?:(?:[{HOOFD}][\w'.-]*|{TV_I})[ ]){{0,4}}[{HOOFD}][\w'.-]*{HUISNUMMER}"
STAD_TWEEDELIG = r"(?:Den|De|Sint|St\.|Nieuw|Oud|Hoog|Laag|Groot|Klein|Bergen|Hendrik|Ter|Ten)"
POSTCODE = (
    rf"{NW}[1-9]\d{{3}}[ ]?(?!SA|SD|SS)[A-Z]{{2}}{NA}"
    rf"(?:[ ,]{{1,3}}(?:'[sS]-|'[tT][ ])?(?:{STAD_TWEEDELIG}[ ])?[{HOOFD}][\w'-]+"
    rf"(?:[ ](?:aan|a/d|op|bij|in)[ ](?:den[ ]|de[ ])?[{HOOFD}][\w'-]+)?)?"
)
POSTCODE_RE = re.compile(POSTCODE)
# "3512 JK Utrecht, Vredenburg 4" en "Vredenburg 4, 3512 JK Utrecht" als één adres.
ADRES_RONDOM_POSTCODE_RE = re.compile(
    rf"{POSTCODE},\s*{STRAAT_ALGEMEEN}|{NW}{STRAAT_ALGEMEEN},\s*{POSTCODE}"
)
ADRES_LABEL_RE = re.compile(
    r"(?im)\b(?:woon)?(?:adres(?:gegevens)?|straat(?:naam)?|huisnummer|postcode|woonplaats|"
    r"geboorteplaats|verblijfplaats|vestigingsadres|correspondentieadres|postadres)"
    r"[ \t]*:[ \t]*([^\n]*?\S)(?=[ \t]{2,}|\t|$)"
)


PLAATS = r"(?:'[sS]-|'[tT][ ])?[A-Z][\w'-]+(?:[ -](?:(?:aan|op|bij|den|de|a/d)[ ])?[A-Z][\w'-]+){0,3}"
PLAATS_RE = re.compile(
    rf"(?i:\b(geboren|wonende|woonachtig|wonend|zonder vaste woon- of verblijfplaats))[ ]+"
    rf"(?i:te|in)[ ]+({PLAATS})"
)
WONEND = r"(?i:\b(?:wonende|woonachtig|wonend))"
# "wonende te Utrecht, Vredenburg 4" en "wonende aan de Neude 11 (te Utrecht)"
WOONADRES_RE = re.compile(
    rf"{WONEND}[ ]+(?i:te|in)[ ]+{PLAATS},\s*({STRAAT_ALGEMEEN})"
    rf"|{WONEND}[ ]+(?i:aan|op)[ ]+(?:(?i:de|het)[ ]+)?({STRAAT_ALGEMEEN})(?:\s+(?i:te)\s+({PLAATS}))?"
)
# Na "paspoort", "identiteitskaart", "rijbewijs", "legitimatie..." het eerstvolgende
# documentnummer, ook als het woord vastgeschreven is ("paspoortnummer"), "nummer"
# ontbreekt of het nummer op de volgende regel staat (zoals vaak in akten).
DOCUMENT_RE = re.compile(
    r"(?i:\b(?:paspoort|identiteitskaart|identiteitsbewijs|id-kaart|identiteitsdocument|rijbewijs|"
    r"reisdocument|legitimatie|verblijfsdocument|verblijfsvergunning|vreemdelingendocument)\w*)"
    r"[^\n]{0,80}?(?:\n[^\n]{0,60}?)?"
    r"(?<![\w-])((?=[A-Za-z0-9]*\d)(?=[A-Za-z0-9]*[A-Z]|\d{8,})[A-Za-z0-9]{6,12})(?![\w-])"
)
KADASTER_RE = re.compile(
    r"(?i)\bkadastraal[ ]+bekend[ ]+(?:als[ ]+)?((?:gemeente[ ]+)?[^\n]{0,60}?sectie[ ]+[A-Z]{1,2}[, ]+"
    r"(?:complex(?:aanduiding)?[ ]+)?(?:nummer|nr\.?)[ ]+\d+(?:[ ]*[A-Z][ ]*\d+)?)"
)


KENTEKEN_RE = re.compile(
    r"(?<![\w-])(?:[A-Z]{2}-\d{2}-[A-Z]{2}|\d{2}-[A-Z]{2}-\d{2}|\d{2}-\d{2}-[A-Z]{2}|[A-Z]{2}-\d{2}-\d{2}"
    r"|[A-Z]{2}-[A-Z]{2}-\d{2}|\d{2}-[A-Z]{2}-[A-Z]{2}|\d{2}-[A-Z]{3}-\d|\d-[A-Z]{3}-\d{2}|[A-Z]{2}-\d{3}-[A-Z]"
    r"|[A-Z]-\d{3}-[A-Z]{2}|[A-Z]{3}-\d{2}-[A-Z]|\d-[A-Z]{2}-\d{3}|[A-Z]-\d{2}-[A-Z]{3}|\d{3}-[A-Z]{2}-\d)(?![\w-])"
)
NUMMER_RE = re.compile(
    r"(?i)\b(klant|contract|polis|dossier|zaak|patiënt|patient|cliënt|client|personeels|medewerker|"
    r"student|leerling|lid|lidmaatschaps|relatie|debiteur|verzekerden|abonnee|abonnements)"
    r"[ -]?(?:nummer|nr)\b\.?[ :]*((?=[A-Z0-9./-]*\d)[A-Z0-9](?:[A-Z0-9./-]{2,}[A-Z0-9]))"
)
BTW_RE = re.compile(r"(?<![\w])NL[ ]?\d{9}[ ]?B[ ]?\d{2}(?![\w])")
IP_RE = re.compile(r"(?<![\d.])(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)(?![\d]|\.\d)")


def _jaar(jj: str) -> str:
    if len(jj) == 4:
        return jj
    eeuw = 2000 if int(jj) <= date.today().year % 100 else 1900
    return str(eeuw + int(jj))


def datum_kandidaten(tekst: str) -> list[tuple[int, int, str, str]]:
    """Alle datums in de tekst als (start, eind, jaar, "JJJJ-MM-DD")."""
    kandidaten = []
    for m in DATUM_NUM_RE.finditer(tekst):
        d, mnd, j = int(m[1]), int(m[2]), _jaar(m[3])
        if 1 <= d <= 31 and 1 <= mnd <= 12:
            kandidaten.append((m.start(), m.end(), j, f"{j}-{mnd:02d}-{d:02d}"))
    for m in DATUM_ISO_RE.finditer(tekst):
        if 1 <= int(m[2]) <= 12 and 1 <= int(m[3]) <= 31:
            kandidaten.append((m.start(), m.end(), m[1], f"{m[1]}-{int(m[2]):02d}-{int(m[3]):02d}"))
    for m in DATUM_TXT_RE.finditer(tekst):
        if 1 <= int(m[1]) <= 31:
            mnd = MAANDEN[m[2].lower()]
            kandidaten.append((m.start(), m.end(), m[3], f"{m[3]}-{mnd:02d}-{int(m[1]):02d}"))
    for m in DATUM_WOORD_RE.finditer(tekst):   # "twaalf maart negentienhonderdvijfentachtig"
        dag = woord_naar_getal(m[1])
        # Het jaar mag uit meer woorden bestaan; woorden erna die er niet bij horen eraf halen.
        woorden = list(re.finditer(r"\S+", m[3]))
        jaar = None
        while woorden and jaar is None:
            jaar = jaar_in_woorden(" ".join(w[0] for w in woorden))
            if jaar is None:
                woorden.pop()
        eind = m.start(3) + (woorden[-1].end() if woorden else 0)
        if dag and dag <= 31 and jaar:
            mnd = MAANDEN[m[2].lower()]
            kandidaten.append((m.start(), eind, str(jaar), f"{jaar}-{mnd:02d}-{dag:02d}"))
    return sorted(kandidaten)


def vind_data(tekst: str, alle_datums: bool) -> tuple[list[Vondst], int]:
    """Geef vondsten voor datums terug, plus het aantal datums dat bleef staan."""
    vondsten, blijft, vorige_eind = [], 0, 0
    for start, eind, jaar, _ in datum_kandidaten(tekst):
        if start < vorige_eind:
            continue
        venster = tekst[max(vorige_eind, start - 60):start]
        aanduiding = list(GEBOORTE_RE.finditer(venster))
        if aanduiding:
            soort = "overlijdensdatum" if OVERLIJDEN_RE.match(aanduiding[-1][0]) else "geboortedatum"
            vondsten.append(Vondst(start, eind, soort, jaar, 5))
        elif alle_datums:
            vondsten.append(Vondst(start, eind, "datum", jaar, 5))
        else:
            blijft += 1
        vorige_eind = eind
    return vondsten, blijft


class Register:
    """Houdt vaste labels bij voor terugkerende waarden (rekeningnummers, tegenpartijen)."""

    def __init__(self):
        self.labels: dict[tuple[str, str], str] = {}
        self.teller: dict[str, int] = {}

    def label(self, soort: str, sleutel: str, voorvoegsel: str) -> str:
        k = (soort, sleutel)
        if k not in self.labels:
            self.teller[soort] = self.teller.get(soort, 0) + 1
            self.labels[k] = f"[{voorvoegsel} {self.teller[soort]}]"
        return self.labels[k]

    def masker(self, rekening: str) -> str:
        """Bankcode + laatste 4 cijfers, uniek per rekening.

        "NL91ABNA0417164300" -> "ABNA4300". Een buitenlandse IBAN zonder letters als
        bankcode krijgt de landcode ("DE3000"), een oud rekeningnummer zonder bankcode
        sterretjes ("****6789"). Hebben twee verschillende rekeningen dezelfde uitkomst,
        dan krijgt de tweede een volgnummer: "ABNA4300 (2)".
        """
        k = ("iban", rekening)
        if k not in self.labels:
            if re.fullmatch(r"[A-Z]{2}\d{2}[A-Z]{4}[A-Z0-9]+", rekening):
                basis = rekening[4:8] + rekening[-4:]
            elif re.fullmatch(r"[A-Z]{2}\d{2}[A-Z0-9]+", rekening):
                basis = rekening[:2] + rekening[-4:]
            else:
                basis = "*" * max(len(rekening) - 4, 4) + rekening[-4:]
            al = sum(1 for (soort, _), l in self.labels.items()
                     if soort == "iban" and l.split(" (")[0] == basis)
            self.labels[k] = basis if not al else f"{basis} ({al + 1})"
        return self.labels[k]


def vind_overig(tekst: str, register: Register, alle_datums: bool) -> tuple[list[Vondst], int]:
    vondsten: list[Vondst] = []

    # Prioriteit bij gelijke lengte: "BSN 123…" (11) > documentnummer na "paspoort" (10)
    # > los nummer dat door de elfproef komt (9). Een paspoortnummer van 9 cijfers kan
    # namelijk toevallig ook door de elfproef komen.
    for m in BSN_LABEL_RE.finditer(tekst):
        vondsten.append(Vondst(m.start(1), m.end(1), "bsn", "[BSN]", 11))
    for m in BSN_RE.finditer(tekst):
        if elfproef(re.sub(r"\D", "", m[1])):
            vondsten.append(Vondst(m.start(1), m.end(1), "bsn", "[BSN]", 9))

    for m in IBAN_RE.finditer(tekst):
        ruw = m[1]
        land = ruw[:2]
        # Bepaal het eind: precies de lengte van het land, anders inkorten tot geldig.
        posities = [i for i, c in enumerate(ruw) if not c.isspace()]
        lengtes = [IBAN_LENGTE[land]] if land in IBAN_LENGTE else range(len(posities), 14, -1)
        for n in lengtes:
            if n > len(posities):
                continue
            stuk = ruw[: posities[n - 1] + 1]
            sleutel = re.sub(r"\s", "", stuk)
            if iban_geldig(sleutel):
                vondsten.append(Vondst(m.start(1), m.start(1) + len(stuk), "iban",
                                       register.masker(sleutel), 9))
                break
    for m in REKENING_LABEL_RE.finditer(tekst):
        sleutel = re.sub(r"\D", "", m[1])
        vondsten.append(Vondst(m.start(1), m.end(1), "iban", register.masker(sleutel), 8))

    for m in EMAIL_RE.finditer(tekst):
        vondsten.append(Vondst(m.start(), m.end(), "email", "[e-mail]", 7))
    for m in TELEFOON_RE.finditer(tekst):
        if DATUM_NUM_RE.search(m[0]) or tekst[m.end():m.end() + 1] == ":":
            continue  # datum met tijd, zoals "05-01-2024 18:12"
        vondsten.append(Vondst(m.start(), m.end(), "telefoon", "[telefoon]", 6))

    for regex in (STRAAT_RE, POSTCODE_RE):
        for m in regex.finditer(tekst):
            vondsten.append(Vondst(m.start(), m.end(), "adres", "[adres]", 4))
    for m in ADRES_LABEL_RE.finditer(tekst):
        vondsten.append(Vondst(m.start(1), m.end(1), "adres", "[adres]", 3))
    for m in PLAATS_RE.finditer(tekst):
        soort = "geboorteplaats" if m[1].lower() == "geboren" else "woonplaats"
        vondsten.append(Vondst(m.start(2), m.end(2), "adres", f"[{soort}]", 3))
    for m in ADRES_RONDOM_POSTCODE_RE.finditer(tekst):
        vondsten.append(Vondst(m.start(), m.end(), "adres", "[adres]", 4))
    for m in WOONADRES_RE.finditer(tekst):
        for groep, label in ((1, "[adres]"), (2, "[adres]"), (3, "[woonplaats]")):
            if m[groep]:
                vondsten.append(Vondst(m.start(groep), m.end(groep), "adres", label, 4))
    for m in DOCUMENT_RE.finditer(tekst):
        vondsten.append(Vondst(m.start(1), m.end(1), "document", "[documentnummer]", 10))
    for m in KADASTER_RE.finditer(tekst):
        vondsten.append(Vondst(m.start(1), m.end(1), "adres", "[kadastrale aanduiding]", 4))
    for m in KENTEKEN_RE.finditer(tekst):
        vondsten.append(Vondst(m.start(), m.end(), "kenteken", "[kenteken]", 6))
    for m in NUMMER_RE.finditer(tekst):
        soort = m[1].lower().replace("patient", "patiënt").replace("client", "cliënt")
        vondsten.append(Vondst(m.start(2), m.end(2), "nummer", f"[{soort}nummer]", 8))
    for m in BTW_RE.finditer(tekst):
        vondsten.append(Vondst(m.start(), m.end(), "btw-nummer", "[btw-nummer]", 9))
    for m in IP_RE.finditer(tekst):
        vondsten.append(Vondst(m.start(), m.end(), "ip-adres", "[ip-adres]", 6))

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
    roepnaam: str | None = None       # uit --namen: "Johannes Petrus Jansen = Hans"


@dataclass
class Persoon:
    voornamen: list[str] = field(default_factory=list)
    initialen: str = ""
    achternaam: str | None = None
    positie: tuple[int, int] = (10**9, 0)
    label: str | None = ""            # None = niet vervangen (geen familielid)
    roepnaam: str | None = None
    opgegeven: bool = False           # staat in --namen/--familie
    teksten: list[str] = field(default_factory=list)   # hoe de naam in het document stond

    @property
    def alle_voornamen(self) -> list[str]:
        return self.voornamen + ([self.roepnaam] if self.roepnaam and self.roepnaam not in self.voornamen else [])


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
        # Elk naamdeel (behalve tussenvoegsels) begint met een hoofdletter; zo valt
        # "Jan de koopsom" af.
        if any(not w[0].isupper() for d in delen for w in d.split() if not _tv(w)):
            return False
        return all(len(d) >= 2 and not any(c.isdigit() for c in d) for d in delen)

    def vermeldingen(self, paginas: list[str], labels: bool = True,
                     tabelnamen: list[list[tuple[str, int, str | None]]] | None = None) -> list[Vermelding]:
        uit: list[Vermelding] = []

        def voeg_toe(tekst, pos, hint=None):
            v = ontleed_naam(tekst, pos, hint)
            if v and self._toegestaan(v):
                uit.append(v)

        for i, regel in enumerate(self.extra_namen):
            naam, _, roepnaam = regel.partition("=")
            v = ontleed_naam(naam, (-1, i))
            if v and self._toegestaan(v):
                v.roepnaam = roepnaam.strip() or None
                uit.append(v)
        for p, tekst in enumerate(paginas):
            for m in TITEL_NAAM_RE.finditer(tekst):
                # Na een titel is één woord een achternaam ("Dhr. Jansen").
                hint = "achternaam" if " " not in m[1].strip() else None
                voeg_toe(m[1], (p, m.start(1)), hint)
            for m in (LABEL_NAAM_RE.finditer(tekst) if labels else ()):
                soort = m[1].lower()
                hint = ("achternaam" if soort in ("achternaam", "geslachtsnaam", "geboortenaam")
                        else "voornaam" if soort in ("voornamen", "voornaam", "roepnaam") else None)
                voeg_toe(m[2], (p, m.start(2)), hint)
            if self.nlp is not None:
                for ent in self.nlp(tekst).ents:
                    if ent.label_ in ("PERSON", "PER"):
                        voeg_toe(ent.text, (p, ent.start_char))
            for naam, start, hint in (tabelnamen[p] if tabelnamen else ()):
                voeg_toe(naam, (p, start), hint)
        return uit


def koppel_personen(vermeldingen: list[Vermelding],
                    alleen_opgegeven: bool = False) -> tuple[list[Persoon], dict[str, str]]:
    """Voeg vermeldingen samen tot personen en ken labels toe.

    Geeft de personen terug plus een tabel achternaam -> familielabel voor
    achternamen die bij meerdere personen horen. Met alleen_opgegeven krijgen
    alleen personen uit de namen-/familielijst een label; de rest blijft staan.
    """
    personen: list[Persoon] = []

    def met_achternaam(a):
        return [p for p in personen if _norm(p.achternaam) == _norm(a)]

    def bijwerken(p: Persoon, v: Vermelding):
        if v.positie[0] == -1:
            p.opgegeven = True
        elif v.tekst not in p.teksten:
            p.teksten.append(v.tekst)
        p.positie = min(p.positie, v.positie)
        for vn in v.voornamen:
            if vn.lower() not in (x.lower() for x in p.voornamen):
                p.voornamen.append(vn)
        if v.initialen and len(v.initialen) > len(p.initialen):
            p.initialen = v.initialen
        if v.roepnaam:
            p.roepnaam = v.roepnaam

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
        return [p for p in personen if n.lower() in (x.lower() for x in p.alle_voornamen)]

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
        p = Persoon(voornamen=[woord], positie=v.positie, teksten=[woord])
        personen.append(p)

    return personen, ken_labels_toe(personen, alleen_opgegeven)


def ken_labels_toe(personen: list[Persoon], alleen_opgegeven: bool = False) -> dict[str, str]:
    """Label = roepnaam of eerste voornaam; zonder bekende voornaam "Persoon N".

    Twee verschillende personen met dezelfde voornaam: "Jan" en "Jan (2)".
    """
    gebruikt: dict[str, int] = {}
    zonder = 0
    # Wie dezelfde achternaam heeft als een opgegeven familielid, hoort er
    # waarschijnlijk ook bij; anders zou "Piet Jansen" de familienaam verraden.
    familienamen = {_norm(p.achternaam) for p in personen if p.opgegeven and p.achternaam}
    for p in sorted(personen, key=lambda p: p.positie):
        if alleen_opgegeven and not p.opgegeven and _norm(p.achternaam) not in familienamen:
            p.label = None
            continue
        basis = p.roepnaam or (p.voornamen[0] if p.voornamen else None)
        if basis:
            basis = _hoofdletter(basis) if basis.isupper() else basis
            gebruikt[basis.lower()] = gebruikt.get(basis.lower(), 0) + 1
            n = gebruikt[basis.lower()]
            p.label = basis if n == 1 else f"{basis} ({n})"
        else:
            zonder += 1
            p.label = f"Persoon {zonder}"

    families: dict[str, str] = {}
    letters = iter("ABCDEFGHIJKLMNOPQRSTUVWXYZ")
    for p in sorted(personen, key=lambda p: p.positie):
        a = _norm(p.achternaam)
        zelfde = [q for q in personen if a and _norm(q.achternaam) == a]
        if a not in families and len(zelfde) > 1 and any(q.label is not None for q in zelfde):
            families[a] = f"Familie {next(letters, '?')}"
    return families


def zoek_voornamen(personen: list[Persoon], teksten: list[str]) -> bool:
    """Vul een ontbrekende voornaam aan als die elders voluit staat.

    "HR J JANSEN" in een naamveld en "groet Jan Jansen" in een omschrijving: dan is
    de voornaam Jan en hoeft het script er niet naar te vragen.
    """
    tekst = "\n".join(teksten)
    gewijzigd = False
    for p in personen:
        if p.voornamen or p.roepnaam or not p.achternaam or not p.initialen:
            continue
        i = p.initialen[0]
        patroon = rf"{NW}({i}(?:[{KLEIN}]+|[{HOOFD}]+))[ ]+{_achternaam_patroon(p.achternaam)}{NA}"
        gevonden = {_hoofdletter(m[1]) for m in re.finditer(patroon, tekst) if not _tv(m[1])}
        if len(gevonden) == 1:
            p.voornamen = [gevonden.pop()]
            gewijzigd = True
    return gewijzigd


class Vrager:
    """Vraagt de gebruiker om een voornaam als die nergens in het document staat."""

    def __init__(self, invoer=input, onthoud: Path | None = None):
        self.invoer = invoer
        self.onthoud = onthoud            # antwoorden worden hier achteraan toegevoegd
        self.antwoorden: dict[tuple[str, str], str | None] = {}
        self.actief = True

    def vul_aan(self, personen: list[Persoon], bron: str, met_pagina: bool = True) -> bool:
        gewijzigd = False
        for p in sorted(personen, key=lambda p: p.positie):
            if p.voornamen or p.roepnaam or p.label is None or not p.achternaam:
                continue
            sleutel = (_norm(p.achternaam), p.initialen[:1])
            schoon = " ".join([f"{i}." for i in p.initialen] + [p.achternaam])
            if sleutel not in self.antwoorden:
                if not self.actief:
                    continue
                gezien = p.teksten[0] if p.teksten else schoon
                waar = f", pagina {p.positie[0] + 1}" if met_pagina and p.positie[0] >= 0 else ""
                try:
                    antwoord = self.invoer(
                        f"  ? Geen voornaam gevonden voor '{gezien}' ({bron}{waar}).\n"
                        f"    Voornaam (Enter = overslaan, - = niet meer vragen): ").strip()
                except EOFError:
                    antwoord = "-"
                if antwoord == "-":
                    self.actief = False
                    continue
                self.antwoorden[sleutel] = antwoord or None
                if antwoord and self.onthoud:
                    with self.onthoud.open("a", encoding="utf-8") as f:
                        f.write(f"{schoon} = {antwoord}\n")
            if self.antwoorden[sleutel]:
                p.roepnaam = self.antwoorden[sleutel]
                gewijzigd = True
        return gewijzigd


def _hoofdletter(t: str) -> str:
    """'JANSEN' -> 'Jansen', 'BAKKER-SMIT' -> 'Bakker-Smit'."""
    return "-".join(x[:1].upper() + x[1:].lower() for x in t.split("-"))


def _tok(t: str) -> str:
    if _tv(t):
        return f"(?i:{re.escape(t)})"
    # Akten en banken schrijven namen vaak in hoofdletters: JANSEN = Jansen.
    vormen = sorted({t, t.upper(), _hoofdletter(t)}, key=len, reverse=True)
    return "(?:" + "|".join(re.escape(v) for v in vormen) + ")"


def _achternaam_patroon(a: str) -> str:
    return r"[ ]+".join(_tok(t) for t in a.split())


def naam_patronen(personen: list[Persoon], families: dict[str, str],
                  vermeldingen: list[Vermelding]) -> list[tuple[re.Pattern, str]]:
    patronen: list[tuple[str, str]] = []
    voornaam_telling: dict[str, int] = {}
    for p in personen:
        for vn in p.alle_voornamen:
            voornaam_telling[vn.lower()] = voornaam_telling.get(vn.lower(), 0) + 1

    for p in personen:
        vn_alt = "|".join(_tok(v) for v in p.alle_voornamen)
        inits = {v[0].upper() for v in p.voornamen} | ({p.initialen[0]} if p.initialen else set())
        init_pat = (rf"(?:{'|'.join(sorted(inits))})(?:\.[ ]?|[ ])(?:[{HOOFD}](?:\.[ ]?|[ ])){{0,4}}"
                    if inits else None)
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
        for vn in p.alle_voornamen:
            # Een losse voornaam die bij meerdere personen hoort, blijft gewoon staan.
            if voornaam_telling[vn.lower()] == 1:
                patronen.append((_tok(vn), p.label))

    # Letterlijke vermeldingen als vangnet.
    for v in vermeldingen:
        if v.hint == "enkel" or not (v.voornamen or v.initialen):
            continue  # losse woorden en achternamen worden al door de patronen hierboven gedekt
        label = next((p.label for p in personen
                      if (v.achternaam and _norm(p.achternaam) == _norm(v.achternaam))
                      or (not v.achternaam and v.voornamen and v.voornamen[0].lower()
                          in (x.lower() for x in p.alle_voornamen))), None)
        tekst = re.sub(r"\s+", " ", v.tekst).strip(" ,.;:")
        if label and len(tekst) >= 2:
            patronen.append((r"[ ]+".join(re.escape(t) for t in tekst.split()), label))

    return [(re.compile(rf"{NW}(?:{pat}){NA}"), label) for pat, label in patronen]


def vind_namen(tekst: str, patronen: list[tuple[re.Pattern, str | None]]) -> list[Vondst]:
    uit = []
    for regex, label in patronen:
        for m in regex.finditer(tekst):
            if label is None:
                # Geen familielid: de naam blijft staan, maar "claimt" de plek zodat
                # een kortere familienaam er niet in gevonden wordt.
                uit.append(Vondst(m.start(), m.end(), "behouden", m[0], 2))
            else:
                uit.append(Vondst(m.start(), m.end(), "naam", label, 2))
    return uit


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

    def cel(rect: fitz.Rect | None) -> str:
        if rect is None:
            return ""
        delen, vorige = [], None
        for i in cel_tekens(pt, rect):
            if vorige is not None and pt.vakken[i][1] != vorige:
                delen.append(" ")
            vorige = pt.vakken[i][1]
            delen.append(teken(i))
        return re.sub(r"\s+", " ", "".join(delen)).strip().replace("|", "\\|")

    def tabel_md(tabel: Tabel) -> str:
        breedte = max(len(r) for r in tabel.rijen)
        rijen = [[cel(c) for c in r] + [""] * (breedte - len(r)) for r in tabel.rijen]
        regels = ["| " + " | ".join(rijen[0]) + " |", "|" + "---|" * breedte]
        regels += ["| " + " | ".join(r) + " |" for r in rijen[1:]]
        return "\n".join(regels)

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
                uit.append("\n" + tabel_md(pt.tabellen[t]) + "\n\n")
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
    rapport: Path | None = None          # controlerapport (zie controle.py)
    bevindingen: list = field(default_factory=list)
    gemarkeerd: bool = False             # lek gevonden: bestandsnaam bevat _CONTROLEREN
    ocr: list[int] = field(default_factory=list)   # pagina's gelezen met tekstherkenning
    tabellen: int = 0
    afbeeldingen: list[tuple[int, float]] = field(default_factory=list)   # (pagina, aandeel) niet gelezen


class Anonimiseerder:
    def __init__(self, namen: list[str] | None = None, behoud: set[str] | None = None,
                 gebruik_ner: bool = True, alle_datums: bool = False, alleen_familie: bool = False,
                 vrager: Vrager | None = None, zonder_afbeeldingen: bool = False,
                 familiegegevens=None, sleutel: bytes | None = None, rapport: bool = True,
                 ocr: bool = True):
        self.herkenner = NaamHerkenner(gebruik_ner, behoud or set(), namen or [])
        self.rapport = rapport
        self.ocr_taal = ocr_talen() if ocr else None
        self.familiegegevens = familiegegevens   # familie.Familiegegevens (BSN/adres per familielid)
        self.sleutel = sleutel
        self.alle_datums = alle_datums
        self.alleen_familie = alleen_familie
        self.vrager = vrager
        self.zonder_afbeeldingen = zonder_afbeeldingen

    def analyseer(self, paginas: list[str], bron: str = "tekst", extra: list[list[Vondst]] | None = None,
                  tabelnamen=None):
        """Vind alle vondsten per pagina (namen worden over het hele document gekoppeld)."""
        vermeldingen = self.herkenner.vermeldingen(paginas, tabelnamen=tabelnamen)
        personen, families = koppel_personen(vermeldingen, self.alleen_familie)
        gevonden = zoek_voornamen(personen, paginas)
        if gevonden | bool(self.vrager and self.vrager.vul_aan(personen, bron)):
            families = ken_labels_toe(personen, self.alleen_familie)
        patronen = naam_patronen(personen, families, vermeldingen)
        familie = self.familie_index(personen)
        register = Register()
        per_pagina, blijft = [], 0
        for tekst in paginas:
            overig, _ = vind_overig(tekst, register, self.alle_datums)
            eigen = familie.vind(tekst) if familie else []
            gekozen = kies_vondsten(overig + vind_namen(tekst, patronen) + eigen
                                    + (extra[len(per_pagina)] if extra else []))
            if eigen:
                from familie import verfijn
                verfijn(gekozen, eigen)
            per_pagina.append(gekozen)
            # Datums die door geen enkele vondst (geboorte, tabelkolom, familie) zijn vervangen.
            blijft += sum(1 for start, eind, _, _ in datum_kandidaten(tekst)
                          if not any(v.start < eind and start < v.eind for v in gekozen))
        return per_pagina, personen, patronen, register, blijft, familie

    def familie_index(self, personen: list[Persoon]):
        from familie import maak_index
        return maak_index(self.familiegegevens, self.sleutel, personen, self.herkenner.extra_namen)

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
        lezingen, scans, ocr_paginas, afbeeldingen = [], [], [], []
        for nr, page in enumerate(doc, 1):
            lezing = lees_pagina(page)
            if lijkt_scan(page, lezing.tekst):
                if self.ocr_taal:
                    tp = page.get_textpage_ocr(language=self.ocr_taal, dpi=300, full=True)
                    lezing = lees_pagina(page, tp)
                    lezing.ocr = True
                    ocr_paginas.append(nr)
                else:
                    scans.append(nr)
            else:
                tabellen = zoek_tabellen(page)
                if (groot := grote_afbeeldingen(page)) and not self.zonder_afbeeldingen:
                    # Een digitale pagina met een ingevoegde scan: alleen de afbeeldingen
                    # met OCR lezen en die tekst samenvoegen met de digitale tekst.
                    if self.ocr_taal:
                        tp = page.get_textpage_ocr(language=self.ocr_taal, dpi=300, full=False)
                        met_ocr = lees_pagina(page, tp)
                        if met_ocr.ocr_regels:
                            lezing = met_ocr
                            ocr_paginas.append(nr)
                    else:
                        afbeeldingen.append((nr, max(aandeel for _, aandeel in groot)))
                lezing.tabellen = tabellen
            lezingen.append(lezing)
        paginas = [l.tekst for l in lezingen]

        per_pagina, personen, patronen, register, blijft, familie = self.analyseer(
            paginas, invoer.name, extra=[tabel_vondsten(l) for l in lezingen],
            tabelnamen=[tabel_namen(l) for l in lezingen])
        uitvoer = [pagina_markdown(l, vs) for l, vs in zip(lezingen, per_pagina)]

        # Ook de bestandsnaam kan namen bevatten.
        stam = anonieme_bestandsnaam(invoer.stem, patronen, register, familie)
        uitmap.mkdir(parents=True, exist_ok=True)

        tellingen: dict[str, int] = {}
        for vs in per_pagina:
            for v in vs:
                if v.soort != "behouden":
                    tellingen[v.soort] = tellingen.get(v.soort, 0) + 1

        tekst_pad = pdf_pad = None
        if maak_tekst:
            tekst_pad = uitmap / f"{stam}_anoniem.md"
            delen = [f"# {stam} (geanonimiseerd)\n"]
            for i, tekst in enumerate(uitvoer, 1):
                schoon = re.sub(r"\n{3,}", "\n\n", tekst).strip()
                delen.append(f"## Pagina {i}\n\n{schoon}\n")
            tekst_pad.write_text("\n".join(delen), encoding="utf-8")

        lekken: list[tuple[int, str]] = []
        if maak_pdf:
            for page, pt, vs in zip(doc, lezingen, per_pagina):
                if vs:
                    redigeer_pagina(page, pt, vs)
                if self.zonder_afbeeldingen and (n := verwijder_afbeeldingen(page)):
                    tellingen["afbeelding"] = tellingen.get("afbeelding", 0) + n
            schoon_document(doc)
            pdf_pad = uitmap / f"{stam}_anoniem.pdf"
            doc.save(pdf_pad, garbage=4, deflate=True, clean=True)
            lekken = controleer(pdf_pad, paginas, per_pagina, ocr_paginas, self.ocr_taal)
        doc.close()

        if sleutelbestand:
            schrijf_sleutel(sleutelbestand, personen, register)

        import controle
        bevindingen = controle.controleer_uitvoer(
            [("bestandsnaam", stam)] + [(f"pagina {i}", t) for i, t in enumerate(uitvoer, 1)], personen, familie, self.herkenner.behoud, nlp=self.herkenner.nlp,
            namen_verdacht=not self.alleen_familie, datums_verdacht=not self.alle_datums)
        r = Resultaat(pdf_pad, tekst_pad, tellingen, blijft, scans, lekken, ocr=ocr_paginas,
                      tabellen=sum(len(l.tabellen) for l in lezingen), afbeeldingen=afbeeldingen)
        return controle.afronden(r, invoer, uitmap, bevindingen, self.rapport)


def anonieme_bestandsnaam(stam: str, patronen, register: Register, familie=None) -> str:
    """Ook een bestandsnaam kan een naam, rekeningnummer of (via de familielijst) een
    BSN, adres of geboortedatum van een familielid bevatten."""
    overig, _ = vind_overig(stam, register, alle_datums=False)
    eigen = familie.vind(stam) if familie else []
    vondsten = kies_vondsten(overig + vind_namen(stam, patronen) + eigen)
    if eigen:
        from familie import verfijn
        verfijn(vondsten, eigen)
    return re.sub(r"[^\w\- ]+", "", pas_toe(stam, vondsten)).strip() or "document"


def schrijf_sleutel(pad: Path, personen: list[Persoon], register: Register) -> None:
    sleutel = {
        "personen": {p.label: {"voornamen": p.voornamen, "roepnaam": p.roepnaam,
                               "initialen": p.initialen, "achternaam": p.achternaam}
                     for p in personen if p.label is not None},
        "rekeningen en tegenpartijen": {label: waarde for (_, waarde), label in register.labels.items()},
    }
    pad.write_text(json.dumps(sleutel, indent=2, ensure_ascii=False), encoding="utf-8")


def controleer(pdf_pad: Path, paginas: list[str], per_pagina: list[list[Vondst]],
               ocr_paginas: list[int] = (), ocr_taal: str | None = None) -> list[tuple[int, str]]:
    """Kijk of gevonden gegevens nog letterlijk in de nieuwe PDF staan.

    Een gescande pagina wordt daarvoor opnieuw met OCR gelezen: de gegevens zitten
    daar in de afbeelding, niet in de tekst.
    """
    lekken = []
    with fitz.open(pdf_pad) as doc:
        for i, (page, tekst, vs) in enumerate(zip(doc, paginas, per_pagina), 1):
            tp = page.get_textpage_ocr(language=ocr_taal, dpi=300, full=True) \
                if i in ocr_paginas and ocr_taal else None
            nieuw = re.sub(r"\s+", " ", page.get_text(textpage=tp))
            for v in vs:
                origineel = re.sub(r"\s+", " ", tekst[v.start:v.eind]).strip()
                if v.soort in ("datum", "geboortedatum", "overlijdensdatum", "behouden") or len(origineel) < 3:
                    continue
                if origineel.lower() == v.vervanging.lower():
                    continue   # een losse voornaam blijft zichzelf ("Jan" -> "Jan")
                if re.search(rf"{NW}{re.escape(origineel)}{NA}", nieuw):
                    lekken.append((i, v.soort))
    return lekken


ONDERSTEUND = {".pdf", ".csv", ".tsv", ".txt", ".xml", ".zip"}


def lees_lijst(pad: Path | None) -> list[str]:
    """Eén waarde per regel; commentaar (#) en ingesprongen regels (gegevens bij een
    familielid, zie familie.py) worden overgeslagen."""
    if not pad:
        return []
    return [r.strip() for r in pad.read_text(encoding="utf-8").splitlines()
            if r.strip() and not r.lstrip().startswith("#") and not r[:1].isspace()]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Anonimiseer PDF's en bankexports (namen, BSN, adressen, geboortedata, "
                    "rekeningnummers) zodat ze aan een AI gegeven kunnen worden.")
    ap.add_argument("invoer", nargs="*", type=Path,
                    help="PDF-bestand(en), bankexport(s) (.csv/.tsv/.txt), CAMT.053 (.xml/.zip) "
                         "of map(pen) daarmee")
    ap.add_argument("-o", "--uitvoer", type=Path, default=None,
                    help="uitvoermap (standaard: een map 'geanonimiseerd' naast elk bronbestand)")
    ap.add_argument("--namen", type=Path,
                    help="tekstbestand met namen die zeker vervangen moeten worden (één per regel); "
                         "met '= roepnaam' erachter wordt die roepnaam het label")
    ap.add_argument("--familie", type=Path,
                    help="tekstbestand met je familie- en gezinsleden; ALLEEN deze namen worden "
                         "vervangen (standaard: familie.txt in de huidige map, als die bestaat)")
    ap.add_argument("--familie-toevoegen", action="store_true",
                    help="voeg met vragen een familielid toe (naam, roepnaam, BSN, geboortedatum, adres) "
                         "aan familie.txt; BSN en adres worden daarna '[BSN Naam]' en '[adres Naam]'")
    ap.add_argument("--sleutel-naar-sleutelhanger", nargs="?", const=True, metavar="BESTAND",
                    help="zet de geheime sleutel in de sleutelhanger van je computer en wis het "
                         "sleutelbestand; met BESTAND: importeer een reservekopie")
    ap.add_argument("--sleutel-exporteren", type=Path, metavar="BESTAND",
                    help="maak een reservekopie van de geheime sleutel (bijv. op een usb-stick)")
    ap.add_argument("--alle-namen", action="store_true",
                    help="vervang alle namen, ook als er een familie.txt is")
    ap.add_argument("--niet-vragen", action="store_true",
                    help="niet om ontbrekende voornamen vragen")
    ap.add_argument("--zonder-afbeeldingen", action="store_true",
                    help="haal ook foto's, handtekeningen en logo's uit de PDF")
    ap.add_argument("--geen-ocr", action="store_true",
                    help="gescande pagina's niet met tekstherkenning (OCR) lezen")
    ap.add_argument("--geen-rapport", action="store_true",
                    help="geen controlerapport schrijven (de lekcheck en _CONTROLEREN blijven werken)")
    ap.add_argument("--behoud", type=Path,
                    help="tekstbestand met woorden/namen die NIET vervangen mogen worden (één per regel)")
    ap.add_argument("--alle-datums", action="store_true",
                    help="vervang álle datums door het jaartal, niet alleen geboortedata")
    ap.add_argument("--geen-ner", action="store_true",
                    help="gebruik geen spaCy-taalmodel voor naamherkenning (alleen patronen en --namen)")
    ap.add_argument("--alleen-tekst", action="store_true", help="maak alleen het Markdown-tekstbestand")
    ap.add_argument("--alleen-pdf", action="store_true", help="maak alleen de geanonimiseerde PDF")
    ap.add_argument("--alle-tegenpartijen", action="store_true",
                    help="bankexports: vervang ook namen van bedrijven/winkels (standaard alleen privépersonen)")
    ap.add_argument("--sleutel", action="store_true",
                    help="schrijf ook een sleutelbestand (pseudoniem -> echte gegevens). "
                         "Bevat persoonsgegevens: deel dit nooit met een AI!")
    args = ap.parse_args(argv)

    import familie as fam
    if args.sleutel_naar_sleutelhanger or args.sleutel_exporteren:
        try:
            if args.sleutel_exporteren:
                fam.exporteer_sleutel(args.sleutel_exporteren)
                print(f"Reservekopie gemaakt: {args.sleutel_exporteren}. Bewaar die veilig en "
                      "nooit naast familie.txt.")
            else:
                bron = None if args.sleutel_naar_sleutelhanger is True else Path(args.sleutel_naar_sleutelhanger)
                print(fam.naar_sleutelhanger(bron=bron))
        except RuntimeError as e:
            print(f"Niet gelukt: {e}", file=sys.stderr)
            return 1
        return 0
    if args.familie_toevoegen:
        doel = args.familie or Path("familie.txt")
        while fam.familielid_toevoegen(doel):
            if input("\nNog iemand toevoegen? (j/n) ").strip().lower() not in ("j", "ja", "y"):
                break
        return 0
    if not args.invoer:
        ap.error("geef één of meer bestanden of mappen op (of gebruik --familie-toevoegen)")

    bestanden: list[Path] = []
    for pad in args.invoer:
        if pad.is_dir():
            # .txt alleen als het bestand expliciet wordt genoemd (bijv. namen.txt niet meenemen)
            bestanden += sorted(p for p in pad.rglob("*") if p.suffix.lower() in ONDERSTEUND - {".txt"}
                                and "_anoniem" not in p.stem and "geanonimiseerd" not in p.parts)
        elif pad.suffix.lower() in ONDERSTEUND and pad.exists():
            bestanden.append(pad)
        else:
            print(f"Overgeslagen (geen PDF/bankexport of bestaat niet): {pad}", file=sys.stderr)
    if not bestanden:
        print("Geen PDF-bestanden of bankexports gevonden.", file=sys.stderr)
        return 1

    from bankdata import BankAnonimiseerder
    from camt import CamtAnonimiseerder

    familie = args.familie
    if familie is None and not args.alle_namen and Path("familie.txt").is_file():
        familie = Path("familie.txt")
    if args.alle_namen:
        familie = None
    if familie:
        print(f"Familielijst {familie}: alleen deze personen worden vervangen "
              "(--alle-namen om alle namen te vervangen).\n")
    namen = lees_lijst(args.namen) + lees_lijst(familie)
    sleutel = fam.geheime_sleutel()
    gegevens = fam.lees_familie(args.namen, sleutel)
    gegevens.samenvoegen(fam.lees_familie(familie, sleutel))
    if gegevens.heeft_afdrukken and not gegevens.sleutel_klopt:
        print(f"Let op: de BSN's/adressen in de familielijst zijn met een andere sleutel gemaakt dan "
              f"die in {fam.sleutel_locatie()}; ze worden niet herkend. Importeer de juiste sleutel met "
              "--sleutel-naar-sleutelhanger BESTAND of voeg ze opnieuw toe met --familie-toevoegen.\n")
        gegevens.per_regel = {}   # leesbare gegevens (telefoon, e-mail, …) blijven werken
    behoud = set(lees_lijst(args.behoud))
    vrager = None
    if not args.niet_vragen and sys.stdin.isatty():
        vrager = Vrager(onthoud=familie or args.namen)
    anon = bank = camt = None
    fouten = 0
    for i, pad in enumerate(bestanden, 1):
        uitmap = args.uitvoer or pad.parent / "geanonimiseerd"
        sleutel = uitmap / f"sleutel_{i}.json" if args.sleutel else None
        try:
            if pad.suffix.lower() == ".pdf":
                anon = anon or Anonimiseerder(namen, behoud, gebruik_ner=not args.geen_ner,
                                              alle_datums=args.alle_datums, alleen_familie=bool(familie),
                                              vrager=vrager, zonder_afbeeldingen=args.zonder_afbeeldingen,
                                              familiegegevens=gegevens, sleutel=sleutel,
                                              rapport=not args.geen_rapport, ocr=not args.geen_ocr)
                r = anon.verwerk(pad, uitmap, maak_pdf=not args.alleen_tekst,
                                 maak_tekst=not args.alleen_pdf, sleutelbestand=sleutel)
            elif pad.suffix.lower() in (".xml", ".zip"):
                camt = camt or CamtAnonimiseerder(namen, behoud, args.alle_tegenpartijen,
                                                  alleen_familie=bool(familie), vrager=vrager,
                                                  familiegegevens=gegevens, sleutel=sleutel,
                                                  rapport=not args.geen_rapport)
                r = camt.verwerk(pad, uitmap, sleutelbestand=sleutel)
            else:
                bank = bank or BankAnonimiseerder(namen, behoud, args.alle_tegenpartijen,
                                                  alleen_familie=bool(familie), vrager=vrager,
                                                  familiegegevens=gegevens, sleutel=sleutel,
                                                  rapport=not args.geen_rapport)
                r = bank.verwerk(pad, uitmap, sleutelbestand=sleutel)
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
            print(f"     ! {r.datums_blijven} datum(s) zonder geboorte-/overlijdenscontext niet aangepast "
                  "(gebruik --alle-datums om ook die te vervangen)")
        if r.ocr:
            print(f"     pagina('s) {', '.join(map(str, r.ocr))} gescand en gelezen met OCR "
                  "(tekstherkenning): extra goed nakijken")
        if r.tabellen:
            print(f"     {r.tabellen} tabel(len) als Markdown-tabel overgenomen")
        if r.scans:
            hoe = "Installeer Tesseract (zie README)" if args.geen_ocr is False and not ocr_talen() \
                else "Zet OCR aan (zonder --geen-ocr)"
            print(f"     ! pagina('s) {', '.join(map(str, r.scans))} lijken gescand (geen tekst): "
                  f"NIET geanonimiseerd. {hoe}.")
        lekken = sum(b.ernst == "lek" for b in r.bevindingen)
        verdacht = sum(b.ernst == "verdacht" for b in r.bevindingen)
        if r.gemarkeerd:
            print(f"     !! {lekken} lek(ken) gevonden: bestand gemarkeerd met _CONTROLEREN. NIET delen "
                  "voordat je het rapport hebt bekeken.")
        if verdacht:
            print(f"     ?  {verdacht} verdachte plek(ken) om even na te kijken")
        if r.rapport:
            print(f"     controlerapport: {r.rapport}")
        if sleutel:
            print(f"     sleutel: {sleutel} (bevat persoonsgegevens, niet delen!)")
    print("\nControleer het resultaat altijd zelf voordat je het met een AI deelt.")
    return 1 if fouten else 0


if __name__ == "__main__":
    sys.exit(main())
