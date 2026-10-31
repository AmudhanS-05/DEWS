"""Writes tests/india/fixtures/indiawris_sample.xlsx: a HAND-MADE file in the assumed India-WRIS export layout
(metadata block, header row, one row per observation). Fictitious station and values; not real data.
    python -m tests.india.make_indiawris_fixture
"""
from datetime import date, datetime

from openpyxl import Workbook

OUT = "tests/india/fixtures/indiawris_sample.xlsx"


def main():
    wb = Workbook()
    ws = wb.active
    ws.title = "Water Level"
    for row in [["River Water Level Data"], [], ["Station Code :", "FIXTURE01"], ["Station Name :", "Fixture Ghat"],
                ["River :", "Test River"], ["Basin :", "Test Basin"], ["State :", "Test State"],
                ["Agency :", "CWC"], ["Unit :", "m"], [],
                ["S.No.", "Data Time", "Water Level (m)"]]:
        ws.append(row)
    vals = [101.20, 101.25, "-", 101.40, 102.10, 103.85, 104.60, 103.90, 102.70, 101.95]
    for k, v in enumerate(vals):
        ws.append([k + 1, date(2019, 8, 1 + k), v])        # daily values, date only (Excel stores midnight)
    ws.append([11, date(2019, 8, 10), 101.95])             # duplicate date -> dropped
    ws.append([12, "11-08-2019 14:30", 101.80])            # text with time, IST
    ws.append([13, datetime(2019, 8, 12, 6, 15), 101.70])  # Excel datetime with time, IST
    ws.append([14, None, None])
    wb.save(OUT)


if __name__ == "__main__":
    main()
