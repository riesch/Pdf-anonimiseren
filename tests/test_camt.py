"""Tests voor CAMT.053, met een verzonnen afschrift (tests/data/camt053_voorbeeld.xml)."""

import sys
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import camt  # noqa: E402

VOORBEELD = Path(__file__).parent / "data" / "camt053_voorbeeld.xml"
NS = {"c": "urn:iso:std:iso:20022:tech:xsd:camt.053.001.02"}


def _tekst(boom, pad):
    return [el.text for el in boom.getroot().findall(pad, NS)]


def test_camt(tmp_path):
    r = camt.CamtAnonimiseerder().verwerk(VOORBEELD, tmp_path)
    uit = r.tekst.read_text(encoding="utf-8")
    boom = ET.parse(r.tekst)    # nog steeds geldige XML met dezelfde namespace
    assert "JANSEN" not in uit.upper() and "Vries" not in uit and "Kerkstraat" not in uit
    assert "NL91ABNA" not in uit and "0417164300" not in uit and "06-12345678" not in uit
    assert _tekst(boom, ".//c:Stmt/c:Acct/c:Id/c:IBAN") == ["**************4300"]
    assert _tekst(boom, ".//c:CdtrAcct/c:Id/c:IBAN") == ["**************4300 (2)", "**************5588"]
    # "HR J JANSEN" krijgt de voornaam uit de omschrijving ("groet Jan Jansen").
    assert set(_tekst(boom, ".//c:Dbtr/c:Nm")) == {"Jan"}
    assert _tekst(boom, ".//c:BirthDt") == ["1987"]
    # Bedrijf, bank, bedragen en datums blijven staan.
    assert "Eneco Consumenten B.V." in uit and "Marten Meesweg" in uit and "NL12ZZZ123456780000" in uit
    assert "ABN AMRO Bank" in uit and "750.00" in uit and "2024-01-10" in uit
    assert _tekst(boom, ".//c:EndToEndId") == ["[kenmerk]", "NOTPROVIDED"]
    assert not r.lekken


def test_camt_zip(tmp_path):
    bron = tmp_path / "afschriften.zip"
    with zipfile.ZipFile(bron, "w") as z:
        z.write(VOORBEELD, "NL91ABNA0417164300_20240110.xml")
        z.write(VOORBEELD, "NL91ABNA0417164300_20240111.xml")
    r = camt.CamtAnonimiseerder().verwerk(bron, tmp_path / "uit")
    with zipfile.ZipFile(r.tekst) as z:
        assert sorted(z.namelist()) == ["4300_20240110.xml", "4300_20240111.xml"]
        assert all("JANSEN" not in z.read(n).decode().upper() for n in z.namelist())


def test_camt_familie(tmp_path):
    r = camt.CamtAnonimiseerder(namen=["Jan Jansen"], alleen_familie=True).verwerk(VOORBEELD, tmp_path)
    uit = r.tekst.read_text(encoding="utf-8")
    assert "JANSEN" not in uit.upper() and "<Nm>A de Vries</Nm>" in uit   # geen familie
