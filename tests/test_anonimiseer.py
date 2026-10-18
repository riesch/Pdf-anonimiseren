"""Tests met uitsluitend verzonnen gegevens."""

import sys
from pathlib import Path

import pymupdf
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import anonimiseer as an  # noqa: E402


@pytest.fixture(scope="module")
def anon():
    return an.Anonimiseerder(gebruik_ner=False)


def test_elfproef():
    assert an.elfproef("111222333")
    assert not an.elfproef("111222334")


def test_iban():
    assert an.iban_geldig("NL91 ABNA 0417 1643 00")
    assert not an.iban_geldig("NL91 ABNA 0417 1643 01")


def test_bsn_en_iban(anon):
    uit = anon.anonimiseer_tekst("BSN: 111.222.333, rekening NL91ABNA0417164300 en NL91 ABNA 0417 1643 00.")
    assert "BSN: 111.222.333" in uit   # BSN alleen via familie.txt
    assert uit.count("ABNA4300") == 2


def test_iban_bankcode_en_laatste_cijfers(anon):
    uit = anon.anonimiseer_tekst("NL91ABNA0417164300, NL39RABO0000004300 en DE89370400440532013000")
    assert uit == "ABNA4300, RABO4300 en DE3000"


def test_iban_zelfde_bank_en_laatste_cijfers_blijven_verschillend(anon):
    uit = anon.anonimiseer_tekst("NL91ABNA0417164300, NL49ABNA1000004300 en weer NL91ABNA0417164300")
    assert uit == "ABNA4300, ABNA4300 (2) en weer ABNA4300"


def test_geboortedatum_wordt_jaar(anon):
    uit = anon.anonimiseer_tekst("Geboortedatum: 12-03-1985\nDatum brief: 14-02-2024")
    assert "Geboortedatum: 1985" in uit
    assert "14-02-2024" in uit  # gewone datum blijft staan
    uit = an.Anonimiseerder(gebruik_ner=False, alle_datums=True).anonimiseer_tekst("Datum: 14-02-2024")
    assert uit == "Datum: 2024"


@pytest.mark.parametrize("tekst", [
    "Kerkstraat 12a", "Burgemeester de Withstraat 5", "Laan van Meerdervoort 120",
    "1234 AB Utrecht", "2511 CV Den Haag",
])
def test_adressen(anon, tekst):
    assert anon.anonimiseer_tekst(f"Woont op {tekst}.") == "Woont op [adres]."


def test_namen_consistent(anon):
    tekst = ("Naam: Jan Jansen\nGeachte heer Jansen,\nUw partner mevrouw A. de Vries "
             "en J. Jansen. Jan belt.")
    uit = anon.anonimiseer_tekst(tekst)
    assert "Jansen" not in uit and "Vries" not in uit
    assert uit == ("Naam: Jan\nGeachte heer Jan,\nUw partner mevrouw Persoon 1 "
                   "en Jan. Jan belt.")   # van "A. de Vries" is geen voornaam bekend


def test_zelfde_achternaam_wordt_familie(anon):
    uit = anon.anonimiseer_tekst("Naam: Jan Jansen\nNaam kind: Piet Jansen\nDhr. Jansen")
    assert uit == "Naam: Jan\nNaam kind: Piet\nDhr. Familie A"


def test_zelfde_voornaam_krijgt_volgnummer(anon):
    uit = anon.anonimiseer_tekst("Naam: Jan Jansen\nNaam partner: Jan de Vries")
    assert uit == "Naam: Jan\nNaam partner: Jan (2)"


def test_roepnaam_uit_namenlijst():
    a = an.Anonimiseerder(namen=["Johannes Petrus Jansen = Hans"], gebruik_ner=False)
    assert a.anonimiseer_tekst("Johannes Petrus JANSEN en Hans Jansen") == "Hans en Hans"


def test_notariele_akte():
    a = an.Anonimiseerder(gebruik_ner=False)
    uit = a.anonimiseer_tekst(
        "de heer Johannes Petrus Maria JANSEN, geboren te Amersfoort op vijf april "
        "negentienhonderdvijfentachtig, wonende te Utrecht, zich legitimerende met paspoort "
        "nummer NW1234567. Het woonhuis, kadastraal bekend gemeente Utrecht, sectie A, nummer 1234. "
        "De heer Jansen tekent.")
    assert uit == (
        "de heer Johannes, geboren te [geboorteplaats] op 1985, wonende te [woonplaats], zich "
        "legitimerende met paspoort nummer [documentnummer]. Het woonhuis, kadastraal bekend "
        "[kadastrale aanduiding]. De heer Johannes tekent.")


def test_datum_in_woorden():
    assert an.jaar_in_woorden("negentienhonderdvijfentachtig") == 1985
    assert an.jaar_in_woorden("tweeduizend") == 2000
    assert an.woord_naar_getal("drieëntwintig") == 23
    assert an.woord_naar_getal("negen en twintig") == 29
    assert [an.woord_naar_getal(w) for w in ("eerste", "derde", "tiende", "achtste", "eenentwintigste", "één")] \
        == [1, 3, 10, 8, 21, 1]
    assert an.jaar_in_woorden("negentienhonderd en tachtig") == 1980


def test_akte_datum_blijft_geboortedatum_wordt_jaar():
    a = an.Anonimiseerder(gebruik_ner=False)
    assert a.anonimiseer_tekst(
        "Heden, de eenentwintigste maart tweeduizend vierentwintig, verschenen: geboren te Zeist op "
        "de tiende februari negentienhonderd en tachtig.") == (
        "Heden, de eenentwintigste maart tweeduizend vierentwintig, verschenen: geboren te "
        "[geboorteplaats] op 1980.")


def test_behoud_en_extra_namen():
    a = an.Anonimiseerder(namen=["Karin Visser"], behoud={"Bakker"}, gebruik_ner=False)
    uit = a.anonimiseer_tekst("Groet, Karin Visser. Mevr. Bakker belde.")
    assert "Visser" not in uit and "Bakker" in uit


def test_pdf(tmp_path, anon):
    bron = tmp_path / "Jan Jansen brief.pdf"
    doc = pymupdf.open()
    doc.new_page().insert_text((40, 60), "Naam: Jan Jansen\nBSN: 111222333\nAdres: Kerkstraat 1", fontsize=11)
    doc.set_metadata({"author": "Jan Jansen"})
    doc.save(bron)

    r = anon.verwerk(bron, tmp_path / "uit")
    assert r.pdf.name == "Jan brief_anoniem.pdf"
    assert not r.lekken
    with pymupdf.open(r.pdf) as d:
        tekst = d[0].get_text()
        assert "Jansen" not in tekst and "Kerkstraat" not in tekst
        assert not d.metadata["author"]
    assert "Naam: Jan" in r.tekst.read_text(encoding="utf-8")


def test_familie_modus_vervangt_alleen_familie():
    a = an.Anonimiseerder(namen=["Johannes Petrus Jansen = Hans"], gebruik_ner=False, alleen_familie=True)
    uit = a.anonimiseer_tekst("Naam: Johannes Petrus JANSEN\nNaam kind: Piet Jansen\n"
                              "Naam getuige: Bert Bakker\nDe heer Bakker belde.")
    # Piet heeft dezelfde achternaam en wordt dus ook vervangen; Bert Bakker niet.
    assert uit == "Naam: Hans\nNaam kind: Piet\nNaam getuige: Bert Bakker\nDe heer Bakker belde."


def test_vraagt_ontbrekende_voornaam_en_onthoudt(tmp_path):
    lijst = tmp_path / "familie.txt"
    lijst.write_text("A. de Vries\n", encoding="utf-8")
    vragen = []
    vrager = an.Vrager(invoer=lambda v: vragen.append(v) or "Anna", onthoud=lijst)
    a = an.Anonimiseerder(namen=an.lees_lijst(lijst), gebruik_ner=False, alleen_familie=True, vrager=vrager)
    assert a.anonimiseer_tekst("Mevrouw A. de Vries tekent.") == "Mevrouw Anna tekent."
    assert len(vragen) == 1 and "A. de Vries" in vragen[0]
    assert a.anonimiseer_tekst("Groet van A. de Vries") == "Groet van Anna"   # niet opnieuw gevraagd
    assert len(vragen) == 1
    assert lijst.read_text(encoding="utf-8").splitlines()[-1] == "A. de Vries = Anna"


def test_overige_persoonsgegevens(anon):
    uit = anon.anonimiseer_tekst(
        "Overleden op 3 mei 2020. Kenteken 12-ABC-3, polisnummer AB-123456, patiëntnummer 7788991, "
        "btw NL123456789B01, ip 192.168.1.20.")
    assert uit == ("Overleden op 2020. Kenteken [kenteken], polisnummer [polisnummer], "
                   "patiëntnummer [patiëntnummer], btw [btw-nummer], ip [ip-adres].")


def test_afbeeldingen_verwijderen(tmp_path):
    bron = tmp_path / "brief.pdf"
    doc = pymupdf.open()
    pagina = doc.new_page()
    pagina.insert_text((40, 60), "Handtekening:", fontsize=11)
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 20, 10), False)
    pagina.insert_image(pymupdf.Rect(40, 70, 160, 130), pixmap=pix)
    doc.save(bron)
    r = an.Anonimiseerder(gebruik_ner=False, zonder_afbeeldingen=True).verwerk(bron, tmp_path / "uit")
    assert r.tellingen["afbeelding"] == 1
    with pymupdf.open(r.pdf) as d:
        assert not d[0].get_images() and "Handtekening" in d[0].get_text()


def test_datum_in_woorden_over_twee_regels(anon):
    uit = anon.anonimiseer_tekst("geboren te Utrecht op achttien maart negentienhonderd\ntweeënzeventig,\nwonende")
    assert uit == "geboren te [geboorteplaats] op 1972,\nwonende"


@pytest.mark.parametrize("tekst, verwacht", [
    ("wonende te 3512 JK Utrecht, Vredenburg 4, gehuwd", "wonende te [adres], gehuwd"),
    ("wonende te 3512 JK Utrecht,\nNeude 11, gehuwd", "wonende te [adres], gehuwd"),
    ("Vredenburg 4, 3512 JK Utrecht", "[adres]"),
    ("wonende te Utrecht, Achter Clarenburg 2, gehuwd", "wonende te [woonplaats], [adres], gehuwd"),
    ("wonende aan de Neude 11 te Utrecht, gehuwd", "wonende aan de [adres] te [woonplaats], gehuwd"),
    ("wonende te 3512 JK UTRECHT, KERKSTRAAT 12", "wonende te [adres]"),
    ("Kerkstraat nummer 12 te Utrecht", "[adres] te Utrecht"),
    ("zie Artikel 12 van de wet", "zie Artikel 12 van de wet"),
])
def test_adres_uit_context(anon, tekst, verwacht):
    assert anon.anonimiseer_tekst(tekst) == verwacht


@pytest.mark.parametrize("tekst, verwacht", [
    ("paspoortnummer: NW1234567", "paspoortnummer: [documentnummer]"),
    ("Nederlands paspoort NW1234567, afgegeven", "Nederlands paspoort [documentnummer], afgegeven"),
    ("zich legitimerende met paspoort\nnummer NW1234567, afgegeven",
     "zich legitimerende met paspoort\nnummer [documentnummer], afgegeven"),
    ("met zijn identiteitsbewijs (paspoort) nummer IR5K3PL92",
     "met zijn identiteitsbewijs (paspoort) nummer [documentnummer]"),
    ("reisdocument: NW1234567", "reisdocument: [documentnummer]"),
    ("rijbewijs nummer 5123456789", "rijbewijs nummer [documentnummer]"),
    ("paspoort, afgegeven op 12-03-2015, BSN 111222333", "paspoort, afgegeven op 12-03-2015, BSN 111222333"),
    # Een documentnummer van 9 cijfers dat toevallig door de elfproef komt, blijft een documentnummer.
    ("zich legitimerende met paspoort nummer 123456782, afgegeven",
     "zich legitimerende met paspoort nummer [documentnummer], afgegeven"),
    ("paspoortnummer: 111222333", "paspoortnummer: [documentnummer]"),
    ("paspoort nummer NW1234567, BSN 111222333", "paspoort nummer [documentnummer], BSN 111222333"),
    ("paspoort verlopen in 2020", "paspoort verlopen in 2020"),
])
def test_documentnummers(anon, tekst, verwacht):
    assert anon.anonimiseer_tekst(tekst) == verwacht


@pytest.mark.parametrize("tekst, verwacht", [
    ("Rekeningnummer: NL91 ABNA 0417\n1643 00 voor de teruggaaf", "Rekeningnummer: ABNA4300 voor de teruggaaf"),
    ("Rekeningnummer: NL91ABNA041716\n4300", "Rekeningnummer: ABNA4300"),
    ("IBAN NL91 ABNA\n0417 1643 00", "IBAN ABNA4300"),
    ("NL91ABNA0417164300\nNL20INGB0001234567", "ABNA4300\nINGB4567"),
    ("IBAN NL91ABNA0417164300\nBSN 111222333", "IBAN ABNA4300\nBSN 111222333"),
    ("rekeningnummer 1234\n567 (oud)", "rekeningnummer ****4567 (oud)"),
])
def test_rekeningnummer_over_twee_regels(anon, tekst, verwacht):
    assert anon.anonimiseer_tekst(tekst) == verwacht


def test_rekeningnummer_over_twee_regels_in_pdf(tmp_path, anon):
    bron = tmp_path / "aangifte.pdf"
    doc = pymupdf.open()
    doc.new_page().insert_text((40, 60), "Teruggaaf op rekeningnummer NL91 ABNA 0417\n1643 00 van de belastingdienst",
                               fontsize=11)
    doc.save(bron)
    r = anon.verwerk(bron, tmp_path / "uit")
    with pymupdf.open(r.pdf) as d:
        tekst = d[0].get_text()
    assert "0417" not in tekst and "1643" not in tekst and "ABNA4300" in tekst and not r.gemarkeerd


@pytest.mark.parametrize("tekst, verwacht", [
    ("Bel +32 470 12 34 56 of 0044 20 7946 0958", "Bel [telefoon] of [telefoon]"),
    ("Bedrag +12 345 euro, artikel 12 lid 3", "Bedrag +12 345 euro, artikel 12 lid 3"),
    ("iban nl91abna0417164300", "iban ABNA4300"),
    ("auto 12 ABC 3 en 12-ABC-3", "auto [kenteken] en [kenteken]"),
])
def test_buitenland_kleine_letters_spaties(anon, tekst, verwacht):
    assert anon.anonimiseer_tekst(tekst) == verwacht


def test_paginaminiatuur_en_structuur_verwijderd(tmp_path, anon):
    bron = tmp_path / "brief.pdf"
    doc = pymupdf.open()
    doc.new_page().insert_text((40, 60), "Naam: Jan Jansen", fontsize=11)
    xref = doc.get_new_xref()
    doc.update_object(xref, "<< /Width 1 /Height 1 /ColorSpace /DeviceGray /BitsPerComponent 8 >>")
    doc.update_stream(xref, b"\x00")
    doc.xref_set_key(doc[0].xref, "Thumb", f"{xref} 0 R")
    doc.xref_set_key(doc.pdf_catalog(), "StructTreeRoot", "<< /Type /StructTreeRoot >>")
    doc.save(bron)
    r = anon.verwerk(bron, tmp_path / "uit")
    with pymupdf.open(r.pdf) as d:
        assert d.xref_get_key(d[0].xref, "Thumb")[0] == "null"
        assert d.xref_get_key(d.pdf_catalog(), "StructTreeRoot")[0] == "null"


def test_familie_txt_wordt_overal_gevonden(tmp_path, monkeypatch):
    import familie as fam
    thuis = tmp_path / "thuis"
    (thuis / ".pdf-anonimiseren").mkdir(parents=True)
    lijst = thuis / ".pdf-anonimiseren" / "familie.txt"
    lijst.write_text("Jan Jansen\n", encoding="utf-8")
    monkeypatch.setattr(an.Path, "home", lambda: thuis)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(fam, "PROGRAMMAMAP", tmp_path / "script")
    assert fam.vind_familie() == lijst
    (tmp_path / "familie.txt").write_text("Piet Jansen\n", encoding="utf-8")
    assert fam.vind_familie() == tmp_path / "familie.txt"   # huidige map gaat voor


def test_sleutelbestand_buiten_uitvoermap(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(an, "SLEUTELMAP", tmp_path / "sleutels")
    bron = tmp_path / "bron" / "brief.pdf"
    bron.parent.mkdir()
    doc = pymupdf.open()
    doc.new_page().insert_text((40, 60), "Naam: Jan Jansen", fontsize=11)
    doc.save(bron)
    assert an.main([str(bron), "--sleutel", "--geen-ner", "--alle-namen", "--niet-vragen"]) == 0
    assert list((tmp_path / "sleutels").glob("*.json"))
    assert not list((bron.parent / "geanonimiseerd").glob("*.json"))
    assert "ECHTE persoonsgegevens" in capsys.readouterr().out


def test_dubbele_achternaam():
    v = an.ontleed_naam("Anna Jansen-de Vries")
    assert v.voornamen == ["Anna"] and v.achternaam == "Jansen-de Vries"
    assert an.ontleed_naam("A. Smit-van der Berg").achternaam == "Smit-van der Berg"
    uit = an.Anonimiseerder(gebruik_ner=False).anonimiseer_tekst("Naam: Anna Jansen-de Vries")
    assert uit == "Naam: Anna"


def test_geboortenaam_en_getrouwde_naam_een_persoon():
    a = an.Anonimiseerder(namen=["Anna Wilhelmina de Vries | Anna Jansen-de Vries = Anna"],
                          gebruik_ner=False, alleen_familie=True)
    uit = a.anonimiseer_tekst("Mevrouw A.W. Jansen-de Vries, geboren De Vries. Anna Jansen-de Vries tekent.")
    assert uit == "Mevrouw Anna, geboren Anna. Anna tekent."
    assert "(2)" not in uit


@pytest.mark.parametrize("tekst, verwacht", [
    ("Kenmerk 0012 3456 7890 en ordernummer 0049123456789", "Kenmerk 0012 3456 7890 en ordernummer 0049123456789"),
    ("Bel 0049 151 23456789 of +49 151 23456789", "Bel [telefoon] of [telefoon]"),
    ("Bel 0032 470 12 34 56", "Bel [telefoon]"),
])
def test_telefoon_met_00_alleen_met_landcode(anon, tekst, verwacht):
    assert anon.anonimiseer_tekst(tekst) == verwacht


def test_bsn_alleen_via_familie(anon):
    assert anon.anonimiseer_tekst("BSN: 111222333, burgerservicenummer 123456782") == \
        "BSN: 111222333, burgerservicenummer 123456782"
