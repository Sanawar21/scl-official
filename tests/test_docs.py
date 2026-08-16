"""Docs + changelog tests: markdown renderers, PDF generation, routes."""
import pytest

from app.services.doc_service import DOCS, md_to_html, md_to_pdf, read_doc


@pytest.fixture()
def changelog(app):
    return app.extensions["changelog_service"]


# ---------------------------------------------------------------------------
# markdown renderers
# ---------------------------------------------------------------------------
SAMPLE = """# Title

Some **bold** and `code` here.

## Section

- item one
- item two

| A | B |
|---|---|
| 1 | 2 |

> a quote
"""


def test_md_to_html_renders_constructs():
    html = md_to_html(SAMPLE)
    assert "<h1>Title</h1>" in html
    assert "<h2>Section</h2>" in html
    assert "<b>bold</b>" in html
    assert "<code>code</code>" in html
    assert "<ul>" in html and "<li>item one</li>" in html
    assert "<table>" in html and "<th>A</th>" in html and "<td>1</td>" in html
    assert "<blockquote>a quote</blockquote>" in html


def test_md_to_pdf_returns_pdf_bytes():
    pdf = md_to_pdf(SAMPLE, "Test doc")
    assert pdf[:4] == b"%PDF"
    assert len(pdf) > 500


def test_md_to_pdf_handles_all_real_docs():
    for d in DOCS:
        md = read_doc(d["slug"])
        assert md is not None, d["slug"]
        pdf = md_to_pdf(md, d["title"], "Official SCL Season 2 document")
        assert pdf[:4] == b"%PDF"
        assert len(pdf) > 3000


# ---------------------------------------------------------------------------
# changelog service
# ---------------------------------------------------------------------------
def test_changelog_add_list_delete(app, changelog):
    e = changelog.add_entry("Rule change", "**Bold** body", "2026-08-16", "admin")
    assert e["title"] == "Rule change"
    assert e["change_date"] == "2026-08-16"

    entries = changelog.list_entries()
    assert entries[0]["id"] == e["id"]

    with pytest.raises(ValueError):
        changelog.add_entry("", "body", "", "admin")
    with pytest.raises(ValueError):
        changelog.add_entry("title", "", "", "admin")

    assert changelog.delete_entry(e["id"]) is True
    assert changelog.get_entry(e["id"]) is None


# ---------------------------------------------------------------------------
# routes
# ---------------------------------------------------------------------------
def test_docs_and_changelog_routes(app):
    c = app.test_client()
    assert c.get("/docs").status_code == 200
    assert c.get("/docs/rulebook").status_code == 200
    assert c.get("/docs/vault").status_code == 200
    assert c.get("/docs/wagers").status_code == 200
    assert c.get("/docs/economy").status_code == 200
    r = c.get("/docs/rulebook/pdf")
    assert r.status_code == 200
    assert r.data[:4] == b"%PDF"
    assert c.get("/docs/nope").status_code == 404
    assert c.get("/changelog").status_code == 200


def test_admin_changelog_routes(app, changelog):
    c = app.test_client()
    assert c.get("/admin/changelog").status_code == 302  # admin required
    c.post("/auth/login", data={"username": "admin", "password": "admin123"})
    assert c.get("/admin/changelog").status_code == 200
    r = c.post("/admin/changelog/add", data={"title": "Route entry",
                                             "body": "body text",
                                             "change_date": "2026-08-16"})
    assert r.status_code == 302
    entries = changelog.list_entries()
    assert any(e["title"] == "Route entry" for e in entries)
    # public page shows it, rendered as markdown
    body = c.get("/changelog").data.decode()
    assert "Route entry" in body
    entry = next(e for e in entries if e["title"] == "Route entry")
    r = c.post(f"/admin/changelog/{entry['id']}/delete")
    assert r.status_code == 302
    assert changelog.get_entry(entry["id"]) is None
