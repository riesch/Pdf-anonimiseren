"""Persoonsgegevens van familieleden herkennen en vervangen door een label met hun naam.

In familie.txt kun je onder een naam ook BSN, geboortedatum en adres opslaan:

    Richard Pieter Jansen = Richard
        bsn: 4f1c0e...            # vingerafdruk, niet het BSN zelf
        adres: 9b2c7a...          # straat + huisnummer

Een BSN van Richard wordt dan "[BSN Richard]", zijn adres "[adres Richard]" en zijn
geboortedatum het geboortejaar (ook zonder "geboren" ervoor, bijv. in een tabel).

De gegevens staan er niet leesbaar in, maar als vingerafdruk (HMAC-SHA256) met een
geheime sleutel. Die sleutel staat in de sleutelhanger van je besturingssysteem
(Sleutelhanger op macOS, Referentiebeheer op Windows, Secret Service op Linux) of,
als die er niet is, in ~/.pdf-anonimiseren/sleutel. Wie alleen familie.txt in handen
krijgt, kan de gegevens er niet uit terughalen.

Toevoegen gaat met vragen:  python anonimiseer.py --familie-toevoegen
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
from dataclasses import dataclass, field
from pathlib import Path

import anonimiseer as an

SLEUTEL_PAD = Path.home() / ".pdf-anonimiseren" / "sleutel"
DIENST, ACCOUNT = "pdf-anonimiseren", "familie-sleutel"   # naam in de sleutelhanger
SYSTEEM = "systeem"
CONTROLE_RE = re.compile(r"#\s*sleutel-controle:\s*([0-9a-f]+)")
GEGEVEN_RE = re.compile(r"^\s+(bsn|geboortedatum|adres)\s*:\s*([0-9a-f]{16,64})", re.I)


# ---------------------------------------------------------------------------
# Sleutel en vingerafdrukken
# ---------------------------------------------------------------------------

def systeem_sleutelhanger():
    """De sleutelhanger van het besturingssysteem, of None als die er niet is."""
    try:
        import keyring
    except ImportError:
        return None
    kr = keyring.get_keyring()
    return kr if getattr(kr, "priority", 0) > 0 else None


def _sleutelhanger(sleutelhanger):
    return systeem_sleutelhanger() if sleutelhanger == SYSTEEM else sleutelhanger


def _schrijf_bestand(pad: Path, sleutel: bytes) -> None:
    pad.parent.mkdir(parents=True, exist_ok=True)
    pad.write_text(sleutel.hex())
    try:
        os.chmod(pad, 0o600)
    except OSError:  # pragma: no cover - bijv. Windows
        pass


def geheime_sleutel(maak: bool = False, pad: Path = SLEUTEL_PAD, sleutelhanger=SYSTEEM) -> bytes | None:
    """Haal de sleutel uit de sleutelhanger, anders uit het bestand; maak er zo nodig een.

    Een nieuwe sleutel gaat in de sleutelhanger als die er is, anders in het bestand.
    """
    kr = _sleutelhanger(sleutelhanger)
    if kr is not None and (opgeslagen := kr.get_password(DIENST, ACCOUNT)):
        return bytes.fromhex(opgeslagen)
    if pad.is_file():
        return bytes.fromhex(pad.read_text().strip())
    if not maak:
        return None
    sleutel = secrets.token_bytes(32)
    if kr is not None:
        kr.set_password(DIENST, ACCOUNT, sleutel.hex())
    else:
        _schrijf_bestand(pad, sleutel)
    return sleutel


def sleutel_locatie(pad: Path = SLEUTEL_PAD, sleutelhanger=SYSTEEM) -> str:
    kr = _sleutelhanger(sleutelhanger)
    if kr is not None and kr.get_password(DIENST, ACCOUNT):
        return f"de sleutelhanger ({DIENST})"
    return str(pad)


def naar_sleutelhanger(pad: Path = SLEUTEL_PAD, sleutelhanger=SYSTEEM, bron: Path | None = None) -> str:
    """Zet de sleutel uit een bestand in de sleutelhanger en wis het bestand (tenzij het een
    reservekopie is die met bron is opgegeven)."""
    kr = _sleutelhanger(sleutelhanger)
    if kr is None:
        raise RuntimeError("geen sleutelhanger gevonden. Installeer 'keyring' (pip install keyring); "
                           "op Linux is daarnaast een sleutelbos nodig, zoals GNOME Keyring of KWallet")
    bestand = bron or pad
    if not bestand.is_file():
        raise RuntimeError(f"geen sleutelbestand gevonden: {bestand}")
    sleutel = bestand.read_text().strip()
    bytes.fromhex(sleutel)   # controle: is het echt een sleutel?
    bestaand = kr.get_password(DIENST, ACCOUNT)
    if bestaand and bestaand != sleutel:
        raise RuntimeError("er staat al een andere sleutel in de sleutelhanger; niets gewijzigd")
    kr.set_password(DIENST, ACCOUNT, sleutel)
    if kr.get_password(DIENST, ACCOUNT) != sleutel:
        raise RuntimeError("opslaan in de sleutelhanger is mislukt; het bestand blijft staan")
    if bron is None:
        pad.write_text("0" * len(sleutel))   # eerst overschrijven, dan wissen
        pad.unlink()
        return f"Sleutel staat nu in de sleutelhanger; {pad} is gewist."
    return "Sleutel staat nu in de sleutelhanger."


def exporteer_sleutel(doel: Path, pad: Path = SLEUTEL_PAD, sleutelhanger=SYSTEEM) -> None:
    """Reservekopie, bijv. op een usb-stick of om op een andere computer te importeren."""
    sleutel = geheime_sleutel(pad=pad, sleutelhanger=sleutelhanger)
    if sleutel is None:
        raise RuntimeError("er is nog geen sleutel")
    if doel.exists():
        raise RuntimeError(f"{doel} bestaat al; kies een andere naam")
    _schrijf_bestand(doel, sleutel)


def vingerafdruk(sleutel: bytes, soort: str, waarde: str) -> str:
    return hmac.new(sleutel, f"{soort}:{waarde}".encode(), hashlib.sha256).hexdigest()[:32]


def controle(sleutel: bytes) -> str:
    return vingerafdruk(sleutel, "controle", "")[:8]


def _alnum(s: str) -> str:
    return re.sub(r"[^0-9a-z]", "", s.lower())


def norm_straat(straat: str) -> str:
    """'Kerkstraat', 'Kerkstr.' en 'KERKSTRAAT' worden allemaal 'kerkstr'."""
    return re.sub(r"straat$", "str", _alnum(straat))


def sleutels_adres(straat: str, nummer: str, postcode: str) -> list[tuple[str, str]]:
    uit = []
    if straat and nummer:
        uit.append(("adres", f"straat:{norm_straat(straat)}{_alnum(nummer)}"))
    if postcode:
        if nummer:
            uit.append(("adres", f"postcode-nr:{_alnum(postcode)}{_alnum(nummer)}"))
        uit.append(("adres", f"postcode:{_alnum(postcode)}"))
    return uit


# ---------------------------------------------------------------------------
# familie.txt lezen
# ---------------------------------------------------------------------------

@dataclass
class Familiegegevens:
    per_regel: dict[str, dict[str, str]] = field(default_factory=dict)  # naamregel -> {afdruk: soort}
    sleutel_klopt: bool = True

    @property
    def leeg(self) -> bool:
        return not any(self.per_regel.values())

    def samenvoegen(self, ander: Familiegegevens) -> None:
        for regel, afdrukken in ander.per_regel.items():
            self.per_regel.setdefault(regel, {}).update(afdrukken)
        self.sleutel_klopt &= ander.sleutel_klopt


def lees_familie(pad: Path | None, sleutel: bytes | None) -> Familiegegevens:
    gegevens = Familiegegevens()
    if not pad or not pad.is_file():
        return gegevens
    naamregel = None
    gezien_controle = None
    for regel in pad.read_text(encoding="utf-8").splitlines():
        if m := CONTROLE_RE.search(regel):
            gezien_controle = m[1]
        if not regel.strip() or regel.lstrip().startswith("#"):
            continue
        if not regel[:1].isspace():
            naamregel = regel.strip()
            gegevens.per_regel.setdefault(naamregel, {})
        elif naamregel and (m := GEGEVEN_RE.match(regel)):
            gegevens.per_regel[naamregel][m[2].lower()] = m[1].lower()
    if not gegevens.leeg:
        gegevens.sleutel_klopt = sleutel is not None and gezien_controle in (None, controle(sleutel))
    return gegevens


# ---------------------------------------------------------------------------
# Herkennen in een document
# ---------------------------------------------------------------------------

BSN_KANDIDAAT_RE = re.compile(r"(?<![\d])(\d{4}\.\d{2}\.\d{3}|\d{3}[ .]\d{3}[ .]\d{3}|\d{8,9})(?!\d)")
HUISNUMMER_RE = re.compile(r"(?<![\w.,-])(\d{1,5}(?:[ -]?[A-Za-z]{1,2}(?![A-Za-z]))?)(?![\w]|[.,]\d)")
POSTCODE_KANDIDAAT_RE = re.compile(r"(?<![\w])([1-9]\d{3}[ ]?[A-Z]{2})(?![\w])(?:[ ,]+(\d{1,5}[A-Za-z]?)(?![\w]))?")


class FamilieIndex:
    """Zoekt BSN's, geboortedata en adressen van familieleden in tekst."""

    def __init__(self, gegevens: Familiegegevens, sleutel: bytes, personen: list[an.Persoon],
                 extra_namen: list[str]):
        self.sleutel = sleutel
        self.afdrukken: dict[str, tuple[str, str]] = {}   # afdruk -> (soort, naam)
        for p in personen:
            if p.positie[0] != -1 or not p.label:
                continue
            regel = extra_namen[p.positie[1]].strip()
            for afdruk, soort in gegevens.per_regel.get(regel, {}).items():
                self.afdrukken[afdruk] = (soort, p.label)

    def _zoek(self, soort: str, waarde: str) -> str | None:
        gevonden = self.afdrukken.get(vingerafdruk(self.sleutel, soort, waarde))
        return gevonden[1] if gevonden else None

    def vind(self, tekst: str) -> list[an.Vondst]:
        if not self.afdrukken:
            return []
        uit = []
        for m in BSN_KANDIDAAT_RE.finditer(tekst):
            naam = self._zoek("bsn", re.sub(r"\D", "", m[1]).zfill(9))
            if naam:
                uit.append(an.Vondst(m.start(1), m.end(1), "bsn", f"[BSN {naam}]", 10))
        for start, eind, jaar, iso in an.datum_kandidaten(tekst):
            if self._zoek("geboortedatum", iso):
                uit.append(an.Vondst(start, eind, "geboortedatum", jaar, 10))
        for m in POSTCODE_KANDIDAAT_RE.finditer(tekst):
            pc = _alnum(m[1])
            naam = m[2] and self._zoek("adres", f"postcode-nr:{pc}{_alnum(m[2])}")
            if naam:
                uit.append(an.Vondst(m.start(), m.end(), "adres", f"[adres {naam}]", 10))
            elif naam := self._zoek("adres", f"postcode:{pc}"):
                uit.append(an.Vondst(m.start(1), m.end(1), "adres", f"[adres {naam}]", 10))
        for m in HUISNUMMER_RE.finditer(tekst):
            # Probeer 1 tot 5 woorden vóór het huisnummer als straatnaam.
            regelbegin = tekst.rfind("\n", 0, m.start()) + 1
            woorden = list(re.finditer(r"[^\s,]+", tekst[regelbegin:m.start()]))
            for k in range(1, min(5, len(woorden)) + 1):
                straat = " ".join(w[0] for w in woorden[-k:])
                naam = self._zoek("adres", f"straat:{norm_straat(straat)}{_alnum(m[1])}")
                if naam:
                    uit.append(an.Vondst(regelbegin + woorden[-k].start(), m.end(), "adres",
                                         f"[adres {naam}]", 10))
                    break
        return uit


def maak_index(gegevens: Familiegegevens | None, sleutel: bytes | None, personen: list[an.Persoon],
               extra_namen: list[str]) -> FamilieIndex | None:
    if not gegevens or gegevens.leeg or not sleutel:
        return None
    return FamilieIndex(gegevens, sleutel, personen, extra_namen)


def verfijn(gekozen: list[an.Vondst], eigen: list[an.Vondst]) -> None:
    """Een algemene vondst ("[adres]") die een familiegegeven bevat, krijgt de naam erbij."""
    for v in gekozen:
        if v in eigen:
            continue
        for f in eigen:
            if f.soort == v.soort and f.start < v.eind and v.start < f.eind:
                v.vervanging = f.vervanging
                break


# ---------------------------------------------------------------------------
# Familielid toevoegen (met vragen)
# ---------------------------------------------------------------------------

def _vraag(invoer, tekst: str, controleer=None, fout: str = "") -> str:
    while True:
        antwoord = invoer(tekst).strip()
        if not antwoord or controleer is None or controleer(antwoord):
            return antwoord
        print(f"    {fout}")


def _datum_iso(tekst: str) -> str | None:
    kandidaten = an.datum_kandidaten(tekst.strip())
    if len(kandidaten) == 1 and kandidaten[0][0] == 0 and kandidaten[0][1] == len(tekst.strip()):
        return kandidaten[0][3]
    return None


def familielid_toevoegen(pad: Path, invoer=input, sleutelpad: Path = SLEUTEL_PAD,
                         sleutelhanger=SYSTEEM) -> str | None:
    """Vraag naam, roepnaam, BSN, geboortedatum en adres(sen) en schrijf vingerafdrukken weg."""
    sleutel = geheime_sleutel(maak=True, pad=sleutelpad, sleutelhanger=sleutelhanger)
    print("Familielid toevoegen. De gegevens worden alleen als vingerafdruk opgeslagen.\n"
          "Enter zonder iets in te vullen = overslaan.\n")
    naam = _vraag(invoer, "  Naam (alle voornamen en achternaam): ")
    if not naam:
        return None
    roepnaam = _vraag(invoer, "  Roepnaam (Enter = eerste voornaam): ")
    bsn = _vraag(invoer, "  BSN: ", lambda a: an.elfproef(re.sub(r"\D", "", a)),
                 "Dit is geen geldig BSN (elfproef klopt niet), probeer opnieuw.")
    geboren = _vraag(invoer, "  Geboortedatum (bijv. 10-02-1980 of 10 februari 1980): ", _datum_iso,
                     "Onbekende datum, gebruik bijv. 10-02-1980 of 10 februari 1980.")

    afdrukken: list[tuple[str, str, str]] = []   # (soort, afdruk, uitleg)
    if bsn:
        afdrukken.append(("bsn", vingerafdruk(sleutel, "bsn", re.sub(r"\D", "", bsn).zfill(9)), ""))
    if geboren:
        afdrukken.append(("geboortedatum", vingerafdruk(sleutel, "geboortedatum", _datum_iso(geboren)), ""))
    nummer = 1
    while True:
        straat = _vraag(invoer, f"  Adres {nummer} – straat (Enter = klaar): ")
        if not straat:
            break
        huisnummer = _vraag(invoer, "    Huisnummer + toevoeging (bijv. 12a): ")
        postcode = _vraag(invoer, "    Postcode: ", lambda a: re.fullmatch(r"[1-9]\d{3} ?[A-Za-z]{2}", a),
                          "Gebruik de vorm 1234 AB.")
        uitleg = {"straat": "straat + huisnummer", "postcode-nr": "postcode + huisnummer", "postcode": "postcode"}
        for soort, waarde in sleutels_adres(straat, huisnummer, postcode):
            afdrukken.append((soort, vingerafdruk(sleutel, soort, waarde), uitleg[waarde.split(":")[0]]))
        nummer += 1

    regels = []
    bestaand = pad.read_text(encoding="utf-8") if pad.is_file() else ""
    if not CONTROLE_RE.search(bestaand):
        regels.append(f"# sleutel-controle: {controle(sleutel)}  (niet wijzigen)")
    regels.append(f"{naam} = {roepnaam}" if roepnaam else naam)
    for soort, afdruk, uitleg in afdrukken:
        regels.append(f"    {soort}: {afdruk}" + (f"   # {uitleg}" if uitleg else ""))
    with pad.open("a", encoding="utf-8") as f:
        if bestaand and not bestaand.endswith("\n"):
            f.write("\n")
        f.write(("\n" if bestaand else "") + "\n".join(regels) + "\n")
    label = roepnaam or naam.split()[0]
    print(f"\n  Opgeslagen in {pad}. Voortaan: [BSN {label}], [adres {label}].")
    return label
