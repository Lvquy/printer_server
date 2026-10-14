"""Documentation diagrams must remain reproducible and self-contained."""

import re
import runpy
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SVG = "{http://www.w3.org/2000/svg}"
RENDERER = runpy.run_path(str(ROOT / "tools/render_technical_report.py"))
GENERATOR = runpy.run_path(str(ROOT / "tools/render_report_diagrams.py"))


def test_existing_technical_diagrams_remain_reproducible():
    diagrams = GENERATOR["diagrams"]()
    assert len(diagrams) == 10
    for diagram in diagrams:
        path = ROOT / f"docs/diagrams/{diagram.name}.svg"
        assert path.read_text() == diagram.svg()
        svg = ET.fromstring(path.read_text())
        assert svg.find(f"{SVG}title").text
        assert svg.find(f"{SVG}desc").text
        assert svg.attrib["role"] == "img"


def test_generated_html_embeds_connection_diagram_with_unique_ids():
    html = (ROOT / "docs/technical-report.html").read_text()
    assert html.count('<figure class="report-diagram"') == 1
    assert html.count("<svg ") == 1
    assert "<img " not in html
    assert "<script" not in html
    ids = re.findall(r'\bid="([^"]+)"', html)
    assert len(ids) == len(set(ids))
    assert 'id="connection-title"' in html
    assert 'id="connection-desc"' in html


def test_connection_drawio_and_svg_show_the_same_devices_and_connections():
    report = (ROOT / "docs/technical-report.md").read_text()
    assert "(diagrams/connection.svg)" in report
    assert "Sơ đồ draw.io có thể chỉnh sửa" not in report
    drawing = ET.parse(ROOT / "docs/diagrams/connection.drawio").getroot()
    assert drawing.tag == "mxfile"
    cells = drawing.findall(".//mxCell")
    svg = ET.parse(ROOT / "docs/diagrams/connection.svg").getroot()
    assert svg.attrib["role"] == "img"
    assert svg.find(f"{SVG}title").text
    assert svg.find(f"{SVG}desc").text
    texts = {node.text for node in svg.iter(f"{SVG}tspan")}
    for cell in cells:
        if cell.get("vertex") == "1":
            assert set(cell.get("value", "").splitlines()) <= texts
    connections = {(c.get("source"), c.get("target")) for c in cells if c.get("edge") == "1"}
    assert connections == {
        ("client", "app"), ("admin", "app"), ("app", "cups"),
        ("cups", "usb"), ("cups", "lan"),
    }
    assert len(list(svg.iter(f"{SVG}polyline"))) == len(connections)


@pytest.mark.parametrize(
    "unsafe",
    [
        "<script>alert(1)</script>",
        '<rect onclick="alert(1)"/>',
        '<rect fill="url(https://example.com/asset)"/>',
        '<use href="https://example.com/asset"/>',
    ],
)
def test_inline_svg_rejects_active_or_external_assets(tmp_path, unsafe):
    source = tmp_path / "report.md"
    source.write_text("report")
    (tmp_path / "image.svg").write_text(f'<svg xmlns="http://www.w3.org/2000/svg">{unsafe}</svg>')
    with pytest.raises(ValueError):
        RENDERER["embed_diagrams"]('<p><img alt="Flow" src="image.svg" /></p>', source)


def test_inline_svg_rejects_path_outside_report(tmp_path):
    directory = tmp_path / "docs"
    directory.mkdir()
    source = directory / "report.md"
    source.write_text("report")
    (tmp_path / "external.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg"/>')
    with pytest.raises(ValueError):
        RENDERER["embed_diagrams"]('<p><img alt="Flow" src="../external.svg" /></p>', source)


def test_inline_svg_preserves_accessibility_and_escapes_alt(tmp_path):
    source = tmp_path / "report.md"
    source.write_text("report")
    diagram = GENERATOR["diagrams"]()[0]
    (tmp_path / "image.svg").write_text(diagram.svg())
    html = RENDERER["embed_diagrams"](
        '<p><img alt="Client &amp; admin" src="image.svg" /></p>', source
    )
    assert 'aria-label="Client &amp; admin"' in html
    assert "<img" not in html
    assert "<svg " in html
    assert f'aria-labelledby="{diagram.name}-title {diagram.name}-desc"' in html
