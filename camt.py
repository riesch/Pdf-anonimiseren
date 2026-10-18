"""Anonimiseer CAMT.053-bankafschriften (XML), los of als ZIP met meerdere afschriften.

Elk XML-veld wordt behandeld naar zijn betekenis in de standaard:

  * IBAN / rekeningnummer      -> sterretjes behalve de laatste 4 tekens
  * naam van een privépersoon  -> voornaam (of "Persoon 1"); bedrijven blijven staan
  * adres van een privépersoon -> "[adres]"
  * geboortedatum / -plaats    -> jaar / "[geboorteplaats]"
  * telefoon, e-mail           -> "[telefoon]", "[e-mail]"
  * persoonlijke ID's          -> "[id]" (KvK-/incassant-ID's van bedrijven blijven)
  * kenmerken (EndToEndId, machtiging, betalingskenmerk) -> "[kenmerk]"
  * omschrijvingen (Ustrd, AddtlNtryInf, ...) -> zoals vrije tekst bij CSV-exports

Datums, bedragen, BIC's en transactiecodes blijven staan.
"""

from __future__ import annotations

import io
import re
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

import anonimiseer as an
from bankdata import BankAnonimiseerder, Sessie, _is_rekening, _sleutel

CAMT_EXTENSIES = {".xml", ".zip"}

PARTIJEN = {"Dbtr", "Cdtr", "UltmtDbtr", "UltmtCdtr", "Ownr", "InitgPty", "MsgRcpt", "Invcr", "Invcee"}
BANKEN = {"FinInstnId", "Svcr", "BrnchId", "DbtrAgt", "CdtrAgt", "IntrmyAgt1", "IntrmyAgt2",
          "IntrmyAgt3", "RcvgAgt", "DlvrgAgt", "IssgAgt", "SttlmPlc", "Agt"}
ADRES = {"StrtNm", "BldgNb", "BldgNm", "Flr", "PstBx", "Room", "PstCd", "TwnNm", "TwnLctnNm",
         "DstrctNm", "CtrySubDvsn", "AdrLine", "Dept", "SubDept"}
KENMERKEN = {"EndToEndId", "MndtId", "InstrId", "TxId", "PmtInfId", "UETR", "ChqNb"}
VRIJ = {"Ustrd", "AddtlNtryInf", "AddtlTxInf", "AddtlInf", "AddtlStmtInf", "AddtlRmtInf"}
# Codes en bedrijfsgegevens die nooit persoonsgegevens zijn.
BEHOUD = {"Cd", "Prtry", "Issr", "Ccy", "Sts", "CdtDbtInd", "BIC", "BICFI", "AnyBIC", "Ctry",
          "CtryOfBirth", "CtryOfRes", "Amt", "Dt", "DtTm", "BookgDt", "ValDt", "CreDtTm",
          "FrDtTm", "ToDtTm", "ElctrncSeqNb", "LglSeqNb", "NbOfNtries", "Sum", "TtlNetNtryAmt",
          "SchmeNm", "LEI", "ClrSysMmbId", "MmbId", "Fmly", "SubFmlyCd", "Domn"}


def _naam(el: ET.Element) -> str:
    return el.tag.rsplit("}", 1)[-1]


class Boom:
    """Een XML-document met snelle toegang tot de voorouders van elk element."""

    def __init__(self, naam: str, data: bytes):
        self.naam = naam
        self.boom = ET.ElementTree(ET.fromstring(data))
        wortel = self.boom.getroot()
        if "camt.05" not in wortel.tag:
            raise ValueError(f"{naam} is geen CAMT-bestand")
        self.ns = wortel.tag[1:].split("}")[0] if wortel.tag.startswith("{") else ""
        self.ouder = {kind: ouder for ouder in wortel.iter() for kind in ouder}

    def voorouders(self, el: ET.Element) -> list[str]:
        uit = []
        while el in self.ouder:
            el = self.ouder[el]
            uit.append(_naam(el))
        return uit

    def partij(self, el: ET.Element) -> ET.Element | None:
        while el in self.ouder:
            el = self.ouder[el]
            if _naam(el) in PARTIJEN:
                return el
        return None

    def tekstvelden(self):
        for el in self.boom.getroot().iter():
            if len(el) == 0 and el.text and el.text.strip():
                yield el

    def naar_bytes(self) -> bytes:
        if self.ns:
            ET.register_namespace("", self.ns)
        buf = io.BytesIO()
        self.boom.write(buf, encoding="UTF-8", xml_declaration=True)
        return buf.getvalue()


def partijnaam(boom: Boom, partij: ET.Element) -> str | None:
    for el in partij.iter():
        if _naam(el) == "Nm" and el.text and not set(boom.voorouders(el)) & BANKEN:
            return el.text.strip()
    return None


def lees_bomen(pad: Path) -> list[Boom]:
    if pad.suffix.lower() == ".zip":
        with zipfile.ZipFile(pad) as z:
            namen = [n for n in z.namelist() if n.lower().endswith(".xml") and not n.endswith("/")]
            return [Boom(n, z.read(n)) for n in sorted(namen)]
    return [Boom(pad.name, pad.read_bytes())]


class CamtAnonimiseerder(BankAnonimiseerder):

    def _soort(self, boom: Boom, el: ET.Element) -> str:
        """Bepaal wat er met een tekstveld moet gebeuren."""
        tag, voor = _naam(el), boom.voorouders(el)
        if set(voor) & BANKEN or tag in BEHOUD:
            return "behoud"
        if tag == "IBAN":
            return "rekening"
        if tag == "Id" and voor[:1] == ["Othr"] and any(v.endswith("Acct") for v in voor):
            return "rekening"
        if tag == "Id" and "PrvtId" in voor and "CdtrSchmeId" not in voor:
            return "id"
        if tag == "Id" and "OrgId" in voor:
            return "behoud"
        if tag == "Nm":
            if boom.partij(el) is not None:
                return "naam"
            return "vrij"   # bijv. de naam van een rekening ("Spaarrekening J Jansen")
        if tag in ADRES:
            return "adres"
        if tag == "BirthDt":
            return "geboortedatum"
        if tag in ("CityOfBirth", "PrvcOfBirth"):
            return "geboorteplaats"
        if tag in ("PhneNb", "MobNb", "FaxNb"):
            return "telefoon"
        if tag == "EmailAdr":
            return "email"
        if tag in KENMERKEN or (tag == "Ref" and "CdtrRefInf" in voor):
            return "kenmerk"
        if tag in VRIJ:
            return "vrij"
        return "auto"

    def verwerk(self, invoer: Path, uitmap: Path, sleutelbestand: Path | None = None) -> an.Resultaat:
        bomen = lees_bomen(invoer)
        if not bomen:
            raise ValueError("geen XML-bestanden gevonden")
        velden = [(b, el, self._soort(b, el)) for b in bomen for el in b.tekstvelden()]

        # Ronde 1: eigen rekeningen (Stmt/Acct) eerst, dan namen verzamelen.
        sessie = Sessie(self)
        for b, el, soort in velden:
            if soort == "rekening" and b.voorouders(el)[1:3] in (["Acct", "Stmt"], ["Acct", "Rpt"],
                                                                   ["Acct", "Ntfctn"]):
                sessie.register.masker(_sleutel(el.text.strip()))
        namen = [el.text.strip() for _, el, soort in velden if soort == "naam"]
        teksten = [el.text.strip() for _, el, soort in velden if soort in ("vrij", "auto")]
        sessie.stel_namen_vast(self.verzamel(namen, teksten), invoer.name, namen + teksten)

        # Welke partijen zijn privépersonen? Hun adres gaat eruit.
        personen = set()
        for b in bomen:
            for partij in b.boom.getroot().iter():
                if _naam(partij) in PARTIJEN:
                    naam = partijnaam(b, partij)
                    if naam is None or sessie.is_persoon(naam):
                        personen.add(partij)

        # Ronde 2: elk veld vervangen.
        for b, el, soort in velden:
            waarde = el.text.strip()
            if soort == "rekening" and _is_rekening(waarde):
                el.text = sessie.rekening(waarde)
            elif soort == "naam":
                el.text = sessie.tegenpartij(waarde)
            elif soort == "adres" and b.partij(el) in personen:
                sessie.tel("adres")
                el.text = "[adres]"
            elif soort == "geboortedatum":
                sessie.tel("geboortedatum")
                el.text = waarde[:4]
            elif soort == "id" and b.partij(el) not in personen:
                continue   # bijv. het incassant-ID van een bedrijf
            elif soort in ("geboorteplaats", "telefoon", "email", "id"):
                sessie.tel(soort)
                el.text = {"email": "[e-mail]"}.get(soort, f"[{soort}]")
            elif soort == "kenmerk":
                el.text = sessie.kenmerk(waarde)
            elif soort == "vrij" or (soort == "auto" and not re.fullmatch(r"[\d\s.,:+\-TZ/]*", waarde)):
                el.text = sessie.vrije_tekst(waarde)

        uitmap.mkdir(parents=True, exist_ok=True)
        stam = sessie.bestandsnaam(invoer.stem)
        if invoer.suffix.lower() == ".zip":
            uitpad = uitmap / f"{stam}_anoniem.zip"
            delen = []
            with zipfile.ZipFile(uitpad, "w", zipfile.ZIP_DEFLATED) as z:
                for b in bomen:
                    data = b.naar_bytes()
                    naam = Path(b.naam)
                    z.writestr(f"{sessie.bestandsnaam(naam.stem)}{naam.suffix}", data)
                    delen.append(data.decode("utf-8"))
            uittekst = "\n".join(delen)
        else:
            uitpad = uitmap / f"{stam}_anoniem.xml"
            data = bomen[0].naar_bytes()
            uitpad.write_bytes(data)
            uittekst = data.decode("utf-8")
        return sessie.resultaat(uitpad, uittekst, sleutelbestand)
