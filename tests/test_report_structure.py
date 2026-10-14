"""Guide structure and page-reference integrity without PDF/browser dependencies."""

import hashlib
import json
import re
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "docs/technical-report.md"


class Navigation(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = []
        self.links = []
        self.references = []
        self.files = []

    def handle_starttag(self, tag, attrs):
        data = dict(attrs)
        if "id" in data:
            self.ids.append(data["id"])
        if tag == "a":
            href = data.get("href", "")
            if href.startswith("#"):
                self.links.append(href[1:])
            elif href and not href.startswith(("http://", "https://")):
                self.files.append(href)
        if "data-page-target" in data:
            self.references.append(data["data-page-target"])


def test_guide_has_six_practical_sections_and_valid_navigation():
    source = SOURCE.read_text()
    assert re.findall(r"^## (.+)$", source, re.M) == [
        "1. Kiến trúc và luồng in",
        "2. Yêu cầu trước khi triển khai",
        "3. Cài phần mềm trên thiết bị",
        "4. Cấu hình máy in và kiểm tra in thử",
        "5. Chức năng và tích hợp API",
        "6. Vận hành và xử lý lỗi cơ bản",
    ]
    assert "<!-- BEGIN MAIN TOC -->" in source
    assert "<!-- END MAIN TOC -->" in source
    assert "[TOC]" not in source
    assert "Phụ lục" not in source
    assert "Chỉ mục tra cứu" not in source
    assert len(source.splitlines()) < 750
    # Explicit anchors support GitHub's differing Vietnamese heading slugs.
    anchors = set(re.findall(r'<a (?:id|name)="([^"]+)"', source))
    fragments = set(re.findall(r"\]\(#([^)]+)\)", source))
    assert len(fragments) == 6
    assert fragments <= anchors
    navigation = Navigation()
    navigation.feed(SOURCE.with_suffix(".html").read_text())
    assert len(navigation.ids) == len(set(navigation.ids))
    assert set(navigation.links) <= set(navigation.ids)
    assert set(navigation.references) == fragments
    assert all((SOURCE.parent / path).is_file() for path in navigation.files)


def test_technical_guide_has_installation_commands_and_safety_checks():
    source = SOURCE.read_text()
    assert len(re.findall(r"^### 3\.\d+ ", source, re.M)) == 4
    for text in (
        "sudo apt-get install git", "sudo ./install.sh", "admin12345678",
        "sudo systemctl start print-appliance", "admin-password",
        "http://192.168.0.123:8081/", "systemctl is-active",
        "Kết quả cần đạt", "Chạy trên máy cá nhân", "trong phiên SSH trên Debian",
        "mã, file, tên file và mọi tham số", "không tự in lại",
        "Hủy không thu hồi được giấy đã in", "stop-printer",
    ):
        assert text in source
    assert "script dừng để bảo vệ dữ liệu" in source
    assert "Tiêu đề" in source


def test_canonical_pdf_page_references_are_current():
    data = json.loads(SOURCE.with_suffix(".pages.json").read_text())
    assert (
        data["source_sha256"]
        == hashlib.sha256(SOURCE.read_text(encoding="utf-8").encode("utf-8")).hexdigest()
    )
    assert data["document_version"] == "1.4"
    assert data["product_version"] == "0.1.5"
    assert 1 <= data["page_count"] <= 16
    assert len(data["pages"]) == 6
    assert all(isinstance(n, int) and 1 <= n <= data["page_count"] for n in data["pages"].values())
    html = SOURCE.with_suffix(".html").read_text()
    for target, page in data["pages"].items():
        assert (
            f'data-page-target="{target}" title="Trang trong bản PDF chuẩn">{page}</span>' in html
        )
    with SOURCE.with_suffix(".pdf").open("rb") as file:
        assert file.read(5) == b"%PDF-"


def test_current_guide_features_and_example_addresses():
    source = SOURCE.read_text()
    features = source.split("## 5. Chức năng và tích hợp API", 1)[1].split("## 6.", 1)[0]
    assert "chống gửi trùng" in features
    assert "Idempotency-Key" in features
    assert "Authorization: Bearer" in features
    assert "0.1.5" not in source
    assert "theo phiên bản" not in source
    assert "| Phiên bản |" not in source
    assert "/client#api-guide" in source
    assert "Đổi mật khẩu quản trị" in source
    assert "Mọi địa chỉ ví dụ" in source
    assert "ssh pi@raspberrypi" in source
    assert "ssh pi@192.168.0.123" in source
    assert "http://192.168.0.123:8081/client" in source
    assert "không phải cấu hình mặc định bắt buộc" in source
    assert "không được mã hóa" in source
    html = SOURCE.with_suffix(".html").read_text()
    assert "Tài liệu 1.4" in html
    assert "Tài liệu 1.3" not in html
    assert "Sản phẩm 0.1.5" not in html


def test_specific_printer_and_driver_details_are_examples_only():
    lines = SOURCE.read_text().splitlines()
    for detail in ("Canon", "LBP6230dw", "UFRII LT"):
        mentions = [line for line in lines if detail in line]
        assert mentions
        assert all(line.startswith("> **Ví dụ") for line in mentions)
    assert "không phải model hay driver bắt buộc" in SOURCE.read_text()


def test_pdf_checkout_preserves_binary_bytes():
    attributes = (ROOT / ".gitattributes").read_text()
    assert "*.pdf binary" in attributes.splitlines()


def test_api_examples_match_contract_and_shell_syntax():
    import subprocess

    source = SOURCE.read_text()
    api = source.split("## 5.", 1)[1].split("## 6.", 1)[0]
    contract = json.loads((SOURCE.parent.parent / "frontend/src/client-api.json").read_text())
    examples = [json.loads(block) for block in re.findall(r"```json\n(.*?)\n```", api, re.S)]
    for endpoint in contract["endpoints"]:
        assert f"**{endpoint['method']} `{endpoint['path']}`**" in api
        for response in endpoint["responses"]:
            assert response["example"] in examples
    for block in re.findall(r"```sh\n(.*?)\n```", source, re.S):
        subprocess.run(["bash", "-n"], input=block, text=True, check=True)
    assert "không chạy lại dòng tạo REQUEST_ID" in api
    assert "có thể in ra giấy thật" in api
