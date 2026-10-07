# services/bible/tests/test_commentary_notes_install.py
"""Filing a commentary's notes: no translation installed, volumes coexist.

A commentary source has no verse text, so the importer must either file its
notes (when ``import_notes`` is set) or refuse naming the flag -- never install
an empty translation. Replacing notes is scoped per source so Volume 1 and
Volume 2 of the same edition survive each other, and a re-import of one file
never duplicates it.
"""
from __future__ import annotations

import zipfile
from pathlib import Path

from sqlmodel import Session, select

from services.bible import corpus, importer
from services.bible.models import ImportRun, StudyNote

OPF = """<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="2.0" unique-identifier="bookid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>Commentary</dc:title></metadata>
  <manifest><item id="d1" href="part1.html" media-type="application/xhtml+xml"/></manifest>
  <spine><itemref idref="d1"/></spine>
</package>
"""


def commentary(path: Path, marker: str) -> Path:
    body = (
        "<h1>Romans 1</h1>"
        '<p style="text-transform:uppercase">A Section (1:1)</p>'
        f"<p>{marker} is what this volume says about the verse.</p>"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip")
        archive.writestr(
            "META-INF/container.xml",
            '<?xml version="1.0"?><container version="1.0" '
            'xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
            '<rootfiles><rootfile full-path="OEBPS/content.opf" '
            'media-type="application/oebps-package+xml"/></rootfiles></container>',
        )
        archive.writestr("OEBPS/content.opf", OPF)
        archive.writestr("OEBPS/part1.html", f"<html><body>{body}</body></html>")
    return path


def nkjv_installed(session: Session, corpus_file: Path) -> Session:
    corpus.import_corpus(session, code="nkjv", name="New King James Version", source_path=corpus_file)
    return session


def notes_for(session: Session, edition: str) -> list[StudyNote]:
    return list(session.exec(select(StudyNote).where(StudyNote.edition_code == edition)).all())


def test_a_commentary_source_without_the_notes_flag_is_refused(
    session: Session, tmp_path: Path
) -> None:
    source = commentary(tmp_path / "c.epub", "Volume one")
    report = importer.run(session, importer.ImportPlan(code="nkjv", kind="epub", source_path=str(source)))
    assert report.status == "failed"
    assert "import_notes" in report.message
    assert notes_for(session, "nkjv") == []


def test_commentary_notes_are_filed_without_installing_a_translation(
    session: Session, corpus_file: Path, tmp_path: Path
) -> None:
    nkjv_installed(session, corpus_file)
    source = commentary(tmp_path / "c.epub", "Volume one")
    report = importer.run(
        session,
        importer.ImportPlan(
            code="nkjv",
            kind="epub",
            source_path=str(source),
            import_notes=True,
            edition="nkjv-tomholland",
            edition_name="Romans: The Divine Marriage (Tom Holland)",
        ),
    )
    assert report.status == "succeeded"
    assert report.note_count == 1
    assert report.verse_count == 0
    assert "no translation was installed" in report.message
    filed = notes_for(session, "nkjv-tomholland")
    assert len(filed) == 1
    assert filed[0].version_code == "nkjv"
    assert (filed[0].chapter, filed[0].verse) == (1, 1)
    assert "Volume one" in filed[0].body
    runs = list(session.exec(select(ImportRun).where(ImportRun.code == "nkjv")).all())
    assert any(run.status == "succeeded" for run in runs)


def test_two_volumes_of_one_commentary_keep_each_other(
    session: Session, corpus_file: Path, tmp_path: Path
) -> None:
    nkjv_installed(session, corpus_file)
    plan = dict(
        code="nkjv",
        kind="epub",
        import_notes=True,
        edition="nkjv-tomholland",
        edition_name="Romans: The Divine Marriage (Tom Holland)",
    )
    first = commentary(tmp_path / "vol1.epub", "Volume one")
    second = commentary(tmp_path / "vol2.epub", "Volume two")
    one = importer.run(session, importer.ImportPlan(source_path=str(first), **plan))
    two = importer.run(session, importer.ImportPlan(source_path=str(second), **plan))
    assert one.ok and two.ok
    bodies = " ".join(note.body for note in notes_for(session, "nkjv-tomholland"))
    assert "Volume one" in bodies
    assert "Volume two" in bodies

    again = importer.run(session, importer.ImportPlan(source_path=str(first), **plan))
    assert again.ok
    filed = notes_for(session, "nkjv-tomholland")
    assert len(filed) == 2, "re-importing one volume must replace only its own notes"
    assert sum("Volume one" in note.body for note in filed) == 1
    assert sum("Volume two" in note.body for note in filed) == 1


def test_a_commentary_never_reaches_the_translation_importer(
    session: Session, monkeypatch, corpus_file: Path, tmp_path: Path
) -> None:
    nkjv_installed(session, corpus_file)
    source = commentary(tmp_path / "c.epub", "Volume one")

    def explode(*args, **kwargs):
        raise AssertionError("a commentary must not be handed to import_corpus")

    monkeypatch.setattr(importer.corpus, "import_corpus", explode)
    report = importer.run(
        session,
        importer.ImportPlan(
            code="nkjv", kind="epub", source_path=str(source), import_notes=True,
            edition="nkjv-tomholland",
        ),
    )
    assert report.ok
    assert report.note_count == 1
