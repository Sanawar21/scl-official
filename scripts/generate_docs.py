"""Generate PDFs for the SCL participant documents.

Reads the markdown sources in `docs/` and writes PDFs into
`app/static/docs/<slug>.pdf`, which the website serves from `/docs`.

Usage:
    python scripts/generate_docs.py          # build all four PDFs
    python scripts/generate_docs.py rulebook # build one document
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.doc_service import DOCS, DOCS_ROOT, md_to_pdf, read_doc  # noqa: E402

STATIC_DOCS = Path(__file__).resolve().parents[1] / "app" / "static" / "docs"


def build(slug: str = None) -> None:
    STATIC_DOCS.mkdir(parents=True, exist_ok=True)
    targets = [d for d in DOCS if slug is None or d["slug"] == slug]
    if not targets:
        print(f"Unknown document '{slug}'. Available: {', '.join(d['slug'] for d in DOCS)}")
        sys.exit(1)
    for doc in targets:
        md = read_doc(doc["slug"])
        if md is None:
            print(f"!! Missing source for {doc['slug']} ({doc['file']})")
            continue
        pdf = md_to_pdf(md, doc["title"], "Official SCL Season 2 document")
        out = STATIC_DOCS / f"{doc['slug']}.pdf"
        out.write_bytes(pdf)
        print(f"  {out.name}: {len(pdf):,} bytes")


if __name__ == "__main__":
    build(sys.argv[1] if len(sys.argv) > 1 else None)
