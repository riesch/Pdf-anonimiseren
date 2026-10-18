# Pdf-anonimiseren

Script om PDF-bestanden te anonimiseren, zodat je ze veilig(er) aan een AI kunt geven.

| Gegeven | Wordt |
|---|---|
| Namen | vaste pseudoniemen: `Persoon 1`, `Persoon 2`, … (dezelfde persoon krijgt overal hetzelfde label) |
| Alleen een achternaam die bij meerdere personen hoort (bijv. "Dhr. Jansen") | `Familie A` |
| BSN | `[BSN]` |
| Adressen (straat + huisnummer, postcode + plaats, velden als "Adres:") | `[adres]` |
| Geboortedata | alleen het geboortejaar, bijv. `1985` |
| IBAN / rekeningnummers | `[IBAN 1]`, `[IBAN 2]`, … (vast per rekening) |
| E-mailadressen en telefoonnummers | `[e-mail]` / `[telefoon]` |

Daarnaast worden metadata (auteur, titel), bladwijzers en bijlagen uit de PDF verwijderd,
worden ingevulde formuliervelden meegenomen, en wordt een naam in de bestandsnaam ook vervangen.

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
| `--namen namen.txt` | namen die zeker vervangen moeten worden, één per regel (bijv. alle gezinsleden) |
| `--behoud behoud.txt` | woorden die juist **niet** vervangen mogen worden (bijv. een bedrijfsnaam die als naam wordt gezien) |
| `--alle-datums` | vervang álle datums door het jaartal, niet alleen geboortedata |
| `--geen-ner` | geen taalmodel gebruiken, alleen patronen en `--namen` |
| `--alleen-tekst` / `--alleen-pdf` | maak maar één soort uitvoer |
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
* Andere gegevens zoals polisnummers, klantnummers of kentekens worden niet herkend.

## Tests

```bash
python -m pytest tests
```
