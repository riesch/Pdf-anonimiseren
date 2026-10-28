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
import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import pymupdf as fitz  # PyMuPDF

# De onderdelen staan in aparte modules; hier samengebracht, zodat "import anonimiseer as an"
# alles blijft bieden (ook voor bankdata.py, camt.py, familie.py en de tests).
from patronen import *  # noqa: F401,F403
from patronen import _jaar, _tv  # noqa: F401
from namen import *  # noqa: F401,F403
from namen import _achternaam_patroon, _dubbele_achternamen, _hoofdletter, _norm, _tok  # noqa: F401
from pdf import *  # noqa: F401,F403
from pdf import _lettergrootte, _midden  # noqa: F401

import controle
import familie as fam
from tekstbestanden import lees_tekstbestand


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
    als_persoon: dict[str, str] = field(default_factory=dict)   # bankdata: tegenpartij -> label


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
            # Met familie.txt worden alleen de geboortedata van familieleden ingekort.
            overig, _ = vind_overig(tekst, register, self.alle_datums, not self.alleen_familie)
            eigen = familie.vind(tekst) if familie else []
            gekozen = kies_vondsten(overig + vind_namen(tekst, patronen) + eigen
                                    + (extra[len(per_pagina)] if extra else []))
            if eigen:
                fam.verfijn(gekozen, eigen)
            per_pagina.append(gekozen)
            # Datums die door geen enkele vondst (geboorte, tabelkolom, familie) zijn vervangen.
            blijft += sum(1 for start, eind, _, _ in datum_kandidaten(tekst)
                          if not any(v.start < eind and start < v.eind for v in gekozen))
        return per_pagina, personen, patronen, register, blijft, familie

    def verwerk_tekst(self, invoer: Path, uitmap: Path, sleutelbestand: Path | None = None) -> Resultaat:
        """Word (.docx) of e-mail (.eml): lezen als tekst, anonimiseren, als Markdown wegschrijven."""
        delen = lees_tekstbestand(invoer)
        kopjes, paginas = [k for k, _ in delen], [t for _, t in delen]
        per_pagina, personen, patronen, register, blijft, familie = self.analyseer(paginas, invoer.name)
        uitvoer = [pas_toe(t, vs) for t, vs in zip(paginas, per_pagina)]
        stam = anonieme_bestandsnaam(invoer.stem, patronen, register, familie, not self.alleen_familie)
        uitmap.mkdir(parents=True, exist_ok=True)
        tellingen: dict[str, int] = {}
        for vs in per_pagina:
            for v in vs:
                if v.soort != "behouden":
                    tellingen[v.soort] = tellingen.get(v.soort, 0) + 1
        tekst_pad = uitmap / f"{stam}_anoniem.md"
        tekst_pad.write_text(f"# {stam} (geanonimiseerd)\n\n" + "\n".join(
            f"## {kop}\n\n{re.sub(chr(10) * 3 + '+', chr(10) * 2, t).strip()}\n"
            for kop, t in zip(kopjes, uitvoer)), encoding="utf-8")
        if sleutelbestand:
            schrijf_sleutel(sleutelbestand, personen, register)
        bevindingen = controle.controleer_uitvoer(
            [("bestandsnaam", stam)] + list(zip(kopjes, uitvoer)), personen, familie, self.herkenner.behoud,
            nlp=self.herkenner.nlp, namen_verdacht=not self.alleen_familie,
            datums_verdacht=not (self.alle_datums or self.alleen_familie))
        r = Resultaat(None, tekst_pad, tellingen, blijft, [], [])
        return controle.afronden(r, invoer, uitmap, bevindingen, self.rapport)

    def familie_index(self, personen: list[Persoon]):
        return fam.maak_index(self.familiegegevens, self.sleutel, personen, self.herkenner.extra_namen)

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
        # Verborgen lagen aanzetten en tekst buiten het paginavlak weghalen: allebei
        # onzichtbaar, maar wel in de PDF aanwezig.
        extra: dict[str, int] = {}
        if n := zet_lagen_aan(doc):
            extra["verborgen laag (aangezet)"] = n
        if n := sum(verwijder_tekst_buiten_pagina(page) for page in doc):
            extra["tekst buiten pagina (verwijderd)"] = n
        lezingen, scans, ocr_paginas, afbeeldingen = [], [], [], []
        ocr_controle: list[int] = []   # pagina's die na afloop met OCR nagelezen worden
        for nr, page in enumerate(doc, 1):
            lezing = lees_pagina(page)
            if lijkt_scan(page, lezing.tekst):
                try:
                    if not self.ocr_taal:
                        raise RuntimeError("geen OCR")
                    tp = page.get_textpage_ocr(language=self.ocr_taal, dpi=300, full=True)
                    lezing = lees_pagina(page, tp)
                    lezing.ocr = True
                    ocr_paginas.append(nr)
                    ocr_controle.append(nr)
                except Exception:   # geen Tesseract of OCR mislukt: pagina telt als lek
                    scans.append(nr)
            else:
                tabellen = zoek_tabellen(page)
                if (groot := grote_afbeeldingen(page)) and not self.zonder_afbeeldingen:
                    # Een afbeelding met al een tekstlaag (doorzoekbare scan) niet opnieuw
                    # lezen: dan zou de tekst dubbel in de uitvoer staan.
                    met_tekst = [r for r, _ in groot if heeft_tekstlaag(lezing, r)]
                    zonder_tekst = [(r, a) for r, a in groot if r not in met_tekst]
                    if met_tekst and self.ocr_taal:
                        ocr_controle.append(nr)   # wel nalezen of de scan echt schoon is
                    if zonder_tekst:
                        # Een digitale pagina met een ingevoegde scan: alleen de afbeeldingen
                        # met OCR lezen en die tekst samenvoegen met de digitale tekst.
                        try:
                            if not self.ocr_taal:
                                raise RuntimeError("geen OCR")
                            tp = page.get_textpage_ocr(language=self.ocr_taal, dpi=300, full=False)
                            met_ocr = lees_pagina(page, tp, negeer_ocr=met_tekst)
                            if met_ocr.ocr_regels:
                                lezing = met_ocr
                                ocr_paginas.append(nr)
                                ocr_controle.append(nr)
                        except Exception:
                            afbeeldingen.append((nr, max(aandeel for _, aandeel in zonder_tekst)))
                lezing.tabellen = tabellen
            lezingen.append(lezing)
        paginas = [l.tekst for l in lezingen]

        per_pagina, personen, patronen, register, blijft, familie = self.analyseer(
            paginas, invoer.name, extra=[[] if self.alleen_familie else tabel_vondsten(l) for l in lezingen],
            tabelnamen=[tabel_namen(l) for l in lezingen])
        uitvoer = [pagina_markdown(l, vs) for l, vs in zip(lezingen, per_pagina)]

        # Ook de bestandsnaam kan namen bevatten.
        stam = anonieme_bestandsnaam(invoer.stem, patronen, register, familie, not self.alleen_familie)
        uitmap.mkdir(parents=True, exist_ok=True)

        tellingen: dict[str, int] = dict(extra)
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
            lekken = controleer(pdf_pad, paginas, per_pagina, sorted(set(ocr_controle)), self.ocr_taal)
        doc.close()

        if sleutelbestand:
            schrijf_sleutel(sleutelbestand, personen, register)

        bevindingen = controle.controleer_uitvoer(
            [("bestandsnaam", stam)] + [(f"pagina {i}", t) for i, t in enumerate(uitvoer, 1)], personen, familie, self.herkenner.behoud, nlp=self.herkenner.nlp,
            namen_verdacht=not self.alleen_familie, datums_verdacht=not (self.alle_datums or self.alleen_familie))
        r = Resultaat(pdf_pad, tekst_pad, tellingen, blijft, scans, lekken, ocr=ocr_paginas,
                      tabellen=sum(len(l.tabellen) for l in lezingen), afbeeldingen=afbeeldingen)
        return controle.afronden(r, invoer, uitmap, bevindingen, self.rapport)


def anonieme_bestandsnaam(stam: str, patronen, register: Register, familie=None,
                          geboortedata: bool = True) -> str:
    """Ook een bestandsnaam kan een naam, rekeningnummer of (via de familielijst) een
    BSN, adres of geboortedatum van een familielid bevatten."""
    overig, _ = vind_overig(stam, register, alle_datums=False, geboortedata=geboortedata)
    eigen = familie.vind(stam) if familie else []
    vondsten = kies_vondsten(overig + vind_namen(stam, patronen) + eigen)
    if eigen:
        fam.verfijn(vondsten, eigen)
    return re.sub(r"[^\w\- ]+", "", pas_toe(stam, vondsten)).strip() or "document"


def schrijf_sleutel(pad: Path, personen: list[Persoon], register: Register) -> None:
    sleutel = {
        "personen": {p.label: {"voornamen": p.voornamen, "roepnaam": p.roepnaam,
                               "initialen": p.initialen, "achternamen": p.achternamen}
                     for p in personen if p.label is not None},
        "rekeningen en tegenpartijen": {label: waarde for (_, waarde), label in register.labels.items()},
    }
    pad.parent.mkdir(parents=True, exist_ok=True)
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
            tp = None
            if i in ocr_paginas and ocr_taal:
                try:
                    tp = page.get_textpage_ocr(language=ocr_taal, dpi=300, full=True)
                except Exception:
                    lekken.append((i, "pagina (nalezen met OCR mislukt)"))
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


SLEUTELMAP = Path.home() / ".pdf-anonimiseren" / "sleutelbestanden"
ONDERSTEUND = {".pdf", ".csv", ".tsv", ".txt", ".xml", ".zip", ".docx", ".eml"}


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
                    help="PDF, Word (.docx), e-mail (.eml), bankexport (.csv/.tsv/.txt), "
                         "CAMT.053 (.xml/.zip), of map(pen) daarmee")
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
    ap.add_argument("--toon-tegenpartijen", action="store_true",
                    help="bankdata: toon in de terminal welke tegenpartijen als privépersoon zijn "
                         "behandeld (handig om winkels in behoud.txt te zetten)")
    ap.add_argument("--sleutel", action="store_true",
                    help="schrijf ook een sleutelbestand (pseudoniem -> echte gegevens) in "
                         "~/.pdf-anonimiseren/sleutelbestanden/. Bevat persoonsgegevens: nooit delen!")
    args = ap.parse_args(argv)

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
        doel = args.familie or fam.vind_familie() or fam.PROGRAMMAMAP / "familie.txt"
        print(f"Familielijst: {doel.resolve()}\n")
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
            print(f"Overgeslagen (geen ondersteund bestand of bestaat niet): {pad}", file=sys.stderr)
    if not bestanden:
        print("Geen ondersteunde bestanden gevonden (PDF, Word, e-mail, bankexport, CAMT).", file=sys.stderr)
        return 1

    # Hier pas importeren: bankdata en camt gebruiken zelf anonimiseer.py.
    from bankdata import BankAnonimiseerder
    from camt import CamtAnonimiseerder

    familie = None if args.alle_namen else (args.familie or fam.vind_familie())
    if familie:
        print(f"Familielijst {familie.resolve()}: alleen deze personen worden vervangen "
              "(--alle-namen om alle namen te vervangen).\n")
    else:
        print("Geen familielijst gevonden: alle namen worden vervangen. (Gezocht: "
              + ", ".join(str(p) for p in fam.familie_kandidaten()) + ")\n")
    namen = lees_lijst(args.namen) + lees_lijst(familie)
    geheime_sleutel = fam.geheime_sleutel()
    gegevens = fam.lees_familie(args.namen, geheime_sleutel)
    gegevens.samenvoegen(fam.lees_familie(familie, geheime_sleutel))
    for waarschuwing in gegevens.waarschuwingen:
        print(f"Let op: {waarschuwing}")
    if gegevens.waarschuwingen:
        print()
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
    overzicht: list[tuple[str, str]] = []   # (bestandsnaam, status) voor de samenvatting
    for i, pad in enumerate(bestanden, 1):
        uitmap = args.uitvoer or pad.parent / "geanonimiseerd"
        # Het sleutelbestand bevat echte gegevens: nooit naast de uitvoer (bijv. in iCloud).
        # (Niet verwarren met geheime_sleutel: die is voor de vingerafdrukken in familie.txt.)
        sleutel = (SLEUTELMAP / f"{datetime.now():%Y%m%d-%H%M%S}_{i}.json") if args.sleutel else None
        try:
            if pad.suffix.lower() in (".pdf", ".docx", ".eml"):
                anon = anon or Anonimiseerder(namen, behoud, gebruik_ner=not args.geen_ner,
                                              alle_datums=args.alle_datums, alleen_familie=bool(familie),
                                              vrager=vrager, zonder_afbeeldingen=args.zonder_afbeeldingen,
                                              familiegegevens=gegevens, sleutel=geheime_sleutel,
                                              rapport=not args.geen_rapport, ocr=not args.geen_ocr)
                if pad.suffix.lower() == ".pdf":
                    r = anon.verwerk(pad, uitmap, maak_pdf=not args.alleen_tekst,
                                     maak_tekst=not args.alleen_pdf, sleutelbestand=sleutel)
                else:
                    r = anon.verwerk_tekst(pad, uitmap, sleutelbestand=sleutel)
            elif pad.suffix.lower() in (".xml", ".zip"):
                camt = camt or CamtAnonimiseerder(namen, behoud, args.alle_tegenpartijen,
                                                  alleen_familie=bool(familie), vrager=vrager,
                                                  familiegegevens=gegevens, sleutel=geheime_sleutel,
                                                  rapport=not args.geen_rapport)
                r = camt.verwerk(pad, uitmap, sleutelbestand=sleutel)
            else:
                bank = bank or BankAnonimiseerder(namen, behoud, args.alle_tegenpartijen,
                                                  alleen_familie=bool(familie), vrager=vrager,
                                                  familiegegevens=gegevens, sleutel=geheime_sleutel,
                                                  rapport=not args.geen_rapport)
                r = bank.verwerk(pad, uitmap, sleutelbestand=sleutel)
        except Exception as e:
            print(f"[{i}] FOUT bij {pad.name}: {e}", file=sys.stderr)
            fouten += 1
            overzicht.append((pad.name, "fout"))
            continue
        overzicht.append((pad.name, "controleren" if r.gemarkeerd else
                          ("nakijken" if any(b.ernst == "verdacht" for b in r.bevindingen) else "klaar")))
        samenvatting = ", ".join(f"{n}x {s}" for s, n in sorted(r.tellingen.items())) or "niets gevonden"
        print(f"[{i}] {pad.name}: {samenvatting}")
        for uit in (r.pdf, r.tekst):
            if uit:
                print(f"     -> {uit}")
        if r.datums_blijven and not familie:
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
        if r.als_persoon:
            if args.toon_tegenpartijen:
                print("     Als privépersoon behandeld (alleen hier getoond; staat er een bedrijf tussen, "
                      "zet het dan in behoud.txt):")
                for naam, label in sorted(r.als_persoon.items()):
                    print(f"        {naam}  ->  {label}")
            else:
                print(f"     {len(r.als_persoon)} tegenpartij(en) als privépersoon behandeld "
                      "(bekijk ze met --toon-tegenpartijen)")
        if r.rapport:
            print(f"     controlerapport: {r.rapport}")
        if sleutel:
            print(f"     sleutel: {sleutel}\n"
                  "     !! Dit bestand bevat ECHTE persoonsgegevens. Niet delen, niet aan een AI geven; "
                  "verwijder het als je het niet meer nodig hebt.")
    if len(overzicht) > 1:
        aantal = {s: sum(1 for _, x in overzicht if x == s) for s in ("klaar", "nakijken", "controleren", "fout")}
        print(f"\nSamenvatting: {len(overzicht)} bestanden – {aantal['klaar']} schoon, "
              f"{aantal['nakijken']} met verdachte plekken, {aantal['controleren']} met _CONTROLEREN, "
              f"{aantal['fout']} fout")
        teken = {"klaar": "  ok ", "nakijken": "  ?  ", "controleren": "  !! ", "fout": "  XX "}
        for naam, status in overzicht:
            if status != "klaar":
                print(f"{teken[status]}{naam}")
    print("\nControleer het resultaat altijd zelf voordat je het met een AI deelt.")
    return 1 if fouten else 0


if __name__ == "__main__":
    sys.exit(main())
