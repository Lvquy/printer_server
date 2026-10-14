"""Build the canonical A4 guide with verified destination-page references.

Documentation-only dependencies, separate from the application:
uv run --no-project --python 3.11 --with markdown==3.7 --with playwright==1.58.0 \
  --with pymupdf==1.26.0 python tools/build_report_pdf.py
Requires locally installed Chrome/Chromium, or --browser /path/to/executable.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import tempfile
from pathlib import Path

from render_technical_report import heading_tokens, refresh_navigation, render_document

PRINT_PROFILE = "A4; margins 16/14/18mm; bundled CSS; system fonts; Chrome"


def find_browser(explicit: str | None) -> str:
    if explicit:
        if not Path(explicit).is_file():
            raise ValueError(f"Browser does not exist: {explicit}")
        return explicit
    candidates = [
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/usr/bin/google-chrome",
        "/usr/bin/chromium",
        "/usr/bin/chromium-browser",
        str(
            Path(os.environ.get("PROGRAMFILES", "C:/Program Files"))
            / "Google/Chrome/Application/chrome.exe"
        ),
        str(Path(os.environ.get("LOCALAPPDATA", "")) / "Google/Chrome/Application/chrome.exe"),
    ]
    for candidate in candidates:
        if Path(candidate).is_file():
            return candidate
    raise ValueError("Install Chrome/Chromium or pass --browser with its executable path")


def destinations(document, targets: set[str]) -> dict[str, int]:
    names = document.resolve_names()
    missing = targets - names.keys()
    if missing:
        raise ValueError(f"PDF is missing linked destinations: {sorted(missing)}")
    return {target: int(names[target]["page"]) + 1 for target in sorted(targets)}


def verify_links(document, pages: dict[str, int]) -> int:
    verified = 0
    for page in document:
        for link in page.get_links():
            target = link.get("nameddest")
            if target in pages:
                if int(link.get("page", -1)) + 1 != pages[target]:
                    raise ValueError(f"PDF link does not match printed reference: {target}")
                verified += 1
    if not verified:
        raise ValueError("No internal PDF navigation links were found")
    return verified


def main() -> None:
    import pymupdf
    from playwright.sync_api import sync_playwright

    parser = argparse.ArgumentParser(
        description="Build the paginated offline Print Appliance guide"
    )
    root = Path(__file__).resolve().parents[1]
    parser.add_argument("--source", type=Path, default=root / "docs/technical-report.md")
    parser.add_argument("--browser")
    args = parser.parse_args()
    source = args.source.resolve()
    source.write_text(
        refresh_navigation(source.read_text(encoding="utf-8"), source), encoding="utf-8"
    )
    html_file = source.with_suffix(".html")
    pdf_file = source.with_suffix(".pdf")
    map_file = source.with_suffix(".pages.json")
    page_map: dict[str, int] = {}
    executable = find_browser(args.browser)
    with tempfile.TemporaryDirectory(prefix="print-guide-pagination-") as directory:
        temp = Path(directory)
        html = temp / "guide.html"
        raw = temp / "guide.pdf"
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(executable_path=executable, headless=True)
            try:
                page = browser.new_page(viewport={"width": 1280, "height": 1000})
                external: list[str] = []
                page.on(
                    "request",
                    lambda request: (
                        external.append(request.url)
                        if request.url.startswith(("http://", "https://"))
                        else None
                    ),
                )
                for _iteration in range(5):
                    html.write_text(render_document(source, page_map), encoding="utf-8")
                    page.goto(html.as_uri())
                    page.evaluate("document.fonts.ready")
                    targets = set(
                        page.locator("[data-page-target]").evaluate_all(
                            "nodes => nodes.map(n => n.dataset.pageTarget)"
                        )
                    )
                    page.pdf(
                        path=str(raw), print_background=True, prefer_css_page_size=True, tagged=True
                    )
                    with pymupdf.open(raw) as document:
                        current = destinations(document, targets)
                        count = len(document)
                    if current == page_map:
                        break
                    page_map = current
                else:
                    raise ValueError("Pagination did not stabilize; no canonical PDF was published")
                if external:
                    raise ValueError(f"Document contacted external resources: {external}")
                browser_version = browser.version
            finally:
                browser.close()
        with pymupdf.open(raw) as document:
            assert destinations(document, targets) == page_map
            links = verify_links(document, page_map)
            outline = []
            # Use actual named destinations, never estimated paragraph heights.
            for token in heading_tokens(source.read_text(encoding="utf-8")):
                if token["id"] in page_map:
                    outline.append(
                        [
                            token["level"] - 1,
                            re.sub(r"<[^>]+>", "", token["name"]),
                            page_map[token["id"]],
                        ]
                    )
            document.set_toc(outline)
            for i, sheet in enumerate(document):
                if i:
                    sheet.insert_text(
                        (40, 26),
                        "Print Appliance | User Guide 1.4",
                        fontsize=8,
                        color=(0.33, 0.38, 0.43),
                    )
                    sheet.insert_textbox(
                        pymupdf.Rect(
                            40,
                            sheet.rect.height - 33,
                            sheet.rect.width - 40,
                            sheet.rect.height - 17,
                        ),
                        f"Trang {i + 1} / {count}",
                        fontsize=8,
                        align=2,
                        color=(0.33, 0.38, 0.43),
                    )
            document.set_metadata(
                {
                    "title": "Print Appliance — Hướng dẫn triển khai và sử dụng",
                    "author": "Luong Nguyen · Print Appliance",
                    "subject": "Tài liệu 1.4; sản phẩm 0.1.5",
                    "creator": "Print Appliance documentation tools",
                }
            )
            completed = temp / "completed.pdf"
            document.save(completed, deflate=True, garbage=4)
        with pymupdf.open(completed) as document:
            assert len(document) == count
            assert destinations(document, targets) == page_map
            assert verify_links(document, page_map) == links
        html_file.write_text(render_document(source, page_map), encoding="utf-8")
        pdf_file.write_bytes(completed.read_bytes())
        map_file.write_text(
            json.dumps(
                {
                    "document_version": "1.4",
                    "product_version": "0.1.5",
                    "source_sha256": hashlib.sha256(
                        source.read_text(encoding="utf-8").encode("utf-8")
                    ).hexdigest(),
                    "print_profile": PRINT_PROFILE,
                    "browser_version": browser_version,
                    "page_count": count,
                    "pages": page_map,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    print(
        f"Written {pdf_file}: {count} pages; {len(page_map)} destinations; {links} verified links; {_iteration + 1} pagination passes"
    )


if __name__ == "__main__":
    main()
