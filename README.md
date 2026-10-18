# Pdf-anonimiseren

Script om PDF-bestanden en bankexports te anonimiseren, zodat je ze veilig(er) aan een AI kunt geven.

## PDF-bestanden

| Gegeven | Wordt |
|---|---|
| Namen | alleen de voornaam: `Jan Pieter Jansen`, `J. Jansen` en `Dhr. Jansen` → `Jan` |
| Twee verschillende personen met dezelfde voornaam | `Jan` en `Jan (2)` |
| Persoon van wie geen voornaam bekend is (bijv. alleen `A. de Vries`) | `Persoon 1`, `Persoon 2`, … |
| Alleen een achternaam die bij meerdere personen hoort (bijv. "Dhr. Jansen") | `Familie A` |
| BSN | `[BSN]` |
| Adressen (straat + huisnummer, postcode + plaats, velden als "Adres:") | `[adres]` |
| Geboortedata | alleen het geboortejaar, bijv. `1985` (ook voluit: "vijf april negentienhonderdvijfentachtig") |
| IBAN / rekeningnummers | sterretjes behalve de laatste 4: `**************4300` |
| Twee rekeningen met dezelfde laatste 4 cijfers | `**************4300` en `**************4300 (2)` |
| E-mailadressen en telefoonnummers | `[e-mail]` / `[telefoon]` |

### Notariële akten

Akten noemen alle voornamen voluit ("de heer Johannes Petrus Maria JANSEN"), vaak met de
achternaam in hoofdletters. Het script herkent dat en maakt er `Johannes` van. Verder:

| In de akte | Wordt |
|---|---|
| geboren te Amersfoort | geboren te `[geboorteplaats]` |
| wonende te Utrecht | wonende te `[woonplaats]` |
| paspoort/identiteitskaart/rijbewijs nummer … | `[documentnummer]` |
| kadastraal bekend gemeente …, sectie …, nummer … | kadastraal bekend `[kadastrale aanduiding]` |

**Roepnaam:** staat in de akte "Johannes" maar heet iemand eigenlijk Hans, zet dan in `namen.txt`:

```
Johannes Petrus Maria Jansen = Hans
```

Dan wordt die persoon overal `Hans`.

Daarnaast worden metadata (auteur, titel), bladwijzers en bijlagen uit de PDF verwijderd,
worden ingevulde formuliervelden meegenomen, en wordt een naam in de bestandsnaam ook vervangen.

## Bankexports (CSV/TXT)

Exports van je bankrekening (ING, Rabobank, ABN AMRO, ASN/SNS, Triodos, bunq, Knab, …)
gaan met hetzelfde commando:

```bash
python anonimiseer.py NL00BANK0123456789_01-01-2024_31-12-2024.csv
python anonimiseer.py TXT240101.TXT          # ABN AMRO tekst-export
```

Hier blijven **datums en bedragen** juist staan, zodat een AI je uitgaven nog kan analyseren.

| Gegeven | Wordt |
|---|---|
| Rekeningnummers | sterretjes behalve de laatste 4 (`**************4300`), uniek per rekening, dus terugkerende betalingen blijven herkenbaar |
| Privépersonen als tegenpartij (`HR J JANSEN EO`, `J. Jansen`) | de voornaam als die bekend is (via `namen.txt`), anders `Persoon 1`, … – ook in de omschrijving |
| Bedrijven (Albert Heijn, Eneco, Gemeente, …) | blijven staan; met `--alle-tegenpartijen` → `[tegenpartij 1]` |
| Machtigingskenmerk, betalingskenmerk, transactiereferentie | `[kenmerk]` |
| Pasnummers, klant-/polis-/contractnummers in omschrijvingen | `[pasnummer]` / `[klantnummer]` |
| BSN, adressen, e-mail, telefoon in omschrijvingen | zoals bij PDF's |

Het resultaat is weer een CSV/TXT met dezelfde kolommen (`<naam>_anoniem.csv`). Ook een
rekeningnummer in de bestandsnaam wordt vervangen.

Of een tegenpartij een persoon of een bedrijf is, wordt geschat: woorden als *B.V.*,
*Stichting*, *Gemeente*, cijfers of bekende winkelnamen wijzen op een bedrijf; titels
(*HR*, *MW*) of initialen + achternaam op een persoon. Wordt een bedrijf toch als persoon
gezien, zet de naam dan in `behoud.txt`. Voornamen die alleen in een vrije omschrijving staan
("cadeau voor Sanne") worden alleen vervangen als ze in `namen.txt` staan.

## Installatie

```bash
pip install -r requirements.txt
python -m spacy download nl_core_news_md   # optioneel, maar aanbevolen
```

## Gebruik

```bash
python anonimiseer.py brief.pdf                     # één bestand
python anonimiseer.py map_met_pdfs/ -o uitvoer/     # een hele map
```

Per PDF krijg je in de map `geanonimiseerd/`:

* `<naam>_anoniem.pdf` – de PDF met de gegevens echt verwijderd (niet alleen afgedekt);
* `<naam>_anoniem.md` – de tekst als Markdown. **Dit bestand is het beste om aan een AI te geven.**

### Opties

| Optie | Betekenis |
|---|---|
| `--namen namen.txt` | namen die zeker vervangen moeten worden, één per regel (bijv. alle gezinsleden); `= roepnaam` erachter kiest het label |
| `--behoud behoud.txt` | woorden die juist **niet** vervangen mogen worden (bijv. een bedrijfsnaam die als naam wordt gezien) |
| `--alle-datums` | vervang álle datums door het jaartal, niet alleen geboortedata |
| `--geen-ner` | geen taalmodel gebruiken, alleen patronen en `--namen` |
| `--alleen-tekst` / `--alleen-pdf` | maak maar één soort uitvoer |
| `--alle-tegenpartijen` | bankexports: vervang ook namen van bedrijven en winkels |
| `--sleutel` | schrijf ook `sleutel_N.json` met welk pseudoniem bij wie hoort. **Bevat persoonsgegevens – nooit aan een AI geven.** |

Tip: zet de namen van je gezinsleden in `namen.txt` en gebruik `--namen namen.txt`. Dat is
de betrouwbaarste manier om te zorgen dat ze altijd worden vervangen. (`namen.txt`,
PDF's en sleutelbestanden staan in `.gitignore`, zodat ze niet per ongeluk in git komen.)

## Hoe werkt het?

1. De tekst van elke pagina wordt met PyMuPDF uitgelezen, inclusief de positie van elk teken.
2. Namen worden gevonden via titels (`Dhr.`, `mevrouw`, …), velden (`Naam:`, `Achternaam:`, …),
   het Nederlandse spaCy-taalmodel en je eigen `--namen`-lijst. Over het hele document
   wordt bepaald welke vermeldingen bij dezelfde persoon horen (`Jan Jansen`, `J. Jansen`, `Jan`).
3. BSN's worden herkend met de elfproef (of als ze na "BSN" staan), IBAN's met de
   controlesom; geboortedata aan woorden als "geboren", "geb." of "Geboortedatum".
4. In de PDF wordt de originele tekst weggeredigeerd en vervangen door het label.
   Daarna controleert het script of de gevonden gegevens niet meer in de nieuwe PDF staan.

## Beperkingen – controleer altijd zelf!

* **Gescande PDF's** (afbeeldingen zonder tekst) worden niet geanonimiseerd; het script
  waarschuwt hiervoor. Laat zo'n bestand eerst door OCR halen.
* Naamherkenning is nooit 100%: ongebruikelijke namen of namen in HOOFDLETTERS zonder
  titel/veld kunnen gemist worden. Gebruik `--namen` voor de namen die er echt toe doen.
* Een datum zonder geboorte-aanduiding in de buurt (bijvoorbeeld in een tabel) blijft staan;
  het script meldt hoeveel. Gebruik dan `--alle-datums`.
* In PDF's worden polisnummers, klantnummers of kentekens niet herkend.
* Bankexports: alleen CSV/TXT. MT940, CAMT.053 (XML) en Excel worden (nog) niet ondersteund.
* Een map doorzoeken neemt `.pdf`, `.csv` en `.tsv` mee; een `.txt`-bestand alleen als je het
  zelf noemt (zodat `namen.txt` niet als bankexport wordt gezien).

## Tests

```bash
python -m pytest tests
```
