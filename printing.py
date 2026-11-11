"""Render the generated PDF for the preview and native printer dialog."""

from io import BytesIO
from itertools import chain, repeat

from pypdfium2 import PdfDocument
from PySide6.QtCore import QRectF
from PySide6.QtGui import QImage, QPageLayout, QPageSize, QPainter
from PySide6.QtPrintSupport import QAbstractPrintDialog, QPrintDialog, QPrinter
from PySide6.QtWidgets import QDialog, QMessageBox

from certificate import INK_BOX, PAGE_HEIGHT, PAGE_WIDTH


def page_image(document: PdfDocument, index: int, scale: float) -> QImage:
    page = document[index]
    try:
        bitmap = page.render(scale=scale, rev_byteorder=True, prefer_bgrx=True)
        try:
            pixels = bytes(bitmap.buffer)
            return QImage(
                pixels, bitmap.width, bitmap.height, bitmap.stride, QImage.Format.Format_RGBX8888
            ).copy()
        finally:
            bitmap.close()
    finally:
        page.close()


def split_pdf(pdf: bytes):
    """Yield single-page PDFs copied from the already generated batch."""
    with PdfDocument(pdf) as batch:
        for index in range(len(batch)):
            buffer = BytesIO()
            with PdfDocument.new() as page:
                page.import_pages(batch, [index])
                page.save(buffer)
            yield buffer.getvalue()


def configure_printer() -> QPrinter:
    printer = QPrinter(QPrinter.PrinterMode.HighResolution)
    printer.setPageSize(QPageSize(QPageSize.PageSizeId.A4))
    printer.setPageOrientation(QPageLayout.Orientation.Landscape)
    printer.setDuplex(QPrinter.DuplexMode.DuplexNone)
    printer.setDocName("RoboCAM Certificates of Completion")
    return printer


def print_rectangles(printer: QPrinter) -> tuple[QRectF, QRectF, bool]:
    layout = printer.pageLayout()
    layout.setMode(QPageLayout.Mode.StandardMode)
    layout.setMargins(layout.minimumMargins())
    dpi = printer.resolution()
    paper = QRectF(layout.fullRectPixels(dpi))
    printable = QRectF(layout.paintRectPixels(dpi))
    points_to_pixels = dpi / 72
    width = PAGE_WIDTH * points_to_pixels
    height = PAGE_HEIGHT * points_to_pixels
    actual = QRectF(
        paper.center().x() - width / 2, paper.center().y() - height / 2, width, height,
    )
    left, top, right, bottom = INK_BOX
    ink = QRectF(
        actual.left() + left * points_to_pixels,
        actual.top() + top * points_to_pixels,
        (right - left) * points_to_pixels,
        (bottom - top) * points_to_pixels,
    )
    scale = min(1, printable.width() / width, printable.height() / height)
    fit = QRectF(
        printable.center().x() - width * scale / 2,
        printable.center().y() - height * scale / 2,
        width * scale, height * scale,
    )
    return actual, fit, not printable.contains(ink)


def selected_pages(printer: QPrinter, page_count: int) -> list[int]:
    if printer.printRange() == QPrinter.PrintRange.PageRange:
        ranges = printer.pageRanges()
        if not ranges.isEmpty():
            pages = [index for index in range(page_count) if ranges.contains(index + 1)]
        else:
            start = max(1, printer.fromPage())
            end = min(page_count, printer.toPage() or page_count)
            pages = list(range(start - 1, end))
    else:
        pages = list(range(page_count))
    if printer.pageOrder() == QPrinter.PageOrder.LastPageFirst:
        pages.reverse()
    return pages


def send_pdf(printer: QPrinter, document: PdfDocument, target: QRectF) -> None:
    pages = selected_pages(printer, len(document))
    if not pages:
        raise ValueError("The selected print range contains no certificate pages.")
    copies = printer.copyCount()
    if printer.supportsMultipleCopies():
        job = iter(pages)
    elif printer.collateCopies():
        job = chain.from_iterable(repeat(pages, copies))
        printer.setCopyCount(1)
    else:
        job = chain.from_iterable(repeat(page, copies) for page in pages)
        printer.setCopyCount(1)

    printer.setFullPage(True)
    painter = QPainter()
    if not painter.begin(printer):
        raise OSError("The printer could not start the job. Check the printer and try again.")
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    scale = min(600, max(300, printer.resolution())) / 72
    try:
        for position, index in enumerate(job):
            if position and not printer.newPage():
                raise OSError("The printer could not start the next page.")
            painter.drawImage(target, page_image(document, index, scale))
    except Exception:
        printer.abort()
        painter.end()
        raise
    if not painter.end() or printer.printerState() in {
        QPrinter.PrinterState.Error, QPrinter.PrinterState.Aborted,
    }:
        raise OSError("The print job did not complete. Check the printer queue.")


def print_pdf(parent, pdf: bytes) -> bool:
    with PdfDocument(pdf) as document:
        printer = configure_printer()
        dialog = QPrintDialog(printer, parent)
        dialog.setWindowTitle("Print certificates")
        dialog.setMinMax(1, len(document))
        dialog.setOption(QAbstractPrintDialog.PrintDialogOption.PrintPageRange, len(document) > 1)
        dialog.setOption(QAbstractPrintDialog.PrintDialogOption.PrintCollateCopies, True)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return False

        actual, fit, would_clip = print_rectangles(printer)
        target = actual
        if would_clip:
            question = QMessageBox(parent)
            question.setWindowTitle("Certificate print scaling")
            question.setText("This printer may crop part of the certificate at 100%.")
            actual_button = question.addButton("Actual Size (100%)", QMessageBox.ButtonRole.AcceptRole)
            fit_button = question.addButton("Fit to printable area", QMessageBox.ButtonRole.ActionRole)
            question.addButton(QMessageBox.StandardButton.Cancel)
            question.setDefaultButton(actual_button)
            question.exec()
            if question.clickedButton() == fit_button:
                target = fit
            elif question.clickedButton() != actual_button:
                return False
        send_pdf(printer, document, target)
        return True
