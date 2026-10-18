"""Namen herkennen (titels, velden, taalmodel, familielijst), koppelen tot personen en
vervangen door een label: de voornaam, of "Persoon 1" als die onbekend is."""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

from patronen import *  # noqa: F401,F403
from patronen import _tv  # noqa: F401


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
    extra_achternamen: list[str] = field(default_factory=list)   # bijv. getrouwde naam
    lijstregels: set[int] = field(default_factory=set)   # regels in de namen-/familielijst

    @property
    def achternamen(self) -> list[str]:
        return ([self.achternaam] if self.achternaam else []) + self.extra_achternamen

    @property
    def alle_voornamen(self) -> list[str]:
        return self.voornamen + ([self.roepnaam] if self.roepnaam and self.roepnaam not in self.voornamen else [])


def _norm(s: str | None) -> str:
    return re.sub(r"\s+", " ", s or "").strip().lower()


def _dubbele_achternamen(tokens: list[str]) -> list[str]:
    """['Anna', 'Jansen-de', 'Vries'] -> ['Anna', 'Jansen-de Vries']."""
    uit, i = [], 0
    while i < len(tokens):
        t = tokens[i]
        if "-" in t and _tv(t.rsplit("-", 1)[1]) and i + 1 < len(tokens):
            j = i + 1
            while j < len(tokens) - 1 and _tv(tokens[j]):
                j += 1
            uit.append(" ".join(tokens[i:j + 1]))
            i = j + 1
        else:
            uit.append(t)
            i += 1
    return uit


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
    tokens = _dubbele_achternamen(tokens)
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
            # "Anna de Vries | Anna Jansen-de Vries = Anna": twee namen van dezelfde persoon
            naam, _, roepnaam = regel.partition("=")
            for vorm in naam.split("|"):
                v = ontleed_naam(vorm, (-1, i))
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
            p.lijstregels.add(v.positie[1])
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

    # Namen op dezelfde lijstregel ("Anna de Vries | Anna Jansen-de Vries") zijn één persoon.
    for p in list(personen):
        for q in [q for q in personen if q is not p and q.lijstregels & p.lijstregels]:
            if q not in personen or p not in personen:
                continue
            for vn in q.voornamen:
                if vn.lower() not in (x.lower() for x in p.voornamen):
                    p.voornamen.append(vn)
            p.initialen = max(p.initialen, q.initialen, key=len)
            p.roepnaam = p.roepnaam or q.roepnaam
            p.extra_achternamen += [a for a in q.achternamen
                                    if _norm(a) not in (_norm(b) for b in p.achternamen)]
            p.positie = min(p.positie, q.positie)
            p.teksten += [t for t in q.teksten if t not in p.teksten]
            p.lijstregels |= q.lijstregels
            personen.remove(q)

    return personen, ken_labels_toe(personen, alleen_opgegeven)


def ken_labels_toe(personen: list[Persoon], alleen_opgegeven: bool = False) -> dict[str, str]:
    """Label = roepnaam of eerste voornaam; zonder bekende voornaam "Persoon N".

    Twee verschillende personen met dezelfde voornaam: "Jan" en "Jan (2)".
    """
    gebruikt: dict[str, int] = {}
    zonder = 0
    # Wie dezelfde achternaam heeft als een opgegeven familielid, hoort er
    # waarschijnlijk ook bij; anders zou "Piet Jansen" de familienaam verraden.
    familienamen = {_norm(a) for p in personen if p.opgegeven for a in p.achternamen}
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
        for achternaam in p.achternamen:   # ook een tweede (bijv. getrouwde) achternaam
            ach = _achternaam_patroon(achternaam)
            # Tussenvoegsel aan het begin mag ook met hoofdletter ("Van den Berg").
            voor_deel = []
            if vn_alt:
                voor_deel.append(rf"(?:{vn_alt})(?:[ ]+(?:{vn_alt}))*")
            if init_pat:
                voor_deel.append(init_pat)
            if voor_deel:
                patronen.append((rf"(?:{'|'.join(voor_deel)})[ ]*{ach}", p.label))
                patronen.append((rf"{ach},[ ]?(?:{'|'.join(voor_deel)})(?:[ ]{TV_I}){{0,3}}", p.label))
            fam = families.get(_norm(achternaam))
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
