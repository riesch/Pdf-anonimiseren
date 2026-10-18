# Pdf-anonimiseren

Script om PDF's, Word-documenten, e-mails, bankexports en CAMT.053-afschriften te anonimiseren,
zodat je ze veilig(er) aan een AI kunt geven. Alles draait op je eigen computer.

## PDF-bestanden

| Gegeven | Wordt |
|---|---|
| Namen | alleen de voornaam: `Jan Pieter Jansen`, `J. Jansen` en `Dhr. Jansen` → `Jan` |
| Twee verschillende personen met dezelfde voornaam | `Jan` en `Jan (2)` |
| Persoon van wie geen voornaam bekend is (bijv. alleen `A. de Vries`) | `Persoon 1`, `Persoon 2`, … |
| Alleen een achternaam die bij meerdere personen hoort (bijv. "Dhr. Jansen") | `Familie A` |
| BSN | `[BSN]` |
| Adressen (straat + huisnummer, postcode + plaats, velden als "Adres:") | `[adres]` |
| Geboortedata | alleen het geboortejaar, bijv. `1985` (ook voluit, zoals in akten: "de tiende februari negentienhonderd en tachtig") |
| IBAN / rekeningnummers | bankcode + laatste 4 cijfers: `NL91 ABNA 0417 1643 00` → `ABNA4300` |
| Twee rekeningen bij dezelfde bank met dezelfde laatste 4 cijfers | `ABNA4300` en `ABNA4300 (2)` |
| Buitenlandse IBAN met cijfers als bankcode / oud rekeningnummer | `DE3000` / `****6789` |
| IBAN in kleine letters of over twee regels (`nl91abna…`, `NL91 ABNA 0417` ⏎ `1643 00`) | ook `ABNA4300` |
| E-mailadressen en telefoonnummers (ook buitenlands: `+32 …`, `0049 …`) | `[e-mail]` / `[telefoon]` |
| Overlijdensdatum | alleen het jaar |
| Kenteken (`12-ABC-3`, `12 ABC 3`) | `[kenteken]` |
| Klant-, polis-, dossier-, zaak-, patiënt-, personeels-, studentnummer | `[polisnummer]`, `[patiëntnummer]`, … |
| Btw-nummer (`NL123456789B01`, bevatte vroeger het BSN) | `[btw-nummer]` |
| IP-adres | `[ip-adres]` |
| Paspoort-, ID-kaart-, rijbewijsnummer | `[documentnummer]` |
| Foto's, handtekeningen, logo's (alleen met `--zonder-afbeeldingen`) | grijs vlak `[afbeelding verwijderd]` |

Het Markdown-bestand bevat nooit afbeeldingen; `--zonder-afbeeldingen` is alleen nodig als je de PDF zelf deelt.
Uit de PDF worden ook metadata, bladwijzers, bijlagen, paginaminiaturen en de
toegankelijkheidsstructuur verwijderd, want ook daarin kunnen de originele gegevens staan.

### Familie- en gezinsleden: familie.txt

In grote bestanden met veel namen (akten, dossiers, notulen) is het vaak alleen zinvol om je
eigen familie te anonimiseren. Zet je familieleden in `familie.txt`, in de map waar je het
script start. Dan worden **alleen zij** vervangen (ook als `J. Jansen`, `Dhr. Jansen`, …); andere
namen, zoals die van de notaris, blijven staan. BSN's, adressen, rekeningnummers enz. worden wel
bij iedereen vervangen.

```
Johannes Petrus Maria Jansen = Hans
    telefoon: 06-12345678
    werkgever: Voorbeeld Installatietechniek
Anna Wilhelmina de Vries
```

Onder een naam kun je gegevens van die persoon zetten. Telefoon, e-mail, kenteken en eigen
termen (werkgever, school, huisarts, …) typ je zelf; BSN, geboortedatum en adres voeg je toe met
`python anonimiseer.py --familie-toevoegen`, dat ze als onleesbare vingerafdruk opslaat. In
documenten wordt dat `Hans`, `[telefoon Hans]`, `[werkgever Hans]`, `[BSN Hans]`, `[adres Hans]`.
Staat er van iemand alleen een initiaal, dan vraagt het script om de voornaam en onthoudt het
antwoord.

**Alles over familie.txt, met voorbeelden, staat in [FAMILIE.md](FAMILIE.md).**

Met `--alle-namen` vervang je toch alle namen; met `--familie pad/naar/lijst.txt` gebruik je een
lijst op een andere plek.

### Notariële akten

Akten noemen alle voornamen voluit ("de heer Johannes Petrus Maria JANSEN"), vaak met de
achternaam in hoofdletters. Het script herkent dat en maakt er `Johannes` van. Verder:

| In de akte | Wordt |
|---|---|
| geboren te Amersfoort | geboren te `[geboorteplaats]` |
| wonende te Utrecht | wonende te `[woonplaats]` |
| paspoort/identiteitskaart/rijbewijs/reisdocument (nummer) … — ook "paspoortnummer", zonder "nummer" of op de volgende regel | `[documentnummer]` |
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
| Rekeningnummers | bankcode + laatste 4 cijfers (`ABNA4300`), uniek per rekening, dus terugkerende betalingen blijven herkenbaar |
| Privépersonen als tegenpartij (`HR J JANSEN EO`, `J. Jansen`) | de voornaam als die bekend is (via `namen.txt`), anders `Persoon 1`, … – ook in de omschrijving |
| Bedrijven (Albert Heijn, Eneco, Gemeente, …) | blijven staan; met `--alle-tegenpartijen` → `[tegenpartij 1]` |
| Machtigingskenmerk, betalingskenmerk, transactiereferentie | `[kenmerk]` |
| Pasnummers, klant-/polis-/contractnummers in omschrijvingen | `[pasnummer]` / `[klantnummer]` |
| BSN, adressen, e-mail, telefoon in omschrijvingen | zoals bij PDF's |

Het resultaat is weer een CSV/TXT met dezelfde kolommen (`<naam>_anoniem.csv`), zo opgeslagen
dat Excel letters als `ë` goed toont. Ook een rekeningnummer in de bestandsnaam wordt vervangen.

Of een tegenpartij een persoon of een bedrijf is, wordt geschat: woorden als *B.V.*,
*Stichting*, *Gemeente*, cijfers of bekende winkelnamen wijzen op een bedrijf; titels
(*HR*, *MW*) of initialen + achternaam op een persoon. Namen die ook een persoonsnaam kunnen zijn
(Dirk, Zeeman, Leen Bakker, …) staan bewust niet in de lijst met winkels: liever een winkel te veel
vervangen dan een persoon niet. Met `--toon-tegenpartijen` zie je **alleen in de terminal** welke
tegenpartijen als privépersoon zijn behandeld; staat er een bedrijf tussen, zet het dan in
`behoud.txt`. Voornamen die alleen in een vrije omschrijving staan
("cadeau voor Sanne") worden alleen vervangen als ze in `namen.txt` staan.

## CAMT.053-afschriften (XML)

CAMT.053 is het XML-formaat voor bankafschriften dat alle Nederlandse banken aanbieden.
Een los `.xml`-bestand of een `.zip` met meerdere afschriften gaat met hetzelfde commando:

```bash
python anonimiseer.py afschrift.xml
python anonimiseer.py afschriften_2024.zip
```

Het resultaat is weer geldige CAMT.053-XML (of een ZIP), zodat andere software het nog kan
inlezen. Elk veld wordt behandeld naar zijn betekenis in de standaard:

| Veld | Wordt |
|---|---|
| IBAN en ander rekeningnummer | bankcode + laatste 4 cijfers (`ABNA4300`), uniek per rekening |
| Naam van rekeninghouder of tegenpartij (privépersoon) | voornaam, of `Persoon 1` |
| Naam van een bedrijf of bank | blijft staan (met `--alle-tegenpartijen`: `[tegenpartij 1]`) |
| Adres van een privépersoon (`PstlAdr`) | `[adres]`; adressen van bedrijven blijven staan |
| Geboortedatum en -plaats (`BirthDt`, `CityOfBirth`) | jaar / `[geboorteplaats]` |
| Telefoon, e-mail, persoonlijk ID (`PrvtId`) | `[telefoon]`, `[e-mail]`, `[id]` |
| EndToEndId, machtigingskenmerk, betalingskenmerk | `[kenmerk]` |
| Omschrijving (`Ustrd`, `AddtlTxInf`, …) | zoals vrije tekst bij CSV-exports |
| Datums, bedragen, BIC's, transactiecodes, incassant-ID's | blijven staan |

Staat de rekeninghouder alleen als `HR J JANSEN` in het afschrift, maar ergens anders voluit
(`groet Jan Jansen`), dan gebruikt het script die voornaam en hoeft het niet te vragen.
Bij een ZIP worden alle afschriften samen bekeken, zodat dezelfde persoon of rekening overal
hetzelfde label krijgt.

## Gescande PDF's en tabellen

**Ingevoegde scans.** Ook een afbeelding op een digitale pagina, zoals een kopie van een
identiteitsbewijs onder een brief, wordt met OCR gelezen en geanonimiseerd. Kleine logo's en
iconen worden overgeslagen. Kan een afbeelding niet gelezen worden (geen Tesseract of
`--geen-ocr`), dan telt dat als lek en krijgt het bestand `_CONTROLEREN`.

**Scans.** Een pagina die alleen uit een afbeelding bestaat (een scan zonder tekstherkenning)
wordt automatisch met OCR gelezen, in het Nederlands en Engels. Daarna wordt hij net zo
geanonimiseerd als een digitale pagina: de gegevens worden in de afbeelding zelf
weggepoetst en vervangen door het label. Ter controle wordt de geanonimiseerde pagina nog een
keer met OCR gelezen. Tekstherkenning kan fouten maken, bijvoorbeeld bij een slechte scan of
handschrift; het controlerapport noemt daarom elke OCR-pagina om extra goed na te kijken.

Hiervoor is het gratis programma **Tesseract** nodig (zie Installatie). Zonder Tesseract, of met
`--geen-ocr`, worden scanpagina's niet geanonimiseerd en krijgt het bestand `_CONTROLEREN`.

**Tabellen.** Tabellen met lijnen in digitale PDF's (loonstroken, jaaropgaven, overzichten)
komen als echte Markdown-tabel in het `.md`-bestand, zodat een AI weet welk getal in welke
kolom staat:

```
| Naam | Geboortedatum | BSN |
|---|---|---|
| Richard | 1980 | [BSN] |
```

Een kolom met kop *Naam*, *Achternaam* of *Voornamen* wordt als naam behandeld en een kolom met
kop *Geboren* of *Geboortedatum* als geboortedatum, ook al staat dat woord niet vlak voor elke
cel. Tekst die binnen het tabelvak maar buiten de cellen staat (bijv. een voetnoot) komt onder
de tabel. Tabellen zonder lijnen en tabellen in scans blijven gewone tekst.

## Word-documenten en e-mails

```bash
python anonimiseer.py brief.docx
python anonimiseer.py bericht.eml        # sleep een bericht uit Apple Mail naar de Finder: dat geeft een .eml
```

De uitvoer is een `…_anoniem.md`-bestand met controlerapport (geen nieuw Word-document).

* **Word:** de tekst (tabellen als Markdown-tabel) en apart de kop- en voetteksten, voetnoten en
  opmerkingen, want ook daarin staan vaak namen.
* **E-mail:** afzender, ontvangers, onderwerp, datum en de tekst. Bijlagen worden alleen bij naam
  genoemd: sla ze op en verwerk ze apart.

## Controle na het anonimiseren

Na het anonimiseren kijkt het script de **uitvoer** nog eens na.

**Lekcheck.** Het zoekt naar gegevens van mensen uit je familie- of namenlijst die er niet meer
in mogen staan: hun achternaam (ook in kleine letters, zoals `fam. jansen`), hun andere
voornamen, en hun BSN, adres of geboortedatum. Ook gegevens die in de PDF bleven staan en
gescande pagina's tellen als lek. Ook de bestandsnaam wordt zo gecontroleerd. Bij een lek krijgt het bestand `_CONTROLEREN` in de naam
(`brief_anoniem_CONTROLEREN.md`), zodat je het niet per ongeluk deelt:

```
[1] brief.pdf: 1x geboortedatum, 3x naam
     -> geanonimiseerd/brief_anoniem_CONTROLEREN.md
     !! 1 lek(ken) gevonden: bestand gemarkeerd met _CONTROLEREN. NIET delen voordat je het rapport hebt bekeken.
     ?  2 verdachte plek(ken) om even na te kijken
     controlerapport: geanonimiseerd/controle/brief_controle.md
```

**Controlerapport.** Per bestand komt in `geanonimiseerd/controle/` een rapport met wat er is
vervangen, de lekken en de *verdachte plekken*: dingen die een persoonsgegeven kúnnen zijn,
elk met de regel eromheen en het verdachte stuk tussen `»…«`:

- een naam na een titel (`dhr. Bakker`) of een naam die het taalmodel herkent (niet in familiemodus,
  want daar blijven andere namen bewust staan);
- de achternaam van een familielid als deel van een woord (`www.richardjansen.nl`), of in kleine
  letters als die achternaam ook een gewoon woord is (`groot`, `bakker`);
- een nummer van 9 cijfers (een BSN met een tikfout?), iets dat op een rekeningnummer lijkt;
- een volledige datum (een geboortedatum zonder "geboren" ervoor?). Niet bij bankdata.

Het rapport bevat alleen tekst uit de uitvoer, nooit de originele gegevens, maar deel het niet
met een AI. Met `--geen-rapport` wordt het niet geschreven (de lekcheck blijft werken).

## Installatie

Je hebt **Python 3.10 tot en met 3.14** nodig; **3.13 wordt aangeraden**. De Python die op een Mac
standaard meegeleverd wordt (`/usr/bin/python3`) is 3.9 en te oud, en voor Python 3.15 is spaCy
(naamherkenning) nog niet beschikbaar. Installeer daarom Python 3.13 via Homebrew
(`brew install python@3.13`) en maak de omgeving met die versie:

```bash
/opt/homebrew/bin/python3.13 -m venv .venv   # Intel-Mac: /usr/local/bin/python3.13
source .venv/bin/activate                    # Windows: .venv\Scripts\activate
pip install --upgrade pip
pip install -r requirements.txt
python -m spacy download nl_core_news_md   # optioneel, maar aanbevolen
```

Voor gescande PDF's ook Tesseract (met Nederlands):

| Systeem | Installeren |
|---|---|
| macOS | `brew install tesseract tesseract-lang` (met [Homebrew](https://brew.sh)) |
| Windows | installatieprogramma van [UB Mannheim](https://github.com/UB-Mannheim/tesseract/wiki); vink *Dutch* aan bij de talen |
| Linux | `sudo apt install tesseract-ocr tesseract-ocr-nld` |

## Gebruik

```bash
cd ~/pdf-anonimiseren && source .venv/bin/activate
python anonimiseer.py brief.pdf                     # één bestand
python anonimiseer.py map_met_documenten/           # een hele map
```

Bij meerdere bestanden volgt aan het eind een samenvatting: hoeveel bestanden schoon zijn,
welke verdachte plekken hebben en welke `_CONTROLEREN` kregen.

### Finder-snelactie (Mac)

Met een snelactie kun je in Finder met de rechtermuisknop op een bestand of map kiezen voor
**Anonimiseer**, zonder terminal:

1. Open **Automator** en kies *Nieuw document* → **Snelle taak**.
2. Zet bovenaan *Werkstroom ontvangt* op **bestanden of mappen** in **Finder**.
3. Sleep de actie **Voer shellscript uit** erin. Kies *Shell*: `/bin/bash`, *Geef invoer door*: **als argumenten**.
4. Vervang de tekst door:
   ```bash
   "$HOME/pdf-anonimiseren/mac/anonimiseer-snelactie.sh" "$@"
   ```
5. Bewaar als **Anonimiseer**.

Je krijgt een melding als het klaar is en de map `geanonimiseerd` gaat open. Moet er iets
nagekeken worden, dan opent ook het verslag (`~/.pdf-anonimiseren/laatste_run.txt`). Een snelactie
kan niets vragen: ontbrekende voornamen worden dan niet gevraagd.

Per PDF krijg je in een map `geanonimiseerd/` **naast het bronbestand** (met `-o map` kies je
zelf een andere plek):

* `<naam>_anoniem.pdf` – de PDF met de gegevens echt verwijderd (niet alleen afgedekt);
* `<naam>_anoniem.md` – de tekst als Markdown. **Dit bestand is het beste om aan een AI te geven.**

### Opties

| Optie | Betekenis |
|---|---|
| `--namen namen.txt` | namen die zeker vervangen moeten worden, één per regel (bijv. alle gezinsleden); `= roepnaam` erachter kiest het label |
| `--familie-toevoegen` | met vragen een familielid toevoegen (BSN, geboortedatum, adres, telefoon, e-mail, kenteken, eigen termen) |
| `--sleutel-exporteren pad` | reservekopie van de geheime sleutel (zie [FAMILIE.md](FAMILIE.md)) |
| `--sleutel-naar-sleutelhanger [pad]` | sleutel(bestand of reservekopie) in de sleutelhanger zetten |
| `--familie lijst.txt` | alleen deze familie-/gezinsleden vervangen (standaard: `familie.txt` in de huidige map, de scriptmap of `~/.pdf-anonimiseren/`) |
| `--alle-namen` | alle namen vervangen, ook als er een `familie.txt` is |
| `--niet-vragen` | niet om ontbrekende voornamen vragen |
| `--zonder-afbeeldingen` | foto's, handtekeningen en logo's uit de PDF halen |
| `--geen-ocr` | gescande pagina's niet met OCR lezen |
| `--geen-rapport` | geen controlerapport schrijven (lekcheck en `_CONTROLEREN` blijven werken) |
| `--behoud behoud.txt` | woorden die juist **niet** vervangen mogen worden (bijv. een bedrijfsnaam die als naam wordt gezien) |
| `--alle-datums` | vervang álle datums door het jaartal, niet alleen geboortedata |
| `--geen-ner` | geen taalmodel gebruiken, alleen patronen en `--namen` |
| `--alleen-tekst` / `--alleen-pdf` | maak maar één soort uitvoer |
| `--alle-tegenpartijen` | bankexports: vervang ook namen van bedrijven en winkels |
| `--toon-tegenpartijen` | bankexports: toon in de terminal welke tegenpartijen als privépersoon zijn behandeld |
| `--sleutel` | schrijf ook een sleutelbestand met welk pseudoniem bij wie hoort, in `~/.pdf-anonimiseren/sleutelbestanden/` (nooit naast de uitvoer). **Bevat echte persoonsgegevens – nooit delen.** |

Tip: zet de namen van je gezinsleden in `namen.txt` en gebruik `--namen namen.txt`. Dat is
de betrouwbaarste manier om te zorgen dat ze altijd worden vervangen. (`namen.txt`,
PDF's en sleutelbestanden staan in `.gitignore`, zodat ze niet per ongeluk in git komen.)

## Opbouw van de code

| Bestand | Wat |
|---|---|
| `anonimiseer.py` | hoofdlogica en opdrachtregel |
| `patronen.py` | BSN, IBAN, telefoon, e-mail, datums, adressen, kentekens, documentnummers |
| `namen.py` | namen herkennen, personen koppelen, labels |
| `pdf.py` | PDF lezen (ook OCR), tabellen, wegpoetsen, Markdown |
| `tekstbestanden.py` | Word en e-mail lezen |
| `bankdata.py`, `camt.py` | bankexports en CAMT.053 |
| `familie.py` | familie.txt, vingerafdrukken, sleutel |
| `controle.py` | lekcheck en controlerapport |

## Hoe werkt het?

1. De tekst van elke pagina wordt met PyMuPDF uitgelezen, inclusief de positie van elk teken.
2. Namen worden gevonden via titels (`Dhr.`, `mevrouw`, …), velden (`Naam:`, `Achternaam:`, …),
   het Nederlandse spaCy-taalmodel en je eigen `--namen`-lijst. Over het hele document
   wordt bepaald welke vermeldingen bij dezelfde persoon horen (`Jan Jansen`, `J. Jansen`, `Jan`).
3. BSN's worden herkend met de elfproef (of als ze na "BSN" staan), IBAN's met de
   controlesom; geboortedata aan woorden als "geboren", "geb." of "Geboortedatum".
4. In de PDF wordt de originele tekst weggeredigeerd en vervangen door het label.
5. Daarna wordt de uitvoer gecontroleerd op lekken en verdachte plekken (zie hierboven).

## Beperkingen – controleer altijd zelf!

* **Gescande PDF's** worden met OCR gelezen (als Tesseract geïnstalleerd is). OCR kan
  letters verkeerd lezen; een verkeerd gelezen naam wordt dan niet herkend. Kijk OCR-pagina's
  daarom extra goed na.
* Naamherkenning is nooit 100%: ongebruikelijke namen of namen in HOOFDLETTERS zonder
  titel/veld kunnen gemist worden. Gebruik `--namen` voor de namen die er echt toe doen.
* Een datum zonder geboorte-aanduiding in de buurt (bijvoorbeeld in een tabel) blijft staan;
  het script meldt hoeveel. Gebruik dan `--alle-datums`.
* Niet herkend: medische gegevens (diagnoses, medicijnen), inkomens- en salarisgegevens,
  leeftijden ("43 jaar"), beroep/werkgever, gebruikersnamen en social-media-accounts, en
  handgeschreven tekst. Lees daar zelf op na als dat in je documenten staat.
* Bankexports: CSV/TXT en CAMT.053. MT940 en Excel worden (nog) niet ondersteund.
* Bijlagen van e-mails worden niet automatisch verwerkt.
* Een map doorzoeken neemt `.pdf`, `.docx`, `.eml`, `.csv`, `.tsv`, `.xml` en `.zip` mee; een
  `.txt`-bestand alleen als je het zelf noemt (zodat `namen.txt` niet als bankexport wordt gezien).

## Tests

```bash
pip install -r requirements-dev.txt
python -m pytest tests
```

Bij elke push naar GitHub draaien de tests ook automatisch (zie het tabblad *Actions*).
