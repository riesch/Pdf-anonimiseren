"""Persoonsgegevens herkennen met patronen: BSN, IBAN, telefoon, e-mail, datums, adressen,
kentekens, documentnummers, ... Elke vondst wordt een Vondst met een vervanging."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

# ---------------------------------------------------------------------------
# Bouwstenen voor reguliere expressies
# ---------------------------------------------------------------------------

HOOFD = "A-ZÀ-ÖØ-Þ"
KLEIN = "a-zß-öø-ÿ"
# Eén naamdeel: "Jan", "Anne-Marie", "Bakker-Smit", "JANSEN", "O'Brien"
INITIALEN = rf"(?:[{HOOFD}]\.[ ]?){{1,5}}"
TUSSENVOEGSELS = [
    "van", "de", "der", "den", "het", "ten", "ter", "te", "'t", "von", "zu",
    "vd", "v.d.", "la", "le", "du", "da", "di", "del", "des", "dos", "el",
]
TV = "(?:" + "|".join(re.escape(t) for t in sorted(TUSSENVOEGSELS, key=len, reverse=True)) + ")"
TV_I = rf"(?i:{TV})"
# Eén naamdeel; ook dubbele achternamen: "Bakker-Smit", "Jansen-de Vries", "Smit-van der Berg"
CAP = (rf"[{HOOFD}](?:[{KLEIN}'’]+|[{HOOFD}'’]+)"
       rf"(?:-(?:{TV_I}[ ])*[{HOOFD}][{KLEIN}{HOOFD}'’]+|-{TV_I}(?:[ ]{TV_I})*[ ][{HOOFD}][{KLEIN}{HOOFD}'’]+)*")
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
IBAN_RE = re.compile(r"(?<![A-Za-z0-9])(?=([A-Za-z]{2}\d{2}(?:[ \t]*\n?[ \t]*[A-Za-z0-9]){10,30}))")
REKENING_LABEL_RE = re.compile(
    r"(?i)\b(?:rekening(?:nummer)?|bankrekening(?:nummer)?|reknr|rek\.?[ ]?nr|girorekening|giro)"
    r"\b\.?[ \t]*[:.]?[ \t]*(\d{1,3}(?:[ .]?\n?[ \t]*\d){5,9})(?!\d)"
)
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
TELEFOON_RE = re.compile(
    # Nederlands: 06-12345678, 030-1234567, +31 (0)6 12345678
    r"(?<![\w+])(?:(?:\+|00)31[ \t-]?(?:\(0\)[ \t-]?)?|0)[1-9](?:[ \t-]?\d){8}(?!\d)"
    # buitenlands, altijd met + of 00 en landcode: +32 470 12 34 56, 0049 151 23456789
    r"|(?<![\w+])(?:\+|00)(?!31)[1-9]\d{0,2}[ \t-]?(?:\(0\)[ \t-]?)?\d(?:[ \t-]?\d){6,11}(?!\d)"
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


# Nederlandse kentekens (alle sidecodes), met streepjes of spaties: 12-ABC-3, 12 ABC 3.
_SIDECODES = ["XX-99-XX", "99-XX-99", "99-99-XX", "XX-99-99", "XX-XX-99", "99-XX-XX", "99-XXX-9",
              "9-XXX-99", "XX-999-X", "X-999-XX", "XXX-99-X", "9-XX-999", "X-99-XXX", "999-XX-9"]
KENTEKEN_RE = re.compile(
    r"(?<![\w-])(?:" + "|".join(
        "[- ]".join("[A-Z]{%d}" % len(g) if g[0] == "X" else "\\d{%d}" % len(g) for g in code.split("-"))
        for code in _SIDECODES) + r")(?![\w-])"
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
        land = ruw[:2].upper()   # ook "nl91abna…" in kleine letters
        # Bepaal het eind: precies de lengte van het land, anders inkorten tot geldig.
        posities = [i for i, c in enumerate(ruw) if not c.isspace()]
        lengtes = [IBAN_LENGTE[land]] if land in IBAN_LENGTE else range(len(posities), 14, -1)
        for n in lengtes:
            if n > len(posities):
                continue
            stuk = ruw[: posities[n - 1] + 1]
            sleutel = re.sub(r"\s", "", stuk).upper()
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
