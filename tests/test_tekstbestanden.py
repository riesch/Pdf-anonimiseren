"""Tests voor Word (.docx) en e-mail (.eml), met verzonnen gegevens."""

import sys
import zipfile
from email.message import EmailMessage
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import anonimiseer as an  # noqa: E402
import tekstbestanden as tb  # noqa: E402

W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'


def _p(tekst):
    return f"<w:p><w:r><w:t>{tekst}</w:t></w:r></w:p>"


def _docx(pad):
    tabel = ("<w:tbl>" + "".join(f"<w:tr><w:tc>{_p(a)}</w:tc><w:tc>{_p(b)}</w:tc></w:tr>"
                                 for a, b in (("Naam", "BSN"), ("Richard Jansen", "111222333"))) + "</w:tbl>")
    with zipfile.ZipFile(pad, "w") as z:
        z.writestr("word/document.xml", f'<w:document {W}><w:body>'
                   + _p("Geachte heer Jansen,") + tabel + _p("Rekening NL91ABNA0417164300.")
                   + "</w:body></w:document>")
        z.writestr("word/footer1.xml", f"<w:ftr {W}>{_p('Opgesteld door Karin Visser, tel. 06-12345678')}</w:ftr>")
        z.writestr("word/comments.xml", f"<w:comments {W}>{_p('Vraag aan R. Jansen')}</w:comments>")


def test_docx(tmp_path):
    bron = tmp_path / "Brief Jansen.docx"
    _docx(bron)
    r = an.Anonimiseerder(namen=["Richard Jansen", "Karin Visser"], gebruik_ner=False).verwerk_tekst(
        bron, tmp_path / "uit")
    md = r.tekst.read_text(encoding="utf-8")
    assert r.tekst.name == "Brief Richard_anoniem.md"
    assert "| Naam | BSN |" in md and "| Richard | 111222333 |" in md and "ABNA4300" in md
    assert "## Voettekst" in md and "Karin" in md and "[telefoon]" in md and "## Opmerkingen" in md
    for geheim in ("Jansen", "Visser", "06-12345678"):
        assert geheim not in md


def test_eml(tmp_path):
    m = EmailMessage()
    m["From"] = "Karin Visser <karin@voorbeeld.nl>"
    m["To"] = "Richard Jansen <richard@voorbeeld.nl>"
    m["Subject"] = "Aangifte Richard Jansen"
    m.set_content("Beste Richard,\n\nJe BSN 111222333 staat in de bijlage.\n\nGroet, Karin Visser")
    m.add_attachment(b"%PDF-1.4", maintype="application", subtype="pdf", filename="aangifte.pdf")
    bron = tmp_path / "mail.eml"
    bron.write_bytes(bytes(m))
    r = an.Anonimiseerder(namen=["Richard Jansen", "Karin Visser"], gebruik_ner=False).verwerk_tekst(
        bron, tmp_path / "uit")
    md = r.tekst.read_text(encoding="utf-8")
    assert "Van: Karin <[e-mail]>" in md and "Onderwerp: Aangifte Richard" in md and "BSN 111222333" in md
    assert "aangifte.pdf" in md and "niet geanonimiseerd" in md
    assert "Visser" not in md and "Jansen" not in md and "@" not in md


def test_html_mail_naar_tekst():
    assert tb._html_naar_tekst("<p>Hallo&nbsp;<b>Jan</b></p><style>x{}</style><div>Groet</div>") == "Hallo\xa0Jan\nGroet"
