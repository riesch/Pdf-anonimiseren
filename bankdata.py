"""Anonimiseer bankexports (CSV/TXT van o.a. ING, Rabobank, ABN AMRO, ASN/SNS, Triodos, bunq, Knab).

Anders dan bij PDF's blijven datums en bedragen hier staan: die zijn juist nodig om
uitgaven te analyseren. Wat wordt vervangen:

  * eigen rekeningnummers      -> "[eigen rekening 1]"
  * andere rekeningnummers     -> "[IBAN 1]", "[IBAN 2]", ... (vast per rekening)
  * namen van privépersonen    -> "Persoon 1", ... (ook in omschrijvingen)
  * kenmerken/referenties      -> "[kenmerk]"
  * pasnummers                 -> "[pasnummer]"
  * klant-/polis-/contractnrs  -> "[klantnummer]"
  * BSN, adressen, e-mail, telefoon in omschrijvingen (zoals bij PDF's)

Namen van bedrijven (winkels, energieleverancier, gemeente, ...) blijven staan, zodat
een AI nog kan zien waar het geld heen gaat. Met --alle-tegenpartijen worden ook die
vervangen.
"""

from __future__ import annotations

import csv
import io
import re
from pathlib import Path

import anonimiseer as an

BANK_EXTENSIES = {".csv", ".tsv", ".txt"}

BANK_TITELS = {"hr", "heer", "dhr", "mw", "mvr", "mevr", "mevrouw", "mr", "fam", "familie",
               "drs", "dr", "ir", "ing", "de heer"}
BANK_ACHTERVOEGSELS = {"eo", "e/o", "e.o.", "cj", "c.j.", "en/of", "en", "of"}
SPLITS_RE = re.compile(r"(?i)\s+(?:en/of|en|of|&)\s+")

BEDRIJF_RE = re.compile(
    r"(?i)(?<![\w])(?:b\.?v\.?|n\.?v\.?|v\.?o\.?f\.?|c\.v\.|s\.a\.|stichting|vereniging|gemeente|provincie|"
    r"belastingdienst|waterschap|\w*bank|\w*verzeker\w*|\w*zorg\w*|holding|groep|group|international|"
    r"nederland|benelux|europe|ltd|llc|gmbh|inc|coop\w*|cjib|duo|svb|uwv|\w*energie|klantenservice|"
    r"service\w*|webshop|\w*shop|store|winkel|apotheek|\w*praktijk|kliniek|ziekenhuis|\w*school|"
    r"universiteit|hogeschool|club|sport\w*|\w*fonds|ministerie|rijksoverheid|parkeer\w*|parking|"
    r"tankstation|restaurant|cafe|café|hotel|bakkerij|slagerij|supermarkt|\w*markt|pay|payments|paypal|"
    r"adyen|mollie|tikkie|incasso|ccv|sumup|stripe|klarna|afterpay|ideal|kpn|ziggo|odido|vodafone|"
    r"eneco|vattenfall|essent|albert heijn|jumbo|lidl|aldi|plus|hema|kruidvat|etos|action|ikea|praxis|"
    r"gamma|blokker|zeeman|primark|mediamarkt|coolblue|thuisbezorgd|uber|booking|airbnb|bol\.com|"
    r"amazon|apple|google|microsoft|netflix|spotify|disney|videoland|ns|ov-chipkaart|shell|bp|esso|"
    r"tinq|total|vitens|evides|brabant water|dunea|pwn|anwb|nuon|greenchoice|budget energie|"
    r"woningcorporatie|wonen|woonstichting|makelaar\w*|notaris\w*|advoca\w*|accountant\w*|"
    r"huisarts\w*|tandarts\w*|fysio\w*|kinderopvang|bso|ggd|politie|rdw)(?![\w])"
    r"|\d|\*|\.(?:com|nl|eu|de|org)(?![\w])|&"
)

KENMERK_KOLOMMEN = {"machtigingskenmerk", "betalingskenmerk", "transactiereferentie", "kenmerk",
                    "end-to-end id", "end to end id", "endtoendid", "referentie", "machtiging id",
                    "batch id", "mandaat id", "mandate reference"}
EIGEN_KOLOMMEN = {"rekening", "iban/bban", "iban", "rekeningnummer", "account", "accountnummer",
                  "eigen rekening", "rekening iban", "account number", "rekening (iban)"}
TEGEN_KOLOMMEN = {"tegenrekening", "tegenrekening iban/bban", "tegenrekening iban", "iban tegenpartij",
                  "tegenrekeningnummer", "counterparty iban", "counterparty account", "tegenrekening (iban)"}
NAAM_KOLOMMEN = {"naam / omschrijving", "naam/omschrijving", "naam tegenpartij", "naam uiteindelijke partij",
                 "naam initiërende partij", "naam initierende partij", "tegenpartij", "naam tegenrekening",
                 "counterparty", "counterparty name", "naam", "name", "naam begunstigde", "begunstigde"}
VRIJ_KOLOMMEN = {"mededelingen", "omschrijving", "omschrijving-1", "omschrijving-2", "omschrijving-3",
                 "description", "notities", "notes", "beschrijving", "tag", "mededeling"}

# Namen in vrije tekst: "Naam: J JANSEN  Omschrijving: ..." en "/NAME/J JANSEN/".
NAAM_IN_TEKST_RE = re.compile(
    r"(?i)(?:\bnaam:[ ]*|/name/)(.+?)(?=[ ]{2,}|/|\s+(?:omschrijving|iban|bic|kenmerk|machtiging|"
    r"incassant|datum|pas|term|valutadatum|transactie)\b|$)"
)
TIKKIE_RE = re.compile(r"(?i)\bvan[ ]+([A-Za-zÀ-ÿ.' -]+?),")
PAS_RE = re.compile(r"(?i)\b(?:pas(?:nr|nummer|volgnr)?|kaart(?:nr|nummer)?|card)(?![a-z])\.?[ :]*(\d{2,})")
KLANTNR_RE = re.compile(
    r"(?i)\b(?:klant|contract|polis|dossier|abonnee|abonnements|lid|lidmaatschaps|relatie|debiteur|"
    r"verzekerde|leerling|cli[eë]nt)(?:nr|nummer|-?nr\.?)\b\.?[ :]*([A-Z0-9][A-Z0-9-]{3,})"
)
GETAL_OF_DATUM_RE = re.compile(r"^[\s+\-−]*[\d.,:/ -]*\d[\d.,:/ -]*$|^\s*$")


def _netjes(t: str) -> str:
    """'JANSEN' -> 'Jansen', 'VAN' -> 'van', 'BAKKER-SMIT' -> 'Bakker-Smit'."""
    if an._tv(t):
        return t.lower()
    if t.isupper() or t.islower():
        return "-".join(x.capitalize() for x in t.split("-"))
    return t


def _is_initiaal(t: str) -> bool:
    letters = t.replace(".", "")
    return bool(re.fullmatch(r"[A-Za-z]{1,4}", letters)) and (len(letters) == 1 or "." in t)


def is_persoon(naam: str) -> bool:
    s = naam.strip()
    if not s or BEDRIJF_RE.search(s):
        return False
    tokens = s.split()
    if not all(re.fullmatch(r"[A-Za-zÀ-ÿ'’./-]+", t) for t in tokens):
        return False
    if tokens[0].lower().rstrip(".") in BANK_TITELS:
        return True
    return 2 <= len(tokens) <= 8


def ontleed_banknaam(s: str, positie=(0, 0)) -> list[an.Vermelding]:
    """Ontleed namen zoals banken ze schrijven: 'HR J JANSEN EO', 'JANSEN J', 'J.P. van der Berg'."""
    uit = []
    for deel in SPLITS_RE.split(re.sub(r"\s+", " ", s).strip(" ,;")):
        tokens = deel.replace(",", " ").split()
        while tokens and (tokens[0].lower().rstrip(".") in BANK_TITELS
                          or (tokens[0].lower() == "de" and len(tokens) > 1 and tokens[1].lower() == "heer")):
            tokens.pop(0)
        while tokens and tokens[-1].lower() in BANK_ACHTERVOEGSELS:
            tokens.pop()
        if not tokens:
            continue
        initialen = ""
        # Woorden vóór initialen horen niet bij de naam ("Spaarrekening J. Jansen").
        eerste_init = next((i for i, t in enumerate(tokens[:-1]) if _is_initiaal(t)), 0)
        tokens = tokens[eerste_init:]
        while len(tokens) > 1 and _is_initiaal(tokens[-1]) and not _is_initiaal(tokens[0]):
            initialen = tokens.pop().replace(".", "").upper() + initialen
        if not initialen:
            while len(tokens) > 1 and _is_initiaal(tokens[0]):
                initialen += tokens.pop(0).replace(".", "").upper()
        tokens = [_netjes(t) for t in tokens]
        if initialen or len(tokens) == 1:
            voornamen, achter = [], " ".join(tokens)
        else:
            i = next((k for k, t in enumerate(tokens[:-1]) if an._tv(t) and k > 0), len(tokens) - 1)
            voornamen, achter = tokens[:i], " ".join(tokens[i:])
        if len(achter.replace(" ", "")) >= 2:
            uit.append(an.Vermelding(deel, voornamen, initialen, achter, positie))
    return uit


def _kop(t: str) -> str:
    return re.sub(r"\s+", " ", t.strip().strip('"').lower())


def bepaal_rollen(rijen: list[list[str]]) -> tuple[list[str], bool]:
    """Geef per kolom een rol en of de eerste rij een kopregel is."""
    eerste = rijen[0] if rijen else []
    bekend = EIGEN_KOLOMMEN | TEGEN_KOLOMMEN | NAAM_KOLOMMEN | VRIJ_KOLOMMEN | KENMERK_KOLOMMEN
    heeft_kop = any(_kop(c) in bekend or re.search(r"datum|bedrag|saldo|munt|date|amount", _kop(c))
                    for c in eerste) and not any(an.iban_geldig(c) for c in eerste)
    breedte = max((len(r) for r in rijen), default=0)

    rollen = []
    for k in range(breedte):
        kop = _kop(eerste[k]) if heeft_kop and k < len(eerste) else ""
        if kop in TEGEN_KOLOMMEN or "tegenrekening" in kop or "counterparty iban" in kop:
            rol = "tegenrekening"
        elif kop in EIGEN_KOLOMMEN:
            rol = "eigen"
        elif kop in KENMERK_KOLOMMEN or "kenmerk" in kop or "referentie" in kop:
            rol = "kenmerk"
        elif kop in NAAM_KOLOMMEN or (("naam" in kop or "name" in kop) and "omschrijving" not in kop):
            rol = "naam"
        elif kop in VRIJ_KOLOMMEN or re.search(r"omschrijving|mededeling|description", kop):
            rol = "vrij"
        elif kop:
            rol = "behoud" if re.search(r"datum|date|bedrag|amount|saldo|balance|munt|currency|bic|"
                                        r"code|volgnr|af bij|mutatiesoort|incassant", kop) else "auto"
        else:
            rol = "auto"
        rollen.append(rol)

    if not heeft_kop:
        # Zonder kopregel (zoals de TXT-export van ABN AMRO): een kolom die op elke
        # rij hetzelfde rekeningnummer bevat, is de eigen rekening.
        for k in range(breedte):
            waarden = {r[k].replace(" ", "").upper() for r in rijen if k < len(r) and r[k].strip()}
            waarde = next(iter(waarden), "")
            if (len(waarden) == 1 and re.fullmatch(r"[A-Z]{2}\d{2}[A-Z0-9]{10,30}|\d{7,10}", waarde)
                    and not re.fullmatch(r"(?:19|20)\d{6}", waarde)):   # geen datum als 20240110
                rollen[k] = "eigen"
                break
    return rollen, heeft_kop


def lees_csv(pad: Path):
    ruw = pad.read_bytes()
    for codering in ("utf-8-sig", "cp1252"):
        try:
            tekst = ruw.decode(codering)
            break
        except UnicodeDecodeError:
            continue
    proef = tekst[:20000]
    eerste_regel = proef.split("\n", 1)[0]
    # Tab en puntkomma gaan voor: komma's staan in Nederlandse exports vaak in bedragen.
    scheiding = next((t for t in "\t;" if t in eerste_regel), None)
    if scheiding is None:
        try:
            scheiding = csv.Sniffer().sniff(proef, delimiters=",;\t|").delimiter
        except csv.Error:
            scheiding = ","
    rijen = list(csv.reader(io.StringIO(tekst), delimiter=scheiding))
    alles_gequote = proef.lstrip().startswith('"')
    return rijen, scheiding, alles_gequote


def _sleutel(rekening: str) -> str:
    return rekening.replace(" ", "").replace(".", "").upper()


def _is_rekening(waarde: str) -> bool:
    s = _sleutel(waarde)
    # Ook zonder geldige controlesom: in een rekeningkolom staat altijd een rekening.
    return bool(re.fullmatch(r"[A-Z]{2}\d{2}[A-Z0-9]{10,30}|P?\d{3,10}", s))


class BankAnonimiseerder:
    def __init__(self, namen: list[str] | None = None, behoud: set[str] | None = None,
                 alle_tegenpartijen: bool = False):
        self.herkenner = an.NaamHerkenner(False, behoud or set(), namen or [])
        self.behoud = {an._norm(b) for b in (behoud or set())}
        self.alle_tegenpartijen = alle_tegenpartijen

    def _persoon(self, naam: str) -> bool:
        return an._norm(naam) not in self.behoud and is_persoon(naam)

    def _namen_in_tekst(self, tekst: str):
        for regex in (NAAM_IN_TEKST_RE, TIKKIE_RE):
            for m in regex.finditer(tekst):
                if regex is TIKKIE_RE and "tikkie" not in tekst.lower():
                    continue
                waarde = m[1].strip()
                if self._persoon(waarde):
                    yield m.start(1), m.start(1) + len(waarde), waarde

    def verwerk(self, invoer: Path, uitmap: Path, sleutelbestand: Path | None = None) -> an.Resultaat:
        rijen, scheiding, alles_gequote = lees_csv(invoer)
        if not rijen:
            raise ValueError("leeg bestand")
        rollen, heeft_kop = bepaal_rollen(rijen)
        data = rijen[1:] if heeft_kop else rijen

        def cellen(rol):
            for r, rij in enumerate(data):
                for k, cel in enumerate(rij):
                    if k < len(rollen) and rollen[k] == rol and cel.strip():
                        yield r, k, cel

        # Ronde 1: rekeningen en namen verzamelen.
        register = an.Register()
        eigen = []
        for _, _, cel in cellen("eigen"):
            if _sleutel(cel) not in eigen:
                eigen.append(_sleutel(cel))
        for i, s in enumerate(eigen, 1):
            register.labels[("iban", s)] = f"[eigen rekening {i}]"

        # Alleen titels ("Dhr. ...") en --namen; "Naam: ..." wordt hieronder per geval
        # beoordeeld, omdat daar vaak een bedrijfsnaam staat.
        vermeldingen = self.herkenner.vermeldingen(["\n".join(c for _, _, c in cellen(rol))
                                                    for rol in ("vrij", "auto")], labels=False)
        for r, k, cel in cellen("naam"):
            if self._persoon(cel):
                vermeldingen += ontleed_banknaam(cel, (r, k))
        for rol in ("vrij", "auto"):
            for r, k, cel in cellen(rol):
                for _, _, waarde in self._namen_in_tekst(cel):
                    vermeldingen += ontleed_banknaam(waarde, (r, k))
        personen, families = an.koppel_personen(vermeldingen)
        patronen = an.naam_patronen(personen, families, vermeldingen)

        def naam_label(waarde: str) -> str:
            vondsten = an.kies_vondsten(an.vind_namen(waarde, patronen))
            return an.pas_toe(waarde, vondsten) if vondsten else "[naam]"

        tellingen: dict[str, int] = {}

        def tel(soort, n=1):
            tellingen[soort] = tellingen.get(soort, 0) + n

        def vrije_tekst(tekst: str) -> str:
            vondsten, _ = an.vind_overig(tekst, register, alle_datums=False)
            vondsten = [v for v in vondsten if v.soort not in ("datum", "geboortedatum")]
            vondsten += an.vind_namen(tekst, patronen)
            vondsten += [an.Vondst(s, e, "naam", naam_label(w), 3) for s, e, w in self._namen_in_tekst(tekst)]
            vondsten += [an.Vondst(m.start(1), m.end(1), "pasnummer", "[pasnummer]", 8)
                         for m in PAS_RE.finditer(tekst)]
            vondsten += [an.Vondst(m.start(1), m.end(1), "klantnummer", "[klantnummer]", 8)
                         for m in KLANTNR_RE.finditer(tekst)]
            gekozen = an.kies_vondsten(vondsten)
            for v in gekozen:
                tel(v.soort)
            return an.pas_toe(tekst, gekozen)

        # Ronde 2: elke cel volgens de rol van de kolom vervangen.
        uit = [list(r) for r in rijen]
        start = 1 if heeft_kop else 0
        for r, rij in enumerate(uit[start:], start):
            for k, cel in enumerate(rij):
                rol = rollen[k] if k < len(rollen) else "auto"
                if not cel.strip() or rol == "behoud":
                    continue
                if rol in ("eigen", "tegenrekening") and _is_rekening(cel):
                    rij[k] = register.label("iban", _sleutel(cel), "IBAN")
                    tel("iban")
                elif rol == "kenmerk":
                    rij[k] = "[kenmerk]"
                    tel("kenmerk")
                elif rol == "naam":
                    if self._persoon(cel):
                        rij[k] = naam_label(cel)
                        tel("naam")
                    elif self.alle_tegenpartijen:
                        rij[k] = register.label("tegenpartij", an._norm(cel), "tegenpartij")
                        tel("tegenpartij")
                    else:
                        rij[k] = vrije_tekst(cel)
                elif rol == "auto" and an.iban_geldig(_sleutel(cel)):
                    rij[k] = register.label("iban", _sleutel(cel), "IBAN")
                    tel("iban")
                elif rol == "vrij" or (rol == "auto" and not GETAL_OF_DATUM_RE.match(cel)):
                    rij[k] = vrije_tekst(cel)

        uitmap.mkdir(parents=True, exist_ok=True)
        stam = an.anonieme_bestandsnaam(invoer.stem, patronen, register)
        uitpad = uitmap / f"{stam}_anoniem{invoer.suffix.lower()}"
        with uitpad.open("w", encoding="utf-8", newline="") as f:
            schrijver = csv.writer(f, delimiter=scheiding, lineterminator="\n",
                                   quoting=csv.QUOTE_ALL if alles_gequote else csv.QUOTE_MINIMAL)
            schrijver.writerows(uit)

        lekken = controleer_bank(uitpad, register, personen)

        if sleutelbestand:
            an.schrijf_sleutel(sleutelbestand, personen, register)
        return an.Resultaat(None, uitpad, tellingen, 0, [], lekken)


def controleer_bank(pad: Path, register: an.Register, personen: list[an.Persoon]) -> list[tuple[int, str]]:
    """Kijk of rekeningnummers of achternamen nog letterlijk in de uitvoer staan."""
    tekst = pad.read_text(encoding="utf-8")
    compact = re.sub(r"[\s.]", "", tekst).upper()
    lekken = []
    for (soort, sleutel), _ in register.labels.items():
        if soort == "iban" and len(sleutel) >= 7 and sleutel in compact:
            lekken.append((0, "rekeningnummer"))
    for p in personen:
        a = (p.achternaam or "").split()
        if a and len(a[-1]) >= 3 and re.search(rf"(?i){an.NW}{re.escape(a[-1])}{an.NA}", tekst):
            lekken.append((0, "naam"))
    return lekken
