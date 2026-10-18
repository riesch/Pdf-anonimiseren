"""Tests voor gegevens van familieleden in familie.txt, met verzonnen gegevens."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import anonimiseer as an  # noqa: E402
import familie as fam  # noqa: E402

ANTWOORDEN = [
    "Richard Pieter Jansen",   # naam
    "",                        # roepnaam -> Richard
    "111222334",               # ongeldig BSN -> opnieuw gevraagd
    "111222333",               # BSN
    "Kerkstraat", "12a", "3512 JK",   # adres 1
    "",                        # klaar
]


class NepSleutelhanger:
    """Doet zich voor als de sleutelhanger van het systeem; de tests raken de echte nooit aan."""
    priority = 1

    def __init__(self):
        self.inhoud = {}

    def get_password(self, dienst, account):
        return self.inhoud.get((dienst, account))

    def set_password(self, dienst, account, waarde):
        self.inhoud[(dienst, account)] = waarde


@pytest.fixture
def lijst(tmp_path):
    antwoorden = iter(ANTWOORDEN)
    pad = tmp_path / "familie.txt"
    sleutelpad = tmp_path / "sleutel"
    assert fam.familielid_toevoegen(pad, lambda _: next(antwoorden), sleutelpad, sleutelhanger=None) == "Richard"
    return pad, fam.geheime_sleutel(pad=sleutelpad, sleutelhanger=None)


def _anon(pad, sleutel, **kw):
    return an.Anonimiseerder(namen=an.lees_lijst(pad), gebruik_ner=False, sleutel=sleutel,
                             familiegegevens=fam.lees_familie(pad, sleutel), **kw)


def test_bestand_bevat_geen_leesbare_gegevens(lijst):
    pad, _ = lijst
    inhoud = pad.read_text(encoding="utf-8")
    assert "Richard Pieter Jansen" in inhoud
    for geheim in ("111222333", "Kerkstraat", "3512"):
        assert geheim not in inhoud
    assert an.lees_lijst(pad) == ["Richard Pieter Jansen"]


def test_bsn_en_adres_krijgen_naam(lijst):
    pad, sleutel = lijst
    uit = _anon(pad, sleutel).anonimiseer_tekst(
        "Richard Jansen, BSN 111.222.333, woont op Kerkstr. 12 A, 3512 JK Utrecht.\n"
        "Geboren 12-03-1985. Ander BSN: 123456782. Buurman woont op Kerkstraat 14.")
    assert uit == ("Richard, BSN [BSN Richard], woont op [adres Richard], [adres Richard].\n"
                   "Geboren 1985. Ander BSN: [BSN]. Buurman woont op [adres].")


def test_familiemodus(lijst):
    pad, sleutel = lijst
    uit = _anon(pad, sleutel, alleen_familie=True).anonimiseer_tekst("BSN 111222333 van R. Jansen")
    assert uit == "BSN [BSN Richard] van Richard"


def test_andere_sleutel_herkent_niets(lijst, tmp_path):
    pad, _ = lijst
    ander = fam.geheime_sleutel(maak=True, pad=tmp_path / "andere", sleutelhanger=None)
    gegevens = fam.lees_familie(pad, ander)
    assert not gegevens.sleutel_klopt


def test_nieuwe_sleutel_gaat_in_sleutelhanger(tmp_path):
    kr, pad = NepSleutelhanger(), tmp_path / "sleutel"
    sleutel = fam.geheime_sleutel(maak=True, pad=pad, sleutelhanger=kr)
    assert not pad.exists() and kr.get_password(fam.DIENST, fam.ACCOUNT) == sleutel.hex()
    assert fam.geheime_sleutel(pad=pad, sleutelhanger=kr) == sleutel


def test_zonder_sleutelhanger_een_bestand(tmp_path):
    pad = tmp_path / "sleutel"
    sleutel = fam.geheime_sleutel(maak=True, pad=pad, sleutelhanger=None)
    assert bytes.fromhex(pad.read_text()) == sleutel


def test_bestaande_sleutel_overzetten_en_reservekopie(tmp_path):
    kr, pad = NepSleutelhanger(), tmp_path / "sleutel"
    sleutel = fam.geheime_sleutel(maak=True, pad=pad, sleutelhanger=None)
    fam.naar_sleutelhanger(pad=pad, sleutelhanger=kr)
    assert not pad.exists() and fam.geheime_sleutel(pad=pad, sleutelhanger=kr) == sleutel

    kopie = tmp_path / "usb" / "reservekopie"
    fam.exporteer_sleutel(kopie, pad=pad, sleutelhanger=kr)
    ander = NepSleutelhanger()   # bijv. een andere computer
    fam.naar_sleutelhanger(pad=pad, sleutelhanger=ander, bron=kopie)
    assert kopie.exists() and fam.geheime_sleutel(pad=pad, sleutelhanger=ander) == sleutel


def test_overschrijft_geen_andere_sleutel(tmp_path):
    kr, pad = NepSleutelhanger(), tmp_path / "sleutel"
    fam.geheime_sleutel(maak=True, pad=tmp_path / "x", sleutelhanger=kr)
    fam.geheime_sleutel(maak=True, pad=pad, sleutelhanger=None)
    with pytest.raises(RuntimeError, match="andere sleutel"):
        fam.naar_sleutelhanger(pad=pad, sleutelhanger=kr)
    assert pad.exists()
