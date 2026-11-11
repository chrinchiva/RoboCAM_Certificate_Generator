# RoboCAM Certificate Generator

A Windows desktop app for importing student records, previewing the original RoboCAM certificate layout, exporting A4 landscape PDFs, and printing to a selected printer.

## Start on Windows

1. Install [Python 3.12 for Windows](https://www.python.org/downloads/windows/) and enable the Python launcher during setup.
2. Extract the ZIP to a writable folder such as `Documents\RoboCAM`.
3. Double-click **Start_RoboCAM.cmd**. The first run creates a local virtual environment and downloads the dependencies.
4. Use **Import Students** and open the sample Excel or CSV from the `samples` folder. Choose **Preview**.

To install and start manually from PowerShell in the extracted folder:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe app.py
```

The app uses Times New Roman Regular and Bold from Windows. If the fonts are stored elsewhere, set `ROBOCAM_FONT_DIR` to the directory containing `times.ttf` and `timesbd.ttf` before starting the app. The PDF generator checks the font names and embedding permissions. The source package uses fonts installed on the computer.

## Prepare student records

Use **Excel Template** to save a blank workbook, or open the template in `samples`. Place the headers in the first row. For Excel imports, the app uses the sheet named **Students** when available and the active sheet otherwise. CSV imports use comma-separated UTF-8.

| Column | Entry |
|---|---|
| `Student_Name` | Required. Enter the name exactly as it should appear. |
| `Date_of_Birth` | Optional. Use an Excel date, `YYYY-MM-DD`, or `DD-MMM-YYYY`. Blank hides the DOB line. |
| `Course_Name` | Required. Use the course title as it should appear in the completion paragraph. |
| `Start_Date` | Required. Enter the first day of training. |
| `End_Date` | Required. Enter the last day of training. |
| `Issue_Date` | Optional. When blank, the app uses the end date. |
| `Certificate_ID` | Required. Use a unique ID. Format Excel IDs as text to preserve leading zeros. The QR code contains this ID. |

For training and issue dates, the app accepts Excel dates, `YYYY-MM-DD`, `DD-MMM-YYYY`, and `DD-MMM-YY`. Two-digit years are read as 2000–2099. The PDF uses English month abbreviations. Use characters available in Times New Roman.

Edit highlighted fields in the table to fix errors. The row number beside each record matches its original spreadsheet row. Edits apply to the current session and its PDF output.

## Preview, export and print

1. Under **Include**, choose **All students** or **Selected rows**. For one student, select one row. Hold **Ctrl** to select several rows.
2. Choose **Preview** and use **Previous** and **Next** to inspect the certificates.
3. Choose **Export PDF**. **Combined PDF** saves all included certificates in one file. **Individual PDFs** saves one file per ID in the selected folder. An ID used as a filename must be valid on Windows.
4. Choose **Print** to open the system printer dialog. Select the printer, page range and copies. A4 landscape is preselected. The app prints at actual size by default. If the reported printer margins could clip the certificate, choose **Fit to printable area** to scale it down.
5. If print placement is important, use the original **Em Keo** sample record with DOB blank for the first page on your printer.

The preview, combined export and print workflow use the same PDF pages. The exported PDF retains selectable Times New Roman text and a vector QR code. Printer output is rendered from those pages at up to 600 dpi, according to printer resolution.

## Verification

The package includes the blank Excel import template, example Excel and CSV records, and a printable PDF for the original Em Keo record. Its layout and line breaks were compared with a render of the Word template. The sample batch exercises a DOB with a separate issue date and a long name that reduces in size.

The GUI and print renderer were exercised in a Linux test environment using Qt's virtual PDF printer. The Windows printer dialog and physical printer output require verification on the target computer.
