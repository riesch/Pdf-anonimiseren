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
    "12-03-1985",              # geboortedatum
    "Kerkstraat", "12a", "3512 JK",   # adres 1
    "",                        # klaar
]


@pytest.fixture
def lijst(tmp_path):
    antwoorden = iter(ANTWOORDEN)
    pad = tmp_path / "familie.txt"
    sleutelpad = tmp_path / "sleutel"
    assert fam.familielid_toevoegen(pad, lambda _: next(antwoorden), sleutelpad) == "Richard"
    return pad, fam.geheime_sleutel(pad=sleutelpad)


def _anon(pad, sleutel, **kw):
    return an.Anonimiseerder(namen=an.lees_lijst(pad), gebruik_ner=False, sleutel=sleutel,
                             familiegegevens=fam.lees_familie(pad, sleutel), **kw)


def test_bestand_bevat_geen_leesbare_gegevens(lijst):
    pad, _ = lijst
    inhoud = pad.read_text(encoding="utf-8")
    assert "Richard Pieter Jansen" in inhoud
    for geheim in ("111222333", "1985", "Kerkstraat", "3512"):
        assert geheim not in inhoud
    assert an.lees_lijst(pad) == ["Richard Pieter Jansen"]


def test_bsn_en_adres_krijgen_naam(lijst):
    pad, sleutel = lijst
    uit = _anon(pad, sleutel).anonimiseer_tekst(
        "Richard Jansen, BSN 111.222.333, woont op Kerkstr. 12 A, 3512 JK Utrecht.\n"
        "Datum: 12-03-1985. Ander BSN: 123456782. Buurman woont op Kerkstraat 14.")
    assert uit == ("Richard, BSN [BSN Richard], woont op [adres Richard], [adres Richard].\n"
                   "Datum: 1985. Ander BSN: [BSN]. Buurman woont op [adres].")


def test_familiemodus(lijst):
    pad, sleutel = lijst
    uit = _anon(pad, sleutel, alleen_familie=True).anonimiseer_tekst("BSN 111222333 van R. Jansen")
    assert uit == "BSN [BSN Richard] van Richard"


def test_andere_sleutel_herkent_niets(lijst, tmp_path):
    pad, _ = lijst
    ander = fam.geheime_sleutel(maak=True, pad=tmp_path / "andere")
    gegevens = fam.lees_familie(pad, ander)
    assert not gegevens.sleutel_klopt
