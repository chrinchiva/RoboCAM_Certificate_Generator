"""Render the RoboCAM certificate on the original Word artwork."""

import os
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from xml.sax.saxutils import escape

import qrcode
from fontTools.ttLib import TTFont as FontFile
from reportlab.lib.colors import HexColor, black, white
from reportlab.lib.enums import TA_JUSTIFY
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import Paragraph

from records import COURSE, ID, NAME, Student, display_date

ASSETS = Path(__file__).resolve().parent / "assets"
PAGE_WIDTH, PAGE_HEIGHT = 297 * mm, 210 * mm
CENTER = PAGE_WIDTH / 2
GOLD = HexColor("#B27409")
REGULAR, BOLD = "RoboCAMTimesRegular", "RoboCAMTimesBold"
NAME_WIDTH = 366
NAME_MIN_SIZE = 18
BODY_X, BODY_WIDTH = 108.55, 627
BODY_FIRST_BASELINE, BODY_LEADING, BODY_MAX_LINES = 353.45, 18.65, 3
QR_CENTER, QR_TOP, QR_SIZE = 157.0, 405.2, 91.0
ID_WIDTH = 149
ISSUE_RIGHT = 765.2
LEAD_INSTRUCTOR = "Lead Instructor, RoboCAM Tech Solutions"
SENIOR_INSTRUCTOR = "Senior Technical Instructor, Preah Kossomak Polytechnic Institute (PPI)"
ISSUER = "Issued by RoboCAM Tech Solutions"
INSTRUCTOR = "CHRIN CHIVA"
BODY_SUFFIX = (
    "in Phnom Penh, Cambodia, and demonstrating strong knowledge in ladder "
    "programming, troubleshooting, and industrial automation applications."
)

# Tight print bounds for the artwork and variable text, in PDF points from the top-left.
# The body and QR are well within these bounds. Issue Date extends below the artwork.
INK_BOX = (36, 24, PAGE_WIDTH - 36, 586)


class FontError(ValueError):
    pass


class LayoutError(ValueError):
    def __init__(self, field: int, message: str):
        super().__init__(message)
        self.field = field


@dataclass(frozen=True)
class CertificateLayout:
    student: Student
    name_size: float
    body: Paragraph
    qr: list[list[bool]]


def font_paths() -> tuple[Path, Path]:
    configured = os.environ.get("ROBOCAM_FONT_DIR")
    folders = [Path(configured)] if configured else [
        Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "Windows" / "Fonts",
        Path("/usr/share/fonts/truetype/msttcorefonts"),
    ]
    for folder in folders:
        if not folder.is_dir():
            continue
        files = {entry.name.casefold(): entry for entry in folder.iterdir() if entry.is_file()}
        if "times.ttf" in files and "timesbd.ttf" in files:
            return files["times.ttf"], files["timesbd.ttf"]
    raise FontError(
        "Times New Roman Regular and Bold are required. Install both in Windows "
        "or set ROBOCAM_FONT_DIR to the folder containing times.ttf and timesbd.ttf, then try again."
    )


def load_font(path: Path, key: str, *, bold: bool) -> set[int]:
    style = "Bold" if bold else "Regular"
    try:
        with FontFile(path) as font:
            family = font["name"].getBestFamilyName()
            actual_style = font["name"].getBestSubFamilyName()
            if family != "Times New Roman" or actual_style != style:
                raise FontError(f"{path.name} must be Times New Roman {style}.")
            flags = font["OS/2"].fsType
            if flags & (0x0002 | 0x0100 | 0x0200):
                raise FontError(f"{path.name} does not permit the required subset embedding.")
            supported = set(font.getBestCmap())
        pdfmetrics.registerFont(TTFont(key, str(path)))
    except FontError:
        raise
    except Exception as exc:
        raise FontError(f"Could not use Times New Roman {style} from {path.name}.") from exc
    return supported


class CertificateRenderer:
    def __init__(self):
        regular_path, bold_path = font_paths()
        self.regular_chars = load_font(regular_path, REGULAR, bold=False)
        self.bold_chars = load_font(bold_path, BOLD, bold=True)
        self.background = ImageReader(str(ASSETS / "certificate_background.png"))
        self.paragraph_style = ParagraphStyle(
            "Certificate completion",
            fontName=REGULAR,
            fontSize=14,
            leading=BODY_LEADING,
            alignment=TA_JUSTIFY,
            firstLineIndent=36,
            splitLongWords=0,
            textColor=black,
        )

    @staticmethod
    def check_glyphs(value: str, supported: set[int], field: int) -> None:
        missing = sorted({char for char in value if ord(char) not in supported})
        if missing:
            codes = ", ".join(f"U+{ord(char):04X}" for char in missing[:5])
            raise LayoutError(field, f"Times New Roman cannot display these characters ({codes}).")

    def layout(self, student: Student) -> CertificateLayout:
        self.check_glyphs(student.name, self.bold_chars, NAME)
        self.check_glyphs(student.course, self.bold_chars, COURSE)
        self.check_glyphs(student.certificate_id, self.regular_chars, ID)

        natural_width = pdfmetrics.stringWidth(student.name, BOLD, 36)
        name_size = 36 if natural_width <= NAME_WIDTH else 36 * NAME_WIDTH / natural_width
        if name_size < NAME_MIN_SIZE:
            raise LayoutError(NAME, "This name cannot fit at the minimum size of 18 pt.")

        id_width = pdfmetrics.stringWidth(student.certificate_id, REGULAR, 12)
        if id_width > ID_WIDTH:
            raise LayoutError(ID, "This ID is too wide for the 12 pt certificate ID area.")

        course_words = student.course.split()
        if any(pdfmetrics.stringWidth(word, BOLD, 14) > BODY_WIDTH for word in course_words):
            raise LayoutError(COURSE, "A word in this course name is wider than the completion paragraph.")
        start, end = display_date(student.start), display_date(student.end)
        # The template has two spaces after the start date before the justified word “to”.
        body = Paragraph(
            "In recognition of successfully completing the "
            f'<font name="{BOLD}">{escape(student.course)}</font> from '
            f'<font name="{BOLD}">{start} </font> to <font name="{BOLD}">{end}</font> '
            f"{BODY_SUFFIX}",
            self.paragraph_style,
        )
        _, height = body.wrap(BODY_WIDTH, BODY_LEADING * BODY_MAX_LINES)
        if height > BODY_LEADING * BODY_MAX_LINES:
            raise LayoutError(COURSE, "The completion paragraph does not fit the original three-line area.")

        code = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, border=4)
        code.add_data(student.certificate_id)
        try:
            code.make(fit=True)
        except qrcode.exceptions.DataOverflowError as exc:
            raise LayoutError(ID, "This certificate ID is too long to encode as a QR code.") from exc
        return CertificateLayout(student, name_size, body, code.get_matrix())

    def render(self, layouts: list[CertificateLayout]) -> bytes:
        if not layouts:
            raise ValueError("Select at least one certificate.")
        output = BytesIO()
        page = Canvas(
            output, pagesize=(PAGE_WIDTH, PAGE_HEIGHT), pageCompression=1, initialFontName=REGULAR,
        )
        page.setTitle("RoboCAM Certificates of Completion")
        page.setAuthor("RoboCAM Tech Solutions")
        for layout in layouts:
            self.draw_certificate(page, layout)
            page.showPage()
        page.save()
        return output.getvalue()

    def draw_certificate(self, page: Canvas, layout: CertificateLayout) -> None:
        student = layout.student
        image_width, image_height = self.background.getSize()
        scale = min(PAGE_WIDTH / image_width, PAGE_HEIGHT / image_height)
        width, height = image_width * scale, image_height * scale
        page.drawImage(
            self.background, (PAGE_WIDTH - width) / 2, (PAGE_HEIGHT - height) / 2,
            width, height, mask="auto",
        )

        page.setFillColor(black)
        page.setFont(BOLD, 20)
        page.drawCentredString(CENTER, PAGE_HEIGHT - 199.1, ISSUER)
        page.setFont(BOLD, layout.name_size)
        page.setFillColor(GOLD)
        page.drawCentredString(CENTER, PAGE_HEIGHT - 297.2, student.name)
        page.setFillColor(black)
        page.setFont(REGULAR, 12)
        if student.dob:
            page.drawCentredString(
                CENTER, PAGE_HEIGHT - 314.5, f"Date of Birth: {display_date(student.dob, full_year=True)}",
            )

        # Paragraph.draw starts its first baseline one font size below its top.
        paragraph_bottom = PAGE_HEIGHT - BODY_FIRST_BASELINE + 14 - layout.body.height
        layout.body.drawOn(page, BODY_X, paragraph_bottom)

        page.setFillColor(GOLD)
        page.setFont(BOLD, 20)
        page.drawCentredString(CENTER, PAGE_HEIGHT - 476.0, INSTRUCTOR)
        page.setFillColor(black)
        page.setFont(REGULAR, 12)
        page.drawCentredString(CENTER, PAGE_HEIGHT - 502.25, LEAD_INSTRUCTOR)
        page.drawCentredString(CENTER, PAGE_HEIGHT - 518.25, SENIOR_INSTRUCTOR)
        self.draw_qr(page, layout.qr)
        page.drawCentredString(QR_CENTER, PAGE_HEIGHT - 509.8, "Certificate ID:")
        page.drawCentredString(QR_CENTER, PAGE_HEIGHT - 523.6, student.certificate_id)
        page.drawRightString(ISSUE_RIGHT, PAGE_HEIGHT - 582.55, f"Issue Date: {display_date(student.issue)}")

    @staticmethod
    def draw_qr(page: Canvas, matrix: list[list[bool]]) -> None:
        step = QR_SIZE / len(matrix)
        x = QR_CENTER - QR_SIZE / 2
        y = PAGE_HEIGHT - QR_TOP
        page.setFillColor(white)
        page.rect(x, y - QR_SIZE, QR_SIZE, QR_SIZE, fill=1, stroke=0)
        page.setFillColor(black)
        path = page.beginPath()
        for row, cells in enumerate(matrix):
            for column, dark in enumerate(cells):
                if dark:
                    path.rect(x + column * step, y - (row + 1) * step, step, step)
        page.drawPath(path, fill=1, stroke=0)
