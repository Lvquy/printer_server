"""Generate the report's offline SVG diagrams using only the Python standard library.

Run: python tools/render_report_diagrams.py
"""

from __future__ import annotations

from html import escape
from pathlib import Path

COLORS = {
    "normal": ("#f3f6f9", "#c9d4df", "#243849"),
    "blue": ("#edf5fb", "#8cb6d2", "#174d71"),
    "green": ("#edf7f2", "#90bba4", "#245c43"),
    "amber": ("#fff7e6", "#d8b56e", "#785715"),
    "red": ("#fcf0ef", "#d4a09b", "#853d36"),
}


class Diagram:
    def __init__(self, name: str, title: str, description: str, height: int) -> None:
        self.name, self.title, self.description = name, title, description
        self.height = height
        self.edges: list[str] = []
        self.nodes: list[str] = []

    def text(
        self,
        x: int,
        y: int,
        lines: list[str],
        *,
        size: int = 17,
        color: str = "#243849",
        bold: bool = False,
        anchor: str = "middle",
    ) -> str:
        weight = 650 if bold else 400
        spans = "".join(
            f'<tspan x="{x}" dy="{0 if i == 0 else size + 7}">{escape(line)}</tspan>'
            for i, line in enumerate(lines)
        )
        return (
            f'<text x="{x}" y="{y}" fill="{color}" font-size="{size}" '
            f'font-weight="{weight}" text-anchor="{anchor}">{spans}</text>'
        )

    def box(
        self,
        x: int,
        y: int,
        w: int,
        h: int,
        title: str,
        lines: list[str] | None = None,
        tone: str = "normal",
    ) -> None:
        fill, stroke, ink = COLORS[tone]
        lines = lines or []
        top = y + (h - 22 - len(lines) * 24) // 2 + 17
        content = self.text(x + w // 2, top, [title], color=ink, bold=True)
        if lines:
            content += self.text(x + w // 2, top + 28, lines, size=16, color=ink)
        self.nodes.append(
            '<g data-box="true">'
            f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="12" '
            f'fill="{fill}" stroke="{stroke}" stroke-width="1.5"/>'
            f"{content}</g>"
        )

    def diamond(self, x: int, y: int, w: int, h: int, lines: list[str]) -> None:
        self.nodes.append(
            f'<polygon points="{x + w // 2},{y} {x + w},{y + h // 2} '
            f'{x + w // 2},{y + h} {x},{y + h // 2}" fill="#edf5fb" '
            'stroke="#8cb6d2" stroke-width="1.5"/>'
        )
        self.nodes.append(self.text(x + w // 2, y + h // 2 - (len(lines) - 1) * 12 + 6, lines))

    def arrow(
        self,
        points: list[tuple[int, int]],
        label: str = "",
        at: tuple[int, int] | None = None,
        *,
        bidirectional: bool = False,
    ) -> None:
        start = f' marker-start="url(#{self.name}-arrow)"' if bidirectional else ""
        coordinates = " ".join(f"{x},{y}" for x, y in points)
        self.edges.append(
            f'<polyline points="{coordinates}" fill="none" stroke="#617687" stroke-width="1.8" '
            f'marker-end="url(#{self.name}-arrow)"{start}/>'
        )
        if label and at:
            self.nodes.append(self.text(*at, [label], size=15))

    def note(self, x: int, y: int, lines: list[str]) -> None:
        self.nodes.append(self.text(x, y, lines, size=15, color="#53616f"))

    def line(self, x: int, y: int, end_y: int) -> None:
        self.edges.append(
            f'<line x1="{x}" y1="{y}" x2="{x}" y2="{end_y}" stroke="#b3c3cf" '
            'stroke-width="1.5" stroke-dasharray="5 5"/>'
        )

    def svg(self) -> str:
        return (
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 920 {self.height}" '
            f'width="920" height="{self.height}" role="img" '
            f'aria-labelledby="{self.name}-title {self.name}-desc" '
            'font-family="system-ui, -apple-system, Segoe UI, sans-serif">\n'
            f'<title id="{self.name}-title">{escape(self.title)}</title>\n'
            f'<desc id="{self.name}-desc">{escape(self.description)}</desc>\n'
            f'<defs><marker id="{self.name}-arrow" viewBox="0 0 10 10" refX="9" refY="5" '
            'markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
            '<path d="M 0 0 L 10 5 L 0 10 z" fill="#617687"/></marker></defs>\n'
            '<rect width="920" height="100%" rx="16" fill="#fff"/>\n'
            + "\n".join(self.edges + self.nodes)
            + "\n</svg>\n"
        )


def diagrams() -> list[Diagram]:
    result = []
    d = Diagram(
        "01-architecture",
        "Kiến trúc tổng thể",
        "Ứng dụng gửi yêu cầu. Quản trị dùng phiên đăng nhập. Thiết bị lưu dữ liệu và điều phối; CUPS dùng driver gửi tới máy.",
        650,
    )
    d.box(40, 24, 390, 92, "Ứng dụng gửi yêu cầu", ["Khóa truy cập + mã yêu cầu"], "blue")
    d.box(490, 24, 390, 92, "Trang quản trị", ["Phiên đăng nhập + bảo vệ thao tác"], "blue")
    d.box(
        230,
        178,
        460,
        85,
        "Thiết bị quản lý in trên Linux",
        ["Xác thực • quyền • tiếp nhận • quản trị"],
    )
    d.arrow([(235, 116), (235, 145), (350, 145), (350, 178)])
    d.arrow([(685, 116), (685, 145), (570, 145), (570, 178)])
    d.box(40, 310, 340, 92, "Kho dữ liệu bền vững", ["Lệnh • file • cấu hình • lịch sử"], "green")
    d.box(480, 310, 400, 92, "Điều phối hàng đợi", ["Theo từng máy in đăng ký"])
    d.arrow([(350, 263), (350, 286), (210, 286), (210, 310)])
    d.arrow([(570, 263), (570, 286), (680, 286), (680, 310)])
    d.arrow([(380, 356), (480, 356)], "Đọc / ghi", (430, 337), bidirectional=True)
    d.box(480, 459, 400, 86, "Hệ thống in CUPS + driver", ["Xử lý nội dung gửi tới máy"], "blue")
    d.arrow([(680, 402), (680, 459)])
    d.box(40, 459, 340, 86, "Máy in vật lý", ["USB hoặc mạng LAN"], "green")
    d.arrow([(480, 502), (380, 502)], "Truyền nội dung", (430, 573))
    d.note(460, 612, ["Thiết bị quản lý yêu cầu; CUPS quản lý driver và truyền tới máy."])
    result.append(d)

    d = Diagram(
        "02-admission",
        "Tiếp nhận và chống gửi trùng",
        "Cùng mã và nội dung trả lệnh cũ; khác nội dung báo xung đột. Yêu cầu mới phải hợp lệ và được lưu an toàn trước khi báo đã nhận.",
        760,
    )
    d.box(255, 20, 410, 78, "Nhận yêu cầu in mới", ["Khóa truy cập + mã yêu cầu + file"], "blue")
    d.box(255, 132, 410, 80, "Xác thực và kiểm đầu vào", ["Tạo dấu nhận dạng của yêu cầu"])
    d.arrow([(460, 98), (460, 132)])
    d.diamond(310, 245, 300, 120, ["Mã yêu cầu đã có?", "Trong phạm vi ứng dụng gửi"])
    d.arrow([(460, 212), (460, 245)])
    d.box(30, 250, 220, 112, "Đã có", ["Dấu khớp → lệnh cũ", "Dấu khác → lỗi 409"], "amber")
    d.arrow([(310, 305), (250, 305)], "Có", (278, 283))
    d.box(
        255,
        408,
        410,
        100,
        "Kiểm tra yêu cầu mới",
        ["Được cấp máy • đúng cấu hình", "Lựa chọn hợp lệ • còn sức chứa"],
        "blue",
    )
    d.arrow([(460, 365), (460, 408)], "Chưa có", (530, 390))
    d.box(710, 408, 185, 100, "Không hợp lệ", ["Trả mã lỗi", "Không nhận lệnh"], "red")
    d.arrow([(665, 458), (710, 458)])
    d.box(
        255,
        552,
        410,
        95,
        "Lưu bền vững",
        ["Lưu file và lệnh trước phản hồi", "Giữ cấu hình tại thời điểm nhận"],
        "green",
    )
    d.arrow([(460, 508), (460, 552)], "Đạt", (500, 536))
    d.box(255, 685, 410, 55, "202 + mã lệnh: đã nhận, chưa phải đã in", tone="green")
    d.arrow([(460, 647), (460, 685)])
    d.note(783, 610, ["Quyền được kiểm lại", "khi ghi nhận lệnh."])
    result.append(d)

    d = Diagram(
        "03-job-lifecycle",
        "Vòng đời lệnh in",
        "Chờ, giao, theo dõi rồi ghi kết quả. Lệnh giữ cần quản trị cho tiếp tục. Unknown cần đối soát, không tự gửi lại. Sơ đồ rút gọn.",
        640,
    )
    d.box(35, 50, 230, 86, "queued", ["Đã nhận, chờ FIFO"], "blue")
    d.box(345, 50, 230, 86, "submitting", ["Ý định giao đã lưu"], "blue")
    d.box(655, 50, 230, 86, "submitted", ["Có mã CUPS; theo dõi"], "blue")
    d.arrow([(265, 93), (345, 93)])
    d.arrow([(575, 93), (655, 93)])
    d.box(35, 225, 230, 108, "held", ["Giữ do lỗi hoặc cấu hình", "Quản trị cho tiếp tục"], "amber")
    d.arrow([(150, 136), (150, 225)], "Giữ trước giao", (235, 184))
    d.arrow([(65, 225), (65, 172), (55, 172), (55, 136)])
    d.box(345, 225, 230, 108, "unknown", ["Chưa rõ kết quả", "Chặn tiến trình máy"], "red")
    d.arrow([(460, 136), (460, 225)])
    d.arrow([(770, 136), (770, 180), (530, 180), (530, 225)])
    d.box(655, 225, 230, 108, "Kết quả cuối", ["completed / failed", "canceled"], "green")
    d.arrow([(840, 136), (905, 136), (905, 279), (885, 279)])
    d.arrow([(575, 279), (655, 279)], "Đối soát", (615, 260))
    d.box(
        95,
        395,
        730,
        88,
        "Các nhánh khác",
        [
            "queued / held có thể hủy trước giao; lỗi xác định có thể kết thúc failed.",
            "Hủy sau giao cần đối chiếu kết quả CUPS, không hứa thu hồi giấy.",
        ],
    )
    d.box(
        95,
        528,
        730,
        78,
        "Giới hạn bảo đảm",
        ["completed là CUPS báo hoàn thành, không phải chứng minh đã ra giấy."],
        "amber",
    )
    result.append(d)

    d = Diagram(
        "04-cups-handoff",
        "Giao CUPS có kiểm soát",
        "Lưu lệnh trước phản hồi. Giao có giữ, lưu mã CUPS rồi mới cho in. Kiểm mã và dấu nhận diện để theo dõi; sự cố cần đối soát.",
        705,
    )
    for x, label in ((115, "Ứng dụng"), (345, "Điều phối"), (575, "Kho dữ liệu"), (805, "CUPS")):
        d.box(x - 90, 20, 180, 60, label, tone="blue")
        d.line(x, 90, 610)
    d.arrow([(115, 123), (345, 123)], "Gửi file + mã yêu cầu", (230, 109))
    d.arrow([(345, 174), (575, 174)], "Lưu bền vững", (460, 160))
    d.arrow([(345, 225), (115, 225)], "202 + mã lệnh", (230, 211))
    d.arrow([(345, 280), (575, 280)], "submitting + dấu nhận diện", (460, 266))
    d.arrow([(345, 335), (805, 335)], "Giao lệnh nhưng giữ chưa in", (575, 321))
    d.arrow([(805, 390), (345, 390)], "Mã lệnh CUPS", (575, 376))
    d.arrow([(345, 445), (575, 445)], "Lưu mã CUPS + submitted", (460, 431))
    d.arrow([(345, 500), (805, 500)], "Cho phép in khi đủ điều kiện", (575, 486))
    d.arrow([(345, 555), (805, 555)], "Kiểm mã CUPS + dấu nhận diện", (575, 541))
    d.arrow([(805, 605), (345, 605)], "Trạng thái → lưu kết quả", (575, 591))
    d.box(75, 637, 770, 52, "Đứt giữa các bước → đối soát; không tự gửi lại nội dung", tone="amber")
    result.append(d)

    d = Diagram(
        "05-printer-fifo",
        "Hàng đợi theo máy",
        "Mỗi máy đăng ký có thứ tự riêng. Các máy khác xử lý độc lập. Unknown chặn máy liên quan; nhiều đăng ký một máy thật không tạo thứ tự chung.",
        485,
    )
    for y, label, jobs, tone in (
        (40, "Máy đăng ký A", "A1 → A2 → A3", "green"),
        (175, "Máy đăng ký B", "B1 → B2 → B3", "blue"),
        (310, "Máy đăng ký C", "C1 unknown • C2 chờ", "amber"),
    ):
        d.box(25, y, 235, 90, label, ["Thứ tự tiếp nhận: FIFO"])
        d.box(
            315,
            y,
            270,
            90,
            jobs,
            ["Một lệnh đang giao / theo dõi" if label[-1] != "C" else "Chặn máy C; không gửi lại"],
            tone,
        )
        d.box(
            650,
            y,
            245,
            90,
            "CUPS / máy " + label[-1],
            ["Xử lý độc lập" if label[-1] != "C" else "Đối soát thủ công"],
            tone,
        )
        d.arrow([(260, y + 45), (315, y + 45)])
        if label[-1] != "C":
            d.arrow([(585, y + 45), (650, y + 45)])
    d.note(
        460,
        448,
        ["Tối đa 4 máy mỗi vòng. Hai đăng ký cùng một máy vật lý không có FIFO chung."],
    )
    result.append(d)

    d = Diagram(
        "06-option-permissions",
        "Lựa chọn và quyền sử dụng",
        "Khả năng máy được kết hợp với mặc định và quyền. Ứng dụng chỉ chọn giá trị được cấp; thiết bị kiểm tổ hợp và giữ cấu hình cho lệnh.",
        605,
    )
    d.box(
        60,
        25,
        340,
        96,
        "CUPS và driver",
        ["Lựa chọn • mặc định • ràng buộc", "Khả năng máy được công bố"],
        "blue",
    )
    d.box(
        520,
        25,
        340,
        96,
        "Quản trị máy",
        ["Mặc định trên thiết bị", "Các giá trị ứng dụng được chọn"],
        "blue",
    )
    d.box(
        240,
        181,
        440,
        96,
        "Bộ lựa chọn theo máy",
        ["Chỉ lựa chọn phù hợp và được cấp", "Dấu cấu hình để nhận biết thay đổi"],
    )
    d.arrow([(230, 121), (230, 150), (350, 150), (350, 181)])
    d.arrow([(690, 121), (690, 150), (570, 150), (570, 181)])
    d.box(
        60,
        337,
        340,
        94,
        "Ứng dụng chọn",
        ["Dùng mặc định → không ghi đè", "Chọn khác → phải được cấp quyền"],
    )
    d.box(
        520,
        337,
        340,
        94,
        "Thiết bị kiểm tra",
        ["Đúng giá trị • tổ hợp • cấu hình", "Không chỉ dựa vào biểu mẫu"],
        "green",
    )
    d.arrow([(350, 277), (350, 303), (230, 303), (230, 337)])
    d.arrow([(400, 384), (520, 384)])
    d.box(
        240,
        491,
        440,
        85,
        "Cấu hình giữ cho lệnh",
        ["Lựa chọn không tự đổi sau khi nhận"],
        "green",
    )
    d.arrow([(690, 431), (690, 461), (570, 461), (570, 491)])
    d.note(150, 520, ["ZPL: gửi nguyên nội dung", "Không áp mặc định PDF."])
    result.append(d)

    d = Diagram(
        "07-client-workflow",
        "Cấp quyền và gửi lệnh",
        "Quản trị cấp máy và chuyển khóa. Client chọn file, gửi rồi xem kết quả. Mất phản hồi cần giữ yêu cầu gốc; tải lại không tự gửi.",
        650,
    )
    d.box(
        245,
        20,
        430,
        80,
        "Quản trị tạo client và cấp máy",
        ["Sao chép khóa truy cập một lần"],
        "blue",
    )
    d.box(245, 138, 430, 80, "Ứng dụng kết nối bằng khóa", ["Tải máy được cấp + lịch sử"])
    d.arrow([(460, 100), (460, 138)])
    d.box(
        245,
        258,
        430,
        82,
        "Chọn máy • file • số bản • lựa chọn",
        ["Bấm gửi = yêu cầu in thật"],
        "amber",
    )
    d.arrow([(460, 218), (460, 258)])
    d.box(720, 250, 175, 102, "Lỗi xác định", ["Đọc thông báo lỗi", "Không tự đổi mã"], "red")
    d.arrow([(675, 300), (720, 300)], "Lỗi", (697, 282))
    d.diamond(335, 382, 250, 102, ["Có phản hồi nhận?"])
    d.arrow([(460, 340), (460, 382)])
    d.box(
        35,
        527,
        385,
        96,
        "Có mã lệnh",
        ["Xem trạng thái / lịch sử", "Không tạo lệnh mới để kiểm tra"],
        "green",
    )
    d.arrow([(335, 433), (225, 433), (225, 527)], "Có", (265, 412))
    d.box(
        500,
        527,
        385,
        96,
        "Mất phản hồi",
        ["Còn tab → gửi lại yêu cầu gốc", "Mất dữ liệu → lịch sử + đối soát"],
        "amber",
    )
    d.arrow([(585, 433), (690, 433), (690, 527)], "Chưa rõ", (658, 412))
    d.note(125, 384, ["Tab ghi nhớ khóa truy cập.", "Tải lại không tự gửi lệnh."])
    result.append(d)

    d = Diagram(
        "08-unknown-resolution",
        "Đối soát chưa rõ kết quả",
        "Giữ file và chặn máy; kiểm mã CUPS, dấu nhận diện và trạng thái. Bằng chứng đủ thì quản trị ghi kết quả có lý do. Không tự gửi lại file.",
        655,
    )
    d.box(
        255,
        20,
        410,
        85,
        "unknown: giữ file, chặn máy",
        ["Thiếu lịch sử không phải là chưa in"],
        "red",
    )
    d.box(
        255, 147, 410, 85, "Đọc CUPS có xác thực", ["Mã CUPS + dấu nhận diện + trạng thái"], "blue"
    )
    d.arrow([(460, 105), (460, 147)])
    d.diamond(305, 275, 310, 120, ["Đúng lệnh cần đối soát", "và đã kết thúc?"])
    d.arrow([(460, 232), (460, 275)])
    d.box(
        35,
        442,
        390,
        108,
        "Bằng chứng đủ",
        ["Quản trị ghi kết quả + lý do", "Hoàn thành / lỗi / đã hủy"],
        "green",
    )
    d.arrow([(305, 335), (230, 335), (230, 442)], "Có", (258, 314))
    d.box(
        495,
        442,
        390,
        108,
        "Chưa đủ bằng chứng",
        ["Chưa kết thúc: xử lý và kiểm lại", "Thiếu lịch sử: đối soát thực tế"],
        "amber",
    )
    d.arrow([(615, 335), (690, 335), (690, 442)], "Không", (662, 314))
    d.note(
        460,
        602,
        [
            "Sai dấu nhận diện: giữ unknown, không lấy kết quả từ một lệnh CUPS khác.",
            "Ghi kết quả có bằng chứng, không tự gửi lại file.",
        ],
    )
    result.append(d)

    d = Diagram(
        "09-backup-restore",
        "Sao lưu và phục hồi",
        "Backup khi được phép; kiểm lệnh, dừng, bảo vệ bản sao và lưu ngoài thiết bị. Restore cần đối soát, giữ dừng và kiểm trước khi cho chạy.",
        715,
    )
    d.box(35, 20, 395, 62, "BACKUP ĐẦY ĐỦ", tone="blue")
    d.box(490, 20, 395, 62, "RESTORE CÓ KIỂM SOÁT", tone="amber")
    left = [
        ("Maintenance được phép", ["App không còn lệnh chưa kết thúc", "CUPS không có lệnh chờ"]),
        (
            "Stop service và kiểm lại",
            ["Tránh bỏ sót lệnh vừa nhận", "Sao lưu nhất quán, không chép rời file"],
        ),
        (
            "Bản sao được bảo vệ",
            ["Lệnh + file + cấu hình + phiên bản", "Kiểm bản sao • lưu ngoài thiết bị"],
        ),
        (
            "Start service sau backup",
            ["Kiểm dịch vụ sẵn sàng", "Driver có kế hoạch sao lưu riêng"],
        ),
    ]
    right = [
        (
            "Duyệt quyết định restore",
            ["Đối soát lệnh nhận sau backup", "Backup trạng thái hiện tại"],
        ),
        ("Giữ service dừng", ["Phục hồi dữ liệu đúng phạm vi", "Dùng phiên bản tương thích"]),
        (
            "Kiểm trước khi cho chạy",
            ["Toàn vẹn • cấu hình • lệnh chờ", "Chưa đạt → giữ dừng, đối soát"],
        ),
        (
            "Start chỉ khi đủ điều kiện",
            ["Không coi backup cũ là chưa in", "Giữ bản trước phục hồi"],
        ),
    ]
    for x, blocks in ((35, left), (490, right)):
        for i, (title, lines) in enumerate(blocks):
            y = 119 + i * 135
            d.box(x, y, 395, 98, title, lines, "normal" if x == 35 else "amber")
            if i:
                d.arrow([(x + 197, y - 37), (x + 197, y)])
    d.note(
        460,
        691,
        ["Bản sao chỉ có thông tin lệnh không đủ phục hồi file. Cần đối soát kết quả in."],
    )
    result.append(d)

    d = Diagram(
        "10-git-rollout",
        "Cập nhật và rollback",
        "Chọn bản đã kiểm tra, chuẩn bị cài. Kiểm dung lượng, lệnh và backup. Cài rồi kiểm dịch vụ/cấu hình trước khi chốt. Rollback phụ thuộc dữ liệu tương thích.",
        670,
    )
    d.box(
        40,
        25,
        360,
        92,
        "Chọn phiên bản cần cài",
        ["Bản đã được kiểm tra", "Ghi lại bản mới và bản hiện tại"],
        "blue",
    )
    d.box(
        520,
        25,
        360,
        92,
        "Chuẩn bị cài đặt",
        ["Cùng phiên bản đã chọn", "Nguồn đáng tin • kiểm gói cài"],
    )
    d.arrow([(400, 71), (520, 71)])
    d.box(
        235,
        171,
        450,
        82,
        "Linux: kiểm trước update",
        ["Dung lượng • lệnh chờ • bảo trì • backup"],
        "amber",
    )
    d.arrow([(700, 117), (700, 144), (570, 144), (570, 171)])
    d.box(235, 296, 450, 82, "Cài bản được phép", ["Đúng phiên bản; giữ service dừng"])
    d.arrow([(460, 253), (460, 296)])
    d.diamond(315, 421, 290, 110, ["Kiểm tra sau cài đạt?", "Dịch vụ • đăng nhập • cấu hình"])
    d.arrow([(460, 378), (460, 421)])
    d.box(35, 572, 390, 78, "Ghi phiên bản đã chạy", ["Giữ bản trước; dọn khóa tạm"], "green")
    d.arrow([(315, 476), (230, 476), (230, 572)], "Đạt", (270, 455))
    d.box(
        495,
        572,
        390,
        78,
        "Rollback được duyệt",
        ["Tương thích dữ liệu? Hay cần đối soát?"],
        "amber",
    )
    d.arrow([(605, 476), (690, 476), (690, 572)], "Lỗi", (650, 455))
    result.append(d)
    return result


def main() -> None:
    destination = Path(__file__).resolve().parents[1] / "docs/diagrams"
    destination.mkdir(parents=True, exist_ok=True)
    for diagram in diagrams():
        (destination / f"{diagram.name}.svg").write_text(diagram.svg(), encoding="utf-8")
    print(f"Written {len(diagrams())} SVG diagrams in {destination}")


if __name__ == "__main__":
    main()
