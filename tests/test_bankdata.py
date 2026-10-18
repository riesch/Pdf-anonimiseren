"""Tests voor bankexports, met uitsluitend verzonnen gegevens."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import bankdata as bd  # noqa: E402

ING = '''"Datum";"Naam / Omschrijving";"Rekening";"Tegenrekening";"Code";"Af Bij";"Bedrag (EUR)";"Mutatiesoort";"Mededelingen";"Saldo na mutatie";"Tag"
"20240105";"Albert Heijn 1234 UTRECHT NLD";"NL91ABNA0417164300";"";"BA";"Af";"45,67";"Betaalautomaat";"Pasvolgnr: 001 05-01-2024 18:12 Term: 5678";"1.234,56";""
"20240110";"HR J JANSEN EO";"NL91ABNA0417164300";"NL20INGB0001234567";"OV";"Af";"750,00";"Overschrijving";"Naam: HR J JANSEN EO Omschrijving: huur Kerkstraat 12a";"484,56";""
"20240112";"Eneco Consumenten";"NL91ABNA0417164300";"NL02RABO0123456789";"IC";"Af";"120,00";"Incasso";"Naam: Eneco Consumenten Omschrijving: klantnr 12345678";"364,56";""
"20240120";"J. Jansen";"NL91ABNA0417164300";"NL91ABNA0417164300";"OV";"Bij";"10,00";"Overschrijving";"Naam: J. Jansen Omschrijving: van spaar";"399,56";""
'''


def _verwerk(tmp_path, naam, inhoud, **kw):
    bron = tmp_path / naam
    bron.write_text(inhoud, encoding="utf-8")
    r = bd.BankAnonimiseerder(**kw).verwerk(bron, tmp_path / "uit")
    return r, r.tekst.read_text(encoding="utf-8")


def test_is_persoon():
    assert bd.is_persoon("HR J JANSEN EO")
    assert bd.is_persoon("A de Vries")
    assert not bd.is_persoon("Albert Heijn 1234 UTRECHT")
    assert not bd.is_persoon("Vattenfall Klantenservice N.V.")
    assert not bd.is_persoon("Coolblue")


def test_ing(tmp_path):
    r, uit = _verwerk(tmp_path, "NL91ABNA0417164300_2024.csv", ING)
    assert "eigen rekening 1" in r.tekst.name
    assert "JANSEN" not in uit.upper() and "NL20INGB" not in uit and "NL91ABNA" not in uit
    assert "Kerkstraat" not in uit and "12345678" not in uit
    assert "Albert Heijn" in uit and "Eneco Consumenten" in uit   # bedrijven blijven staan
    assert "750,00" in uit and "20240110" in uit                   # bedragen en datums ook
    assert uit.count("Persoon 1") == 4                              # HR J JANSEN = J. Jansen
    assert '"[eigen rekening 1]";"[eigen rekening 1]"' in uit
    assert not r.lekken


def test_alle_tegenpartijen(tmp_path):
    _, uit = _verwerk(tmp_path, "x.csv", ING, alle_tegenpartijen=True)
    assert "Albert Heijn" not in uit.split("\n")[1] and "[tegenpartij 1]" in uit


def test_abn_txt_zonder_kopregel(tmp_path):
    regel = ("123456789\tEUR\t20240110\t954,33\t204,33\t20240110\t-750,00\t"
             "/TRTP/SEPA OVERBOEKING/IBAN/NL20INGB0001234567/BIC/INGBNL2A/NAME/J JANSEN/REMI/HUUR/\n")
    _, uit = _verwerk(tmp_path, "TXT240101.TXT", regel * 2)
    assert uit.startswith("[eigen rekening 1]\tEUR\t20240110")
    assert "/NAME/Persoon 1/" in uit and "[IBAN 1]" in uit and "-750,00" in uit
