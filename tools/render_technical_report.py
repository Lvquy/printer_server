"""Render the report without changing application dependencies.

Run: uv run --no-project --with markdown==3.7 python tools/render_technical_report.py
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import unicodedata
import xml.etree.ElementTree as ET
from html import escape, unescape
from pathlib import Path

CSS = """
:root{color-scheme:light;--ink:#202832;--muted:#53616f;--line:#d9e1e8;--accent:#145a73}
*{box-sizing:border-box}html{scroll-behavior:smooth}body{margin:0;background:#f3f6f8;color:var(--ink);font:16px/1.7 system-ui,-apple-system,"Segoe UI",sans-serif}
.shell{max-width:1140px;margin:auto;padding:40px 42px 64px;background:white;min-height:100vh}
.report-bar{font-size:13px;color:var(--muted);border-bottom:1px solid var(--line);padding-bottom:16px;margin-bottom:28px;display:flex;gap:16px;flex-wrap:wrap}
h1,h2,h3,h4{line-height:1.3;scroll-margin-top:20px}h1{font-size:34px;letter-spacing:-.6px;margin-bottom:14px}h2{font-size:25px;margin:48px 0 18px;border-top:1px solid var(--line);padding-top:24px}h3{font-size:20px;margin-top:30px}h4{font-size:17px}
p,ul,ol{margin:12px 0}li{margin:5px 0}a{color:var(--accent);text-underline-offset:3px;overflow-wrap:anywhere}strong{font-weight:650}
code{font:13px/1.65 ui-monospace,SFMono-Regular,Consolas,"Liberation Mono",monospace;background:#edf2f5;padding:2px 4px;border-radius:3px;overflow-wrap:anywhere}
pre{border:1px solid var(--line);background:#f5f7f9;border-radius:7px;padding:15px 18px;overflow-x:auto;margin:18px 0;max-width:100%}pre code{background:none;padding:0;border-radius:0;white-space:pre;overflow-wrap:normal}
blockquote{margin:20px 0;padding:8px 20px;border-left:4px solid #b88728;background:#fff8e8}blockquote p{margin:6px 0}
table{border-collapse:collapse;width:100%;margin:20px 0;font-size:14px;table-layout:fixed}th,td{text-align:left;vertical-align:top;border:1px solid var(--line);padding:10px 12px;overflow-wrap:anywhere}th{background:#edf2f5;font-weight:650}tr:nth-child(even){background:#fafcfd}td code,th code{font-size:12px}
.toc{margin:30px 0;padding:16px 22px;background:#edf4f7;border:1px solid var(--line);border-radius:8px}.toc::before{content:none;display:block;font-weight:650;font-size:20px;margin-bottom:12px}.toc>ul{columns:1;column-gap:36px;padding-left:22px}.toc li{break-inside:avoid}.toc li li{font-size:13px}.toc a{text-decoration:none;display:flex;align-items:baseline;gap:12px}.toc a>span:first-child{flex:1}.toc a:hover{text-decoration:underline}
.report-cover{padding:24px 0 42px}.report-cover h1{font-size:42px;max-width:850px}.report-cover>p:first-of-type{font-size:22px;color:var(--accent)}.report-cover ul{list-style:none;padding:20px 24px;border:1px solid var(--line);border-radius:8px}.front-title{margin-top:40px}.figure-list,.table-list,.index-list{padding:8px 22px}.figure-list a,.table-list a{display:flex;align-items:baseline;gap:12px}.figure-list a>span:first-child,.table-list a>span:first-child{flex:1}.page-reference{display:inline-block;min-width:2.5em;flex-shrink:0;text-align:right;font-size:12px;font-variant-numeric:tabular-nums;color:var(--muted)}.index-list .page-reference{min-width:0;margin-left:4px}.index-list .page-reference::before{content:"tr. ";font-size:inherit}.table-caption{font-weight:650;font-size:14px;break-after:avoid;margin:24px 0 8px}.report-table table{margin-top:0}.report-table{break-inside:auto}
.report-end{border-top:1px solid var(--line);margin-top:44px;padding-top:16px;color:var(--muted);font-size:13px}
.report-diagram{margin:28px 0;break-inside:avoid;break-after:avoid}.diagram-viewport{overflow-x:auto;border:1px solid var(--line);border-radius:12px;background:white}.diagram-viewport:focus-visible{outline:3px solid var(--accent);outline-offset:3px}.diagram-viewport svg{display:block;width:100%;min-width:720px;max-width:920px;height:auto;margin:auto}.diagram-hint{display:none;margin-top:7px;color:var(--muted);font-size:12px}figure+p:has(>em:only-child){margin-top:-16px;color:var(--muted);font-size:14px}
@media(max-width:720px){.report-cover h1{font-size:28px}.report-cover>p:first-of-type{font-size:18px}.report-cover ul{padding:14px}.figure-list,.table-list,.index-list{padding:0}.diagram-hint{display:block}.shell{padding:24px 18px 40px}body{font-size:15px}h1{font-size:27px}h2{font-size:22px}.toc>ul{columns:1}th,td{padding:7px;font-size:12px}pre{padding:12px}}
@page{size:A4;margin:16mm 14mm 18mm}
@media print{.index-list{font-size:9pt}.index-list li{margin:2pt 0;line-height:1.3}.report-bar{display:none}.report-cover{min-height:230mm;padding:24mm 0 0;break-after:page}.report-cover h1{font-size:30pt}.report-cover>p:first-of-type{font-size:15pt}.report-cover ul{padding:14pt;font-size:11pt}.front-title,h2{break-before:auto}.page-reference{font-size:8pt}.table-caption{font-size:9pt}.figure-list,.table-list,.index-list{padding:0}.diagram-viewport{overflow:visible;border:none}.diagram-viewport svg{min-width:0;max-width:100%}.diagram-hint{display:none}figure+p:has(>em:only-child){font-size:9pt}body{background:white;font-size:10pt;line-height:1.45}.shell{padding:0;max-width:none}.report-bar{font-size:8pt}h1{font-size:24pt}h2{font-size:17pt;margin-top:26pt;padding-top:12pt}h3{font-size:13pt}h1,h2,h3,h4{break-after:avoid}p,li{orphans:3;widows:3}pre{font-size:8pt;padding:8pt;overflow:visible;break-inside:avoid}pre code{font-size:8pt;white-space:pre-wrap;overflow-wrap:anywhere}table{font-size:8.5pt;table-layout:fixed;break-inside:avoid}thead{display:table-header-group}tr{break-inside:avoid}th,td{padding:5pt}td code,th code{font-size:8pt}a{color:inherit}.toc{background:white;margin:12pt 0;padding:6pt 12pt}.toc li{margin:2pt 0}.report-diagram{margin:12pt 0;break-after:auto}.toc>ul{columns:1}.toc li li{font-size:8pt}.report-end{font-size:8pt}blockquote{background:white}html{scroll-behavior:auto}}
"""


def embed_diagrams(body: str, source: Path) -> str:
    """Inline trusted, local SVGs so downloaded HTML needs no sibling image files."""
    namespace = "http://www.w3.org/2000/svg"
    ET.register_namespace("", namespace)
    permitted = {
        "svg",
        "g",
        "rect",
        "text",
        "tspan",
        "polygon",
        "polyline",
        "line",
        "title",
        "desc",
        "defs",
        "marker",
        "path",
        "circle",
    }

    def replace(match: re.Match[str]) -> str:
        attrs = {key: unescape(value) for key, value in re.findall(r'(\w+)="([^"]*)"', match[1])}
        src = attrs.get("src", "")
        if not src.endswith(".svg"):
            raise ValueError(f"Report images must be local SVGs: {src}")
        root = source.resolve().parent
        path = (root / src).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise ValueError(f"Diagram is not a local report asset: {src}")
        svg = ET.fromstring(path.read_text(encoding="utf-8"))
        if svg.tag != f"{{{namespace}}}svg":
            raise ValueError(f"Not an SVG: {src}")
        for node in svg.iter():
            if node.tag.removeprefix(f"{{{namespace}}}") not in permitted:
                raise ValueError(f"Unsupported SVG element: {node.tag}")
            for key, value in node.attrib.items():
                if (
                    key.lower().startswith("on")
                    or "href" in key
                    or "url(" in value
                    and not value.startswith("url(#")
                ):
                    raise ValueError(f"Active/external SVG attribute: {key}")
        label = escape(attrs.get("alt", "Sơ đồ"), quote=True)
        embedded = ET.tostring(svg, encoding="unicode")
        number = re.match(r"(\d+)-", path.stem)
        target = f' id="figure-{int(number[1])}"' if number else ""
        return (
            f'<figure class="report-diagram"{target}>'
            f'<div class="diagram-viewport" tabindex="0" aria-label="{label}">{embedded}</div>'
            '<span class="diagram-hint">Màn hình nhỏ: vuốt ngang trong sơ đồ; dùng phím mũi tên khi vùng sơ đồ có focus.</span>'
            "</figure>"
        )

    body = re.sub(r'<p><a id="figure-\d+"></a></p>\s*', "", body)
    return re.sub(r"<p><img\s+([^>]+?)/></p>", replace, body)


def plain(value: str) -> str:
    return unescape(re.sub(r"<[^>]*>", "", value))


def heading_tokens(source: str) -> list[dict]:
    import markdown

    converter = markdown.Markdown(
        extensions=["tables", "fenced_code", "toc", "sane_lists"],
        extension_configs={"toc": {"toc_depth": "2-4"}},
    )
    converter.convert(source)
    result = []

    def visit(tokens: list[dict]) -> None:
        for token in tokens:
            result.append(token)
            visit(token["children"])

    visit(converter.toc_tokens)
    return result


def refresh_navigation(source: str, path: Path) -> str:
    tokens = heading_tokens(source)
    toc = "\n".join(
        "    " * (t["level"] - 2) + f"- [{plain(t['name'])}](#{t['id']})" for t in tokens if t["level"] == 2
    )
    figures = []
    for number, filename in re.findall(r"\]\(diagrams/(\d+)-([^)]+)\.svg\)", source):
        svg = ET.fromstring((path.parent / f"diagrams/{number}-{filename}.svg").read_text())
        title = svg.find("{http://www.w3.org/2000/svg}title").text
        figures.append(f"- [Hình {int(number)} — {title}](#figure-{int(number)})")
    tables = [
        f"- [{label}](#table-{number})"
        for number, label in re.findall(
            r'<a id="table-(\d+)"></a>\s*\n\n\*\*(Bảng [^\n]+)\*\*', source
        )
    ]
    index_specs = {
        "API": ["3.1", "C.1"],
        "API Guide": ["7.4"],
        "API key (khóa truy cập)": ["2.3", "7.1"],
        "Backup (sao lưu)": ["9.2", "D.2"],
        "Capability (khả năng in)": ["6.2", "B.2"],
        "Client": ["2.1", "7.1"],
        "Completed (hoàn thành)": ["5.2", "10.1"],
        "Correlation (dấu đối soát)": ["5.3", "8.3"],
        "CUPS": ["3.2", "5.3"],
        "Dữ liệu bền vững": ["3.3", "5.1"],
        "Driver": ["3.2", "B.1.2"],
        "Đổi mật khẩu quản trị": ["8.5", "C.3.1"],
        "Duplex (in hai mặt)": ["6.2", "B.2.1"],
        "FIFO": ["5.4"],
        "Fill / fit": ["6.3", "B.2.1"],
        "Git và clone project": ["A.1"],
        "Hàng đợi": ["2.2", "5.4"],
        "HTTP / HTTPS": ["3.1", "10.2"],
        "Hủy lệnh": ["8.2", "D.1.2"],
        "Khóa truy cập": ["2.3", "7.1"],
        "Lệnh in và mã lệnh": ["2.2", "5.2"],
        "Linux / EDATEC": ["4.2", "A.2"],
        "Mã lỗi": ["C.1.2", "D.1.4"],
        "Mã yêu cầu / chống trùng": ["2.3", "5.1"],
        "Máy in đăng ký": ["2.1", "6.1"],
        "Mặc định và ghi đè": ["6.2", "6.3"],
        "Offline": ["8.2", "D.1.4"],
        "PDF": ["3.4", "6.3"],
        "Quyền sử dụng máy": ["7.1", "C.3.1"],
        "Restore (phục hồi)": ["9.2", "D.2.3"],
        "Resume (tiếp tục)": ["8.2", "D.1.2"],
        "Rollback": ["9.3", "D.3"],
        "SessionStorage": ["7.2"],
        "Snapshot": ["2.2", "6.4"],
        "Thu hồi và đổi khóa": ["7.1", "C.3.1"],
        "TLS": ["10.2"],
        "Unknown (chưa rõ kết quả)": ["5.2", "8.3"],
        "ZPL": ["3.4", "C.2.3"],
    }
    index = []
    for term in sorted(
        index_specs if "<!-- BEGIN INDEX -->" in source else {},
        key=lambda s: "".join(
            c
            for c in unicodedata.normalize("NFKD", s.replace("Đ", "D"))
            if not unicodedata.combining(c)
        ).casefold(),
    ):
        links = []
        for section in index_specs[term]:
            token = next(
                t for t in tokens if re.match(re.escape(section) + r"(?: | —)", plain(t["name"]))
            )
            links.append(f"[{section}](#{token['id']})")
        index.append(f"- **{term}:** " + "; ".join(links))
    for name, content in (
        ("MAIN TOC", toc),
        ("FIGURE LIST", "\n".join(figures)),
        ("TABLE LIST", "\n".join(tables)),
        ("INDEX", "\n".join(index)),
    ):
        pattern = r"<!-- BEGIN " + name + r" -->.*?<!-- END " + name + r" -->"
        source, count = re.subn(
            pattern,
            lambda _, name=name, content=content: (
                f"<!-- BEGIN {name} -->\n{content}\n<!-- END {name} -->"
            ),
            source,
            flags=re.S,
        )
        if count != 1 and (name == "MAIN TOC" or count != 0):
            raise ValueError(f"Missing or duplicate navigation block: {name}")
    # Explicit named anchors make the ASCII links work in GitHub's Unicode-heading renderer.
    source = re.sub(r'(?m)^<a name="[^"]+" class="heading-anchor"></a>\n\n', "", source)
    headings = iter(tokens)
    lines = []
    fenced = False
    for line in source.splitlines():
        if line.startswith("```"):
            fenced = not fenced
        if not fenced and re.match(r"^#{2,4} ", line):
            token = next(headings)
            lines.extend([f'<a name="{token["id"]}" class="heading-anchor"></a>', ""])
        lines.append(line)
    if next(headings, None) is not None:
        raise ValueError("Heading parser and Markdown navigation disagree")
    return "\n".join(lines) + "\n"


def render_document(source: Path, pages: dict[str, int] | None = None) -> str:
    import markdown

    text = re.sub(
        r'(?m)^<a name="[^"]+" class="heading-anchor"></a>\n\n',
        "",
        source.read_text(encoding="utf-8"),
    )
    body = markdown.markdown(
        text,
        extensions=["tables", "fenced_code", "toc", "sane_lists"],
        extension_configs={"toc": {"toc_depth": "2-4"}},
    )
    if "<!-- COVER-END -->" in body:
        cover, body = body.split("<!-- COVER-END -->", 1)
        body = '<section class="report-cover">' + cover + "</section>" + body
    for name, css, target in (
        ("MAIN TOC", "toc", "table-of-contents"),
        ("FIGURE LIST", "figure-list", "list-of-figures"),
        ("TABLE LIST", "table-list", "list-of-tables"),
        ("INDEX", "index-list", "chi-muc-tra-cuu"),
    ):
        body = body.replace(
            f"<!-- BEGIN {name} -->", f'<nav class="{css}" aria-labelledby="{target}">'
        ).replace(f"<!-- END {name} -->", "</nav>")
    body = embed_diagrams(body, source)
    body = re.sub(
        r'<p><a id="(table-\d+)"></a></p>\s*<p><strong>(Bảng [^<]+)</strong></p>\s*(<table>.*?</table>)',
        lambda m: (
            f'<div class="report-table" id="{m[1]}"><p class="table-caption">{m[2]}</p>{m[3]}</div>'
        ),
        body,
        flags=re.S,
    )

    def reference(match: re.Match[str]) -> str:
        target, label = match[1], match[2]
        number = str((pages or {}).get(unescape(target), "—"))
        return f'<a href="#{target}"><span>{label}</span><span class="page-reference" data-page-target="{target}" title="Trang trong bản PDF chuẩn">{number}</span></a>'

    body = re.sub(r'<a href="#([^"]+)">(.*?)</a>', reference, body, flags=re.S)
    return f"""<!doctype html>
<html lang="vi"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="description" content="Triển khai Print Appliance trên Debian 12: cài đặt từng bước, cấu hình máy in, tích hợp API và vận hành.">
<title>Print Appliance — Hướng dẫn triển khai và sử dụng</title><style>{CSS}</style></head>
<body><div class="shell"><header class="report-bar"><span>PRINT APPLIANCE · HƯỚNG DẪN</span><span>Tài liệu 1.4 · Tiếng Việt</span></header>
<main>{body}</main><footer class="report-end">Luong Nguyen · Print Appliance. Bản HTML đọc được khi không có Internet; số trang trong mục lục dẫn tới bản PDF đi kèm.</footer></div></body></html>
"""


def main() -> None:
    parser = argparse.ArgumentParser(description="Render the self-contained technical report")
    root = Path(__file__).resolve().parents[1]
    parser.add_argument("--source", type=Path, default=root / "docs/technical-report.md")
    parser.add_argument("--output", type=Path, default=root / "docs/technical-report.html")
    parser.add_argument(
        "--refresh-content",
        action="store_true",
        help="Refresh linked Markdown TOC, lists and index",
    )
    args = parser.parse_args()
    try:
        import markdown  # noqa: F401
    except ImportError as exc:
        raise SystemExit(
            "Run with: uv run --no-project --with markdown==3.7 python tools/render_technical_report.py"
        ) from exc
    if args.refresh_content:
        args.source.write_text(
            refresh_navigation(args.source.read_text(encoding="utf-8"), args.source),
            encoding="utf-8",
        )
    pages = {}
    page_file = args.source.with_suffix(".pages.json")
    if page_file.exists():
        data = json.loads(page_file.read_text())
        if (
            data.get("source_sha256")
            == hashlib.sha256(args.source.read_text(encoding="utf-8").encode("utf-8")).hexdigest()
        ):
            pages = data["pages"]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(render_document(args.source, pages), encoding="utf-8")
    print(f"Written {args.output}")


if __name__ == "__main__":
    main()
