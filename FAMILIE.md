# familie.txt – handleiding met voorbeelden

In `familie.txt` leg je vast wie je familie- en gezinsleden zijn en welke gegevens van hen
herkend moeten worden. Het script vervangt die gegevens dan door een label met de voornaam,
zoals `Hans`, `[BSN Hans]` of `[werkgever Hans]`.

Alle namen en gegevens in deze handleiding zijn verzonnen.

## Waar staat het bestand?

Het script zoekt `familie.txt` op deze plekken, in deze volgorde, en gebruikt de eerste die
bestaat:

1. de map waar je het script start;
2. de map van het script zelf (meestal `~/pdf-anonimiseren`);
3. `~/.pdf-anonimiseren/familie.txt`.

Zet hem dus het liefst in `~/pdf-anonimiseren`; dan wordt hij altijd gevonden, ook via de
Finder-snelactie:

```bash
cd ~/pdf-anonimiseren
source .venv/bin/activate
python anonimiseer.py "pad/naar/document.pdf"
```

Het script meldt bij elke run bovenaan dat het de lijst gebruikt:

```
Familielijst familie.txt: alleen deze personen worden vervangen (--alle-namen om alle namen te vervangen).
```

Wordt geen lijst gevonden, dan meldt het script dat (en waar het gezocht heeft) en vervangt het
alle namen. Staat de lijst ergens anders, gebruik dan `--familie pad/naar/familie.txt`.

`familie.txt` staat in `.gitignore` en komt dus nooit in git of op GitHub. Geef het bestand ook
niet aan een AI.

## Opbouw in één oogopslag

```
# Regels die met # beginnen zijn commentaar.

Johannes Petrus Maria Jansen = Hans
    telefoon: 06-12345678
    e-mail: hans@voorbeeld.nl
    werkgever: Voorbeeld Installatietechniek
    bsn: 86c23bd8b3a77d66cb34c0213cb5a9cf
    adres: 3f306474947f1bed06c34e0ecab70aa5   # straat + huisnummer

Anna Wilhelmina de Vries
    kenteken: 12-ABC-3
    huisarts: Praktijk De Linde

J.P. Bakker = Jan
```

- **Een regel zonder spaties ervoor** is een persoon.
- **Ingesprongen regels** (met spaties ervoor) zijn gegevens van de persoon erboven.
- **`= Hans`** achter een naam is de roepnaam. Die wordt het label in plaats van de eerste voornaam.
- **Leesbare regels** (`telefoon:`, `e-mail:`, `werkgever:`) typ je zelf of laat je vragen.
- **Vingerafdrukken** (`bsn:`, `geboortedatum:`, `adres:` met een lange code) maakt het script zelf
  via `--familie-toevoegen`. Typ of wijzig die niet met de hand.

Met dit bestand wordt bijvoorbeeld:

| In het document | Wordt |
|---|---|
| De heer J.P.M. JANSEN | De heer Hans |
| tel. +31 6 1234 5678 | tel. `[telefoon Hans]` |
| werkt bij Voorbeeld Installatietechniek | werkt bij `[werkgever Hans]` |
| Mevrouw A.W. de Vries rijdt in 12ABC3 | Mevrouw Anna rijdt in `[kenteken Anna]` |
| huisarts: Praktijk De Linde | huisarts: `[huisarts Anna]` |
| Dhr. J.P. Bakker belde | Dhr. Jan belde |
| Notaris: mr. K. Visser | blijft staan (geen familie) |
| info@notaris.nl | `[e-mail]` (e-mailadressen van anderen worden altijd vervangen) |

## 1. Personen toevoegen

Typ de naam zoals hij in documenten staat, **liefst met alle voornamen**. Eén persoon per regel.

| Regel in familie.txt | Label in documenten | Opmerking |
|---|---|---|
| `Richard Pieter Jansen` | `Richard` | eerste voornaam wordt het label |
| `Johannes Petrus Maria Jansen = Hans` | `Hans` | roepnaam die niet in de voornamen staat |
| `Anna Wilhelmina de Vries` | `Anna` | tussenvoegsels mogen gewoon |
| `J.P. Bakker = Jan` | `Jan` | ken je alleen initialen, geef dan een roepnaam |
| `J.P. Bakker` | `Persoon 1` | zonder roepnaam is er geen voornaam bekend |
| `Anna Jansen-de Vries` | `Anna` | dubbele achternaam |
| `Anna Wilhelmina de Vries \| Anna Jansen-de Vries = Anna` | `Anna` | twee namen van één persoon, bijv. geboortenaam en getrouwde naam, gescheiden door `\|` |

Het script herkent daarna ook andere schrijfwijzen van die persoon: `J. Jansen`, `J.P.M. Jansen`,
`JANSEN`, `Jansen, J.P.M.`, `Dhr. Jansen` en losse voornamen als `Pieter`.

**Dubbele achternaam:** het streepje mag ontbreken, in familie.txt én in het document:
`Anna Jansen Bakker` vindt ook `A. Jansen-Bakker`, zolang die naam ergens met streepje staat.
Staat in familie.txt maar één deel (`Anna Bakker`), dan wordt `A. Jansen-Bakker` toch aan
Anna gekoppeld als de eerste voornaam of voorletter klopt. De spelling moet wel gelijk zijn;
het script raadt niet naar tikfouten. Twijfel je, zet dan beide vormen op één regel:
`Anna Jansen-Bakker | Anna Bakker = Anna`.

Iemand met **dezelfde achternaam** als een familielid wordt ook vervangen, anders zou de
familienaam alsnog in het document staan. Hebben meerdere familieleden dezelfde achternaam,
dan wordt een losse achternaam ("Dhr. Jansen") `Familie A`, omdat niet duidelijk is wie bedoeld is.

## 2. Leesbare gegevens zelf typen: telefoon, e-mail, kenteken, eigen termen

Zet ze **ingesprongen** (met spaties ervoor) onder de naam, in de vorm `soort: waarde`:

```
Richard Pieter Jansen
    telefoon: 06-12345678
    telefoon: 030-1234567
    e-mail: richard@voorbeeld.nl
    kenteken: 12-ABC-3
    werkgever: Voorbeeld Installatietechniek
    school: Basisschool De Regenboog
    huisarts: Praktijk De Linde
    sportclub: Hockeyclub Voorbeeld
```

| Soort | Ook goed | Herkend in documenten als | Wordt |
|---|---|---|---|
| `telefoon` | `tel`, `mobiel`, `telefoonnummer` | `06 1234 5678`, `+31 6 12345678`, `0031-6-12345678`; buitenlands: `+32 470 12 34 56` | `[telefoon Richard]` |
| `e-mail` | `email`, `mail` | ook met hoofdletters | `[e-mail Richard]` |
| `kenteken` | | `12-ABC-3`, `12 ABC 3`, `12ABC3` | `[kenteken Richard]` |
| elk ander woord | | dezelfde woorden, hoofdletters en leestekens maken niet uit | `[werkgever Richard]`, `[school Richard]`, … |

Tips:

- Meerdere telefoonnummers of e-mailadressen? Gewoon meerdere regels.
- Een eigen term wordt herkend als precies die woorden achter elkaar staan. Schrijf de term zoals
  hij in documenten voorkomt; zet varianten op een eigen regel
  (`werkgever: Voorbeeld B.V.` en `werkgever: Voorbeeld Installatietechniek`).
- Het woord vóór de dubbele punt wordt het label. Kies een kort, duidelijk woord.
- `bsn`, `geboortedatum` en `adres` kun je **niet** leesbaar typen; die gaan via de vragen (stap 3).

## 3. BSN, geboortedatum en adres: via vragen

**Let op:** BSN's worden **alleen** vervangen als ze van een familielid zijn en zo zijn
toegevoegd. Andere BSN's (bijvoorbeeld van een getuige of notaris) blijven staan; het
controlerapport meldt nummers van 9 cijfers wel als verdacht, zodat je ze kunt nakijken.

Deze gegevens worden niet leesbaar opgeslagen, maar als vingerafdruk. Laat het script ze daarom
zelf toevoegen:

```bash
python anonimiseer.py --familie-toevoegen
```

Voorbeeld van een vragenronde:

```
  Naam (alle voornamen en achternaam): Richard Pieter Jansen
  Roepnaam (Enter = eerste voornaam):
  BSN: 111222333
  Geboortedatum (bijv. 10-02-1980 of 10 februari 1980): 10 februari 1980
  Adres 1 – straat (Enter = klaar): Kerkstraat
    Huisnummer + toevoeging (bijv. 12a): 12a
    Postcode: 3512 JK
  Adres 2 – straat (Enter = klaar):
  Telefoonnummer (Enter = klaar): 06-12345678
  Telefoonnummer (Enter = klaar):
  E-mailadres (Enter = klaar):
  Kenteken (Enter = klaar):
  Overige termen die naar deze persoon verwijzen, zoals werkgever, school of huisarts.
    Term (Enter = klaar): Voorbeeld Installatietechniek
    Label (bijv. werkgever): werkgever
    Term (Enter = klaar):

  Opgeslagen in familie.txt.
Nog iemand toevoegen? (j/n) n
```

Elke vraag kun je overslaan met Enter. Het resultaat in `familie.txt`:

```
Richard Pieter Jansen
    bsn: 86c23bd8b3a77d66cb34c0213cb5a9cf
    geboortedatum: b51d6a4923e893868232c0ac2835899c
    adres: 3f306474947f1bed06c34e0ecab70aa5   # straat + huisnummer
    adres: a528d6e21eab81d298f13032738ef056   # postcode + huisnummer
    adres: 6e058168252108c7bcfd2468c9aedb80   # postcode
    telefoon: 06-12345678
    werkgever: Voorbeeld Installatietechniek
```

| In het document | Wordt |
|---|---|
| het BSN van Richard | `[BSN Richard]` |
| `Kerkstraat 12a`, `Kerkstr. 12 A`, `3512 JK` | `[adres Richard]` |
| zijn geboortedatum, in elke schrijfwijze (ook voluit, ook zonder "geboren") | `1980` |
| een ander BSN | blijft staan: BSN's worden **alleen** via `familie.txt` vervangen |
| een andere geboortedatum, ook na "geboren" | blijft staan: met `familie.txt` worden **alleen** de geboortedata van familieleden ingekort |
| een ander adres | `[adres]` |

Geboortedatum kun je typen als `10-02-1980`, `10/02/1980`, `10 februari 1980`, `10 feb. 1980`
of voluit (`tien februari negentienhonderdtachtig`).

### De geheime sleutel

De vingerafdrukken worden gemaakt met een geheime sleutel. Die staat in de Sleutelhanger van je
Mac (naam `pdf-anonimiseren`), of in `~/.pdf-anonimiseren/sleutel` als er geen sleutelhanger is.
Wie alleen `familie.txt` heeft, kan de gegevens er niet uit terughalen.

**Maak een reservekopie**, apart van `familie.txt`, bijvoorbeeld op een usb-stick:

```bash
python anonimiseer.py --sleutel-exporteren /Volumes/USB/sleutel-kopie
```

Op een andere computer zet je die kopie in de sleutelhanger met
`python anonimiseer.py --sleutel-naar-sleutelhanger /Volumes/USB/sleutel-kopie`. Zonder de juiste
sleutel worden BSN, geboortedatum en adres niet meer herkend; het script waarschuwt dan. Leesbare
gegevens (telefoon, e-mail, …) werken altijd, ook zonder sleutel.

## 4. Later aanvullen, wijzigen of verwijderen

- **Aanvullen:** zet een nieuwe ingesprongen regel onder de naam, of voer bij
  `--familie-toevoegen` dezelfde naam opnieuw in. Gegevens bij dezelfde naam worden
  samengevoegd, ook als de roepnaam er de ene keer wel en de andere keer niet bij staat.
- **Wijzigen:** pas een leesbare regel gewoon aan. Een vingerafdruk kun je niet aanpassen;
  verwijder de regel en voeg het gegeven opnieuw toe via `--familie-toevoegen`.
- **Verwijderen:** haal de regel weg. Bij adressen horen drie regels (straat + huisnummer,
  postcode + huisnummer, postcode); het commentaar erachter laat zien welke welke is.

## 5. Ontbrekende voornaam

Staat in een document alleen `A. de Vries` en is er geen voornaam bekend, dan vraagt het script:

```
  ? Geen voornaam gevonden voor 'A. de Vries' (brief.pdf, pagina 2).
    Voornaam (Enter = overslaan, - = niet meer vragen): Anna
```

Het antwoord komt onderaan `familie.txt` te staan als `A. de Vries = Anna`, zodat het de volgende
keer niet opnieuw gevraagd wordt. Met `--niet-vragen` vraagt het script niets.

## Veelgemaakte fouten

| Fout | Gevolg | Goed |
|---|---|---|
| Gegevensregel zonder spaties ervoor: `telefoon: 06-…` | wordt gelezen als een (ongeldige) naam en genegeerd | `    telefoon: 06-…` |
| `familie.txt` op een plek waar het script niet zoekt | niet gevonden: alle namen worden vervangen (het script meldt dat) | zet hem in `~/pdf-anonimiseren`, of gebruik `--familie pad` |
| Dezelfde persoon op twee regels met een andere achternaam (`Anna de Vries` en `Anna Jansen`) | twee personen: `Anna` en `Anna (2)` | één regel: `Anna de Vries \| Anna Jansen = Anna` |
| BSN, adres of geboortedatum leesbaar typen: `    bsn: …` | wordt niet gebruikt; het script waarschuwt (met regelnummer) | `--familie-toevoegen` |
| Een gegevensregel zonder `soort:` ervoor | wordt niet begrepen; het script waarschuwt | `    telefoon: 06-…` |
| Sleutel kwijt | BSN, geboortedatum en adres worden niet meer herkend | reservekopie met `--sleutel-exporteren` |

## Beperkingen

- Een eigen term wordt alleen herkend als de woorden precies zo achter elkaar staan; afkortingen
  of spelfouten in het document worden niet herkend.
- In een gescande PDF hangt alles af van de tekstherkenning (OCR): een verkeerd gelezen letter
  betekent dat een naam of nummer niet herkend wordt. Kijk het controlerapport altijd na.
