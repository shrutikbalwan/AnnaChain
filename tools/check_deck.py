#!/usr/bin/env python3
"""Check the SIH deck: six slides, and no trace of the project's old name.

    python3 tools/check_deck.py [docs/SIH2026_26232_AnnaChain_OfficialFormat.pptx]

Looks everywhere text can hide in a .pptx, not just the obvious text boxes:
grouped shapes, tables, speaker notes, and the slide layouts and masters
behind them. If a PDF of the same name sits next to it, its text is checked
too. Exit status 0 only if everything passes.
"""
import re
import sys
from pathlib import Path

OLD = re.compile(r"secure\s*harvest", re.I)
DEFAULT = Path(__file__).resolve().parents[1] / "docs" / "SIH2026_26232_AnnaChain_OfficialFormat.pptx"


def shape_texts(shapes):
    for sh in shapes:
        if sh.shape_type == 6:                                   # a group
            yield from shape_texts(sh.shapes)
        if getattr(sh, "has_text_frame", False) and sh.has_text_frame:
            yield sh.text_frame.text
        if getattr(sh, "has_table", False) and sh.has_table:
            for row in sh.table.rows:
                for cell in row.cells:
                    yield cell.text


def main():
    from pptx import Presentation
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT
    if not path.exists():
        sys.exit(f"deck not found: {path}")
    p = Presentation(str(path))

    hits = []
    for i, sl in enumerate(p.slides, 1):
        for t in shape_texts(sl.shapes):
            if OLD.search(t):
                hits.append(f"slide {i}: {t.strip()[:70]!r}")
        if sl.has_notes_slide and OLD.search(sl.notes_slide.notes_text_frame.text or ""):
            hits.append(f"slide {i} speaker notes")
    for m in p.slide_masters:
        for t in shape_texts(m.shapes):
            if OLD.search(t):
                hits.append(f"slide master: {t.strip()[:70]!r}")
        for lay in m.slide_layouts:
            for t in shape_texts(lay.shapes):
                if OLD.search(t):
                    hits.append(f"layout {lay.name!r}: {t.strip()[:70]!r}")
    core = p.core_properties
    for field in ("title", "subject", "keywords", "comments"):
        if OLD.search(getattr(core, field) or ""):
            hits.append(f"document property {field}")

    pdf = path.with_suffix(".pdf")
    pdf_note = "no PDF beside it"
    if pdf.exists():
        try:
            from pypdf import PdfReader
            pages = PdfReader(str(pdf)).pages
            pdf_note = f"PDF: {len(pages)} pages"
            for i, pg in enumerate(pages, 1):
                if OLD.search(pg.extract_text() or ""):
                    hits.append(f"PDF page {i}")
        except ImportError:
            pdf_note = "PDF present but pypdf is not installed; its text was not checked"

    n = len(p.slides)
    print("slides:", n)
    print("old name present:", bool(hits))
    for h in hits:
        print("  ", h)
    print(pdf_note)
    sys.exit(0 if n == 6 and not hits else 1)


if __name__ == "__main__":
    main()
