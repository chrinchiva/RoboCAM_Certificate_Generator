"""Import and validate student records independently of the GUI."""

import csv
import re
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from openpyxl import load_workbook

HEADERS = (
    "Student_Name", "Date_of_Birth", "Course_Name", "Start_Date",
    "End_Date", "Issue_Date", "Certificate_ID",
)
NAME, DOB, COURSE, START, END, ISSUE, ID = range(len(HEADERS))
REQUIRED = (NAME, COURSE, START, END, ID)
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
MONTH_NUMBER = {month.lower(): index for index, month in enumerate(MONTHS, 1)}
DATE_TEXT = re.compile(r"(\d{1,2})-([a-z]{3})-(\d{4}|\d{2})", re.I | re.ASCII)


@dataclass(frozen=True)
class Student:
    name: str
    dob: date | None
    course: str
    start: date
    end: date
    issue: date
    certificate_id: str


def display_date(value: date, full_year: bool = False) -> str:
    year = f"{value.year:04d}" if full_year else f"{value.year % 100:02d}"
    return f"{value.day:02d}-{MONTHS[value.month - 1]}-{year}"


def parse_date(value: str, full_year: bool = False) -> date:
    if re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
        return date.fromisoformat(value)
    match = DATE_TEXT.fullmatch(value)
    if match:
        day, month_text, year_text = match.groups()
        month = MONTH_NUMBER.get(month_text.lower())
        if month and (len(year_text) == 4 or not full_year):
            year = int(year_text)
            if len(year_text) == 2:
                year += 2000
            return date(year, month, int(day))
    raise ValueError("Invalid date")


def cell_text(value) -> str:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return "" if value is None else str(value).strip()


def read_rows(rows) -> list[tuple[int, tuple[str, ...]]]:
    iterator = iter(rows)
    header = next(iterator, None)
    if header is None:
        raise ValueError("The file has no header row.")
    normalized = [cell_text(value).casefold() for value in header]
    columns = []
    for index, label in enumerate(HEADERS):
        key = label.casefold()
        if normalized.count(key) > 1:
            raise ValueError(f"Duplicate column header {label}.")
        if index in REQUIRED and key not in normalized:
            raise ValueError(f"Missing required column {label}.")
        columns.append(normalized.index(key) if key in normalized else None)

    records = []
    for source_row, values in enumerate(iterator, 2):
        row = tuple(
            cell_text(values[col]) if col is not None and col < len(values) else ""
            for col in columns
        )
        if any(row):
            records.append((source_row, row))
    return records


def import_records(path: Path) -> list[tuple[int, tuple[str, ...]]]:
    suffix = path.suffix.lower()
    if suffix == ".csv":
        with path.open(encoding="utf-8-sig", newline="") as file:
            return read_rows(csv.reader(file, strict=True))
    if suffix == ".xlsx":
        workbook = load_workbook(path, read_only=True, data_only=True)
        try:
            sheet = workbook["Students"] if "Students" in workbook.sheetnames else workbook.active
            return read_rows(sheet.iter_rows(values_only=True))
        finally:
            workbook.close()
    raise ValueError("Choose an Excel (.xlsx) or UTF-8 CSV (.csv) file.")


def validate_rows(rows: list[tuple[str, ...]]) -> tuple[dict[int, Student], dict[tuple[int, int], str]]:
    students = {}
    errors = {}
    ids = Counter(row[ID].strip().casefold() for row in rows if row[ID].strip())
    for index, raw in enumerate(rows):
        row = tuple(value.strip() for value in raw)
        issues = {}
        for column in REQUIRED:
            if not row[column]:
                issues[column] = "Required."
        for column in (NAME, COURSE, ID):
            if any(ord(char) < 32 or ord(char) == 127 for char in row[column]):
                issues[column] = "Enter this value on a single line."
        if row[ID] and ids[row[ID].casefold()] > 1:
            issues[ID] = "Duplicate certificate ID."

        dates = {}
        for column in (DOB, START, END, ISSUE):
            dates[column] = None
            if row[column]:
                try:
                    dates[column] = parse_date(row[column], full_year=(column == DOB))
                except ValueError:
                    forms = "YYYY-MM-DD or DD-MMM-YYYY"
                    if column != DOB:
                        forms += " (DD-MMM-YY also accepted)"
                    issues[column] = f"Enter a valid date as {forms}."

        dob, start, end, issue = (dates[column] for column in (DOB, START, END, ISSUE))
        if start and end and end < start:
            issues[END] = "End date must be on or after the start date."
        if start and dob and dob > start:
            issues[DOB] = "Date of birth must be on or before the start date."
        if not issues:
            students[index] = Student(row[NAME], dob, row[COURSE], start, end, issue or end, row[ID])
        errors.update({(index, column): error for column, error in issues.items()})
    return students, errors


def certificate_filename(certificate_id: str) -> str:
    stem = certificate_id.split(".", 1)[0].rstrip().upper()
    reserved = {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"}
    reserved.update(f"{prefix}{number}" for prefix in ("COM", "LPT") for number in "123456789¹²³")
    if re.search(r'[<>:"/\\|?*\x00-\x1f]', certificate_id) or certificate_id.endswith(".") or stem in reserved:
        raise ValueError("This certificate ID cannot be used as a Windows filename.")
    return f"{certificate_id}.pdf"
