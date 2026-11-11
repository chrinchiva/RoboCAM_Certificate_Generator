"""RoboCAM Certificate Generator desktop application."""

import os
import sys
from pathlib import Path
from shutil import copyfile
from tempfile import NamedTemporaryFile, TemporaryDirectory

from pypdfium2 import PdfDocument
from PySide6.QtCore import QRect, QSignalBlocker, Qt
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import (
    QApplication, QComboBox, QFileDialog, QFrame, QHBoxLayout, QHeaderView,
    QInputDialog, QLabel, QListWidget, QListWidgetItem, QMainWindow, QMessageBox,
    QPushButton, QSplitter, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from certificate import CertificateRenderer, FontError, LayoutError
from printing import page_image, print_pdf, split_pdf
from records import HEADERS, ID, certificate_filename, import_records, validate_rows

PROJECT = Path(__file__).resolve().parent
IMPORT_TEMPLATE = PROJECT / "samples" / "RoboCAM_Student_Import_Template.xlsx"
COLUMNS = ("Student name", "Date of birth", "Course name", "Start date", "End date", "Issue date", "Certificate ID")
EXPORT_MODES = ("Combined PDF", "Individual PDFs")

STYLE = """
QMainWindow, QWidget#workspace { background: #F4F6F8; color: #1D2839; }
QLabel { color: #243247; }
QLabel#title { font-size: 25px; font-weight: 700; }
QLabel#panelTitle { font-size: 15px; font-weight: 700; }
QLabel#hint { color: #4F6076; }
QPushButton, QComboBox {
    color: #233248; background: white; border: 1px solid #BAC5D1;
    border-radius: 5px; padding: 7px 11px;
}
QPushButton:hover, QComboBox:hover { border-color: #5F748A; background: #EEF3F8; }
QPushButton:focus, QComboBox:focus { border: 2px solid #8F630C; padding: 6px 10px; }
QPushButton#primary { background: #263A56; color: white; border-color: #263A56; }
QPushButton#primary:hover { background: #334D71; }
QPushButton:disabled { background: #E8EDF2; color: #52647A; border-color: #D3DCE6; }
QPushButton#primary:disabled { background: #E8EDF2; color: #52647A; border-color: #D3DCE6; }
QTableWidget, QListWidget {
    background: white; color: #223047; border: 1px solid #C8D2DC;
    gridline-color: #E4EAF0; alternate-background-color: #F7F9FB;
    selection-background-color: #DDE9FA; selection-color: #172338;
}
QHeaderView::section {
    color: #273750; background: #E9EFF5; border: none;
    border-right: 1px solid #D7E0E9; padding: 7px 5px; font-weight: 600;
}
QLineEdit { color: #172338; background: white; selection-background-color: #263A56; }
QListWidget#errors { background: #FFF8F7; color: #882A25; }
QFrame#previewFrame { background: #E6ECF2; border: 1px solid #CDD7E2; border-radius: 5px; }
QStatusBar { background: #EEF2F7; color: #364B63; }
"""


def button(text: str, slot, *, primary: bool = False) -> QPushButton:
    widget = QPushButton(text)
    if primary:
        widget.setObjectName("primary")
    widget.clicked.connect(slot)
    return widget


def atomic_write(path: Path, data: bytes) -> None:
    with NamedTemporaryFile(dir=path.parent, delete=False) as staging:
        temp_path = Path(staging.name)
        try:
            staging.write(data)
            staging.close()
            os.replace(temp_path, path)
        finally:
            temp_path.unlink(missing_ok=True)


class PagePreview(QWidget):
    def __init__(self):
        super().__init__()
        self.image = QImage()
        self.message = "Import students to preview a certificate."
        self.setMinimumSize(260, 220)

    def clear(self, message: str) -> None:
        self.image = QImage()
        self.message = message
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        frame = self.rect().adjusted(15, 15, -15, -15)
        if self.image.isNull():
            painter.setPen(QColor("#4F6076"))
            painter.drawText(frame, Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, self.message)
            return
        size = self.image.size()
        size.scale(frame.size(), Qt.AspectRatioMode.KeepAspectRatio)
        target = QRect(frame)
        target.setSize(size)
        target.moveCenter(frame.center())
        painter.fillRect(target.translated(0, 3), QColor("#C8D3DF"))
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.drawImage(target, self.image)


class CertificateWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("RoboCAM Certificate Generator")
        self.resize(1460, 810)
        self.setMinimumSize(970, 610)
        self.source_rows: list[int] = []
        self.students = {}
        self.errors = {}
        self.renderer = None
        self.pdf: bytes | None = None
        self.pdf_rows: list[int] = []
        self.page_index = 0

        workspace = QWidget()
        workspace.setObjectName("workspace")
        content = QVBoxLayout(workspace)
        content.setContentsMargins(18, 16, 18, 14)
        content.setSpacing(12)
        self.setCentralWidget(workspace)

        actions = QHBoxLayout()
        title = QLabel("RoboCAM Certificate Generator")
        title.setObjectName("title")
        actions.addWidget(title)
        actions.addStretch()
        actions.addWidget(button("Excel Template", self.save_template))
        actions.addWidget(button("Import Students", self.load_file, primary=True))
        content.addLayout(actions)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(9)
        content.addWidget(splitter, 1)

        records_panel = QWidget()
        records_layout = QVBoxLayout(records_panel)
        records_layout.setContentsMargins(0, 0, 0, 0)
        students_title = QLabel("Students")
        students_title.setObjectName("panelTitle")
        records_layout.addWidget(students_title)
        self.source_label = QLabel("No student file imported")
        self.source_label.setObjectName("hint")
        self.source_label.setTextFormat(Qt.TextFormat.PlainText)
        records_layout.addWidget(self.source_label)

        self.table = QTableWidget(0, len(HEADERS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)
        self.table.setAlternatingRowColors(True)
        self.table.setMinimumWidth(490)
        self.table.verticalHeader().setDefaultSectionSize(30)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setStretchLastSection(True)
        for index, width in enumerate((127, 91, 147, 87, 87, 87, 145)):
            self.table.setColumnWidth(index, width)
        records_layout.addWidget(self.table, 1)
        self.error_list = QListWidget()
        self.error_list.setObjectName("errors")
        self.error_list.setMaximumHeight(108)
        self.error_list.itemClicked.connect(self.focus_error)
        self.error_list.hide()
        records_layout.addWidget(self.error_list)
        splitter.addWidget(records_panel)

        preview_panel = QWidget()
        preview_layout = QVBoxLayout(preview_panel)
        preview_layout.setContentsMargins(0, 0, 0, 0)
        preview_title = QLabel("Certificate preview")
        preview_title.setObjectName("panelTitle")
        preview_layout.addWidget(preview_title)
        preview_frame = QFrame()
        preview_frame.setObjectName("previewFrame")
        frame_layout = QVBoxLayout(preview_frame)
        frame_layout.setContentsMargins(0, 0, 0, 0)
        self.preview = PagePreview()
        frame_layout.addWidget(self.preview)
        preview_layout.addWidget(preview_frame, 1)

        navigation = QHBoxLayout()
        self.previous = button("Previous", lambda: self.change_page(-1))
        self.next = button("Next", lambda: self.change_page(1))
        self.page_label = QLabel()
        self.page_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        navigation.addWidget(self.previous)
        navigation.addWidget(self.page_label, 1)
        navigation.addWidget(self.next)
        preview_layout.addLayout(navigation)
        splitter.addWidget(preview_panel)
        splitter.setSizes([810, 610])

        output_actions = QHBoxLayout()
        self.scope = QComboBox()
        self.scope.addItems(["All students", "Selected rows"])
        self.scope.setAccessibleName("Certificates to include")
        self.scope.currentIndexChanged.connect(self.clear_preview)
        output_actions.addWidget(QLabel("Include"))
        output_actions.addWidget(self.scope)
        output_actions.addStretch()
        self.preview_button = button("Preview", self.make_preview)
        self.export_button = button("Export PDF", self.export_pdf, primary=True)
        self.print_button = button("Print", self.print_certificates)
        for widget in (self.preview_button, self.export_button, self.print_button):
            output_actions.addWidget(widget)
        content.addLayout(output_actions)
        self.table.itemChanged.connect(self.records_edited)
        self.table.itemSelectionChanged.connect(self.selection_changed)
        self.update_actions()

    def load_file(self) -> None:
        filename, _ = QFileDialog.getOpenFileName(
            self, "Import student data", "", "Student files (*.xlsx *.csv)"
        )
        if not filename:
            return
        path = Path(filename)
        try:
            imported = import_records(path)
            if not imported:
                raise ValueError("The file contains no student records.")
        except Exception as exc:
            QMessageBox.warning(self, "Import failed", str(exc))
            return

        with QSignalBlocker(self.table):
            self.table.clearContents()
            self.table.setRowCount(len(imported))
            self.source_rows = [source_row for source_row, _ in imported]
            for row_index, (_, values) in enumerate(imported):
                self.table.setVerticalHeaderItem(row_index, QTableWidgetItem(str(self.source_rows[row_index])))
                for column, value in enumerate(values):
                    self.table.setItem(row_index, column, QTableWidgetItem(value))
            self.table.selectRow(0)
        self.source_label.setText(f"{path.name}  ·  {len(imported)} student records")
        self.records_edited()

    def save_template(self) -> None:
        filename, _ = QFileDialog.getSaveFileName(
            self, "Save Excel import template", IMPORT_TEMPLATE.name, "Excel files (*.xlsx)"
        )
        if filename:
            try:
                copyfile(IMPORT_TEMPLATE, filename)
                self.statusBar().showMessage("Excel template saved.", 5000)
            except OSError as exc:
                QMessageBox.warning(self, "Could not save Excel template", str(exc))

    def records_edited(self) -> None:
        rows = [
            tuple(self.table.item(row, column).text() for column in range(len(HEADERS)))
            for row in range(self.table.rowCount())
        ]
        self.students, self.errors = validate_rows(rows)
        self.show_errors(self.errors)
        self.clear_preview()

    def show_errors(self, errors: dict[tuple[int, int], str]) -> None:
        self.error_list.clear()
        with QSignalBlocker(self.table):
            for row in range(self.table.rowCount()):
                for column in range(len(HEADERS)):
                    item = self.table.item(row, column)
                    error = errors.get((row, column), "")
                    item.setData(Qt.ItemDataRole.BackgroundRole, QColor("#FCE5E2") if error else None)
                    item.setToolTip(error or item.text())
        for (row, column), message in sorted(errors.items()):
            item = QListWidgetItem(f"Row {self.source_rows[row]} · {HEADERS[column]} · {message}")
            item.setData(Qt.ItemDataRole.UserRole, (row, column))
            self.error_list.addItem(item)
        self.error_list.setVisible(bool(errors))

    def focus_error(self, item: QListWidgetItem) -> None:
        row, column = item.data(Qt.ItemDataRole.UserRole)
        cell = self.table.item(row, column)
        self.table.setCurrentItem(cell)
        self.table.scrollToItem(cell)
        self.table.setFocus()

    def selection_changed(self) -> None:
        if self.scope.currentIndex() == 1:
            self.clear_preview()

    def rows_to_render(self) -> list[int]:
        if self.scope.currentIndex() == 1:
            return sorted(index.row() for index in self.table.selectionModel().selectedRows())
        return list(range(self.table.rowCount()))

    def clear_preview(self, *_args) -> None:
        self.pdf = None
        self.pdf_rows = []
        self.page_index = 0
        message = "Choose Preview to check the certificate." if self.source_rows else "Import students to preview a certificate."
        self.preview.clear(message)
        self.update_actions()

    def update_actions(self) -> None:
        has_rows = bool(self.rows_to_render())
        for widget in (self.preview_button, self.export_button, self.print_button):
            widget.setEnabled(has_rows)
        count = len(self.pdf_rows)
        self.previous.setEnabled(self.page_index > 0)
        self.next.setEnabled(self.page_index + 1 < count)
        self.page_label.setText(f"Page {self.page_index + 1} of {count}" if count else "—")

    def prepare_pdf(self) -> bool:
        if self.pdf is not None:
            return True
        rows = self.rows_to_render()
        if not rows:
            return False
        if any(row not in self.students for row in rows):
            self.show_errors(self.errors)
            self.statusBar().showMessage("Correct the highlighted cells before generating certificates.", 8000)
            return False
        try:
            if self.renderer is None:
                self.renderer = CertificateRenderer()
            layouts = []
            errors = dict(self.errors)
            for row in rows:
                try:
                    layouts.append(self.renderer.layout(self.students[row]))
                except LayoutError as exc:
                    errors[row, exc.field] = str(exc)
            self.show_errors(errors)
            if len(layouts) != len(rows):
                self.statusBar().showMessage("Correct the highlighted fields before generating certificates.", 8000)
                return False
            self.pdf = self.renderer.render(layouts)
            self.pdf_rows = rows
            self.page_index = 0
            self.show_page()
            return True
        except FontError as exc:
            QMessageBox.warning(self, "Times New Roman required", str(exc))
        except Exception as exc:
            self.clear_preview()
            QMessageBox.warning(self, "Certificate generation failed", str(exc))
        return False

    def show_page(self) -> None:
        with PdfDocument(self.pdf) as document:
            self.preview.image = page_image(document, self.page_index, 2)
        self.preview.update()
        self.update_actions()

    def change_page(self, offset: int) -> None:
        self.page_index += offset
        self.show_page()

    def make_preview(self) -> None:
        if self.prepare_pdf():
            self.statusBar().showMessage(f"{len(self.pdf_rows)} certificate pages ready.", 5000)

    def export_pdf(self) -> None:
        if not self.prepare_pdf():
            return
        mode, accepted = QInputDialog.getItem(
            self, "Export certificates", "PDF output", EXPORT_MODES, 0, False,
        )
        if not accepted:
            return
        try:
            if mode == EXPORT_MODES[0]:
                filename, _ = QFileDialog.getSaveFileName(
                    self, "Save certificate PDF", "RoboCAM_Certificates.pdf", "PDF files (*.pdf)"
                )
                if not filename:
                    return
                atomic_write(Path(filename), self.pdf)
                self.statusBar().showMessage(f"Saved {len(self.pdf_rows)} certificate pages.", 5000)
            else:
                self.export_individual()
        except Exception as exc:
            QMessageBox.warning(self, "PDF export failed", str(exc))

    def export_individual(self) -> None:
        filenames = []
        for row in self.pdf_rows:
            try:
                filenames.append(certificate_filename(self.students[row].certificate_id))
            except ValueError as exc:
                self.show_errors({**self.errors, (row, ID): str(exc)})
                self.statusBar().showMessage("Correct the certificate ID or choose Combined PDF.", 8000)
                return
        folder = QFileDialog.getExistingDirectory(self, "Save individual certificates")
        if not folder:
            return
        destination = Path(folder)
        existing = [name for name in filenames if (destination / name).exists()]
        if existing and QMessageBox.question(
            self, "Replace existing PDFs",
            f"{len(existing)} matching files already exist. Replace them?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        ) != QMessageBox.StandardButton.Yes:
            return

        with TemporaryDirectory(dir=destination, prefix=".robocam-") as staging:
            # Generate every file before replacing anything in the destination.
            for name, pdf in zip(filenames, split_pdf(self.pdf), strict=True):
                (Path(staging) / name).write_bytes(pdf)
            for saved, name in enumerate(filenames):
                try:
                    os.replace(Path(staging) / name, destination / name)
                except OSError as exc:
                    raise OSError(f"Saved {saved} of {len(filenames)} PDFs. Could not save {name}.\n{exc}") from exc
        self.statusBar().showMessage(f"Saved {len(filenames)} individual certificate PDFs.", 5000)

    def print_certificates(self) -> None:
        if self.prepare_pdf():
            try:
                if print_pdf(self, self.pdf):
                    self.statusBar().showMessage("Print job sent to the selected printer.", 5000)
            except Exception as exc:
                QMessageBox.warning(self, "Printing failed", str(exc))


def main() -> int:
    application = QApplication(sys.argv)
    application.setApplicationName("RoboCAM Certificate Generator")
    application.setStyle("Fusion")
    application.setStyleSheet(STYLE)
    window = CertificateWindow()
    window.show()
    return application.exec()


if __name__ == "__main__":
    raise SystemExit(main())
