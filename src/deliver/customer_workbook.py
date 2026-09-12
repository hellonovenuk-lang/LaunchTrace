"""The customer-facing weekly Excel workbook.

Three tabs, written for three different readers:

* **WEEKLY BRIEF** — a director's 60 seconds. Branding, the week's counts, one
  observation about where the opportunity sits, and the handful of companies
  that deserve attention, each on a single card.
* **OPPORTUNITIES** — the working sheet. One company per row in a real Excel
  table with filters and frozen identity columns, plus three empty columns the
  customer's own team owns.
* **HOW TO USE** — what a new sales hire needs before touching the list.

Presentation only. Everything rendered here is read from a
:class:`~src.deliver.customer_narrative.CustomerReport`, which is itself built
from stored opportunities; nothing in this module scores, qualifies or filters
anything. Internal mechanics — fit scores, confidence figures, taxonomy keys,
local file paths — deliberately do not reach the file.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from openpyxl import Workbook
from openpyxl.cell.rich_text import CellRichText, TextBlock
from openpyxl.cell.text import InlineFont
from openpyxl.formatting.rule import FormulaRule
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation
from openpyxl.worksheet.table import Table, TableStyleInfo
from openpyxl.worksheet.worksheet import Worksheet

from src.deliver.customer_narrative import CustomerReport, CustomerRow
from src.settings import load_config

BRIEF_SHEET = "WEEKLY BRIEF"
OPPORTUNITIES_SHEET = "OPPORTUNITIES"
HOW_TO_SHEET = "HOW TO USE"

# The brief is laid out on a fixed eight-column grid so cards, tiles and
# headings share the same left and right edges.
_BRIEF_COLUMNS = "BCDEFGHI"
_BRIEF_WIDTH = 16.0
_GUTTER = 2.0

# Roughly how many characters of body text fit in one column-width unit, and
# how many points one wrapped line needs. Used only to give merged and wrapped
# cells a sensible height, because Excel will not auto-fit either. Measured
# against Calibri at 10–11pt; a little slack is built into the padding so a
# slightly wider glyph run does not clip.
_CHARS_PER_WIDTH_UNIT = 1.26
_POINTS_PER_LINE = 1.28
_LINE_PADDING = 4.0


@dataclass(frozen=True)
class Palette:
    ink: str
    muted: str
    faint: str
    line: str
    page: str
    card: str
    accent: str
    accent_dark: str
    accent_tint: str
    top_fill: str
    top_text: str
    strong_fill: str
    strong_text: str
    relevant_text: str
    font: str

    @classmethod
    def from_config(cls, brand: dict) -> Palette:
        return cls(**{f: brand[f] for f in cls.__dataclass_fields__})


def _thin(colour: str) -> Side:
    return Side(style="thin", color=colour)


def _fill(colour: str) -> PatternFill:
    return PatternFill("solid", fgColor=colour)


def _wrapped_height(text: str, width_units: float, size: int = 10, minimum: int = 15) -> int:
    """A readable row height for wrapped text in a merged cell.

    Deliberately conservative: the brief is meant to breathe, but a 200-word
    trade mark description must never turn one row into half a screen, so the
    text that reaches here is already summarised and the result is capped.
    """
    per_line = max(int(width_units * _CHARS_PER_WIDTH_UNIT), 10)
    lines = 0
    for paragraph in text.split("\n"):
        lines += max(1, -(-len(paragraph) // per_line))
    return max(minimum, min(int(lines * size * _POINTS_PER_LINE + _LINE_PADDING), 150))


def _set_page(sheet: Worksheet, palette: Palette, last_row: int, last_column: int) -> None:
    """Paint the sheet ground and turn gridlines off.

    An Excel sheet with gridlines showing reads as a spreadsheet; without them,
    and on an off-white ground, it reads as a document.
    """
    sheet.sheet_view.showGridLines = False
    page = _fill(palette.page)
    for row in sheet.iter_rows(min_row=1, max_row=last_row, min_col=1, max_col=last_column):
        for cell in row:
            if cell.fill.fgColor.rgb in (None, "00000000"):
                cell.fill = page


def _box(
    sheet: Worksheet,
    first_row: int,
    last_row: int,
    first_col: int,
    last_col: int,
    palette: Palette,
    fill: str | None = None,
) -> None:
    """Draw a card: a flat fill with a single hairline border around it."""
    body = _fill(fill or palette.card)
    edge = _thin(palette.line)
    for r in range(first_row, last_row + 1):
        for c in range(first_col, last_col + 1):
            cell = sheet.cell(row=r, column=c)
            cell.fill = body
            cell.border = Border(
                top=edge if r == first_row else None,
                bottom=edge if r == last_row else None,
                left=edge if c == first_col else None,
                right=edge if c == last_col else None,
            )


def _wordmark(palette: Palette, brand: dict, size: int = 20) -> CellRichText:
    return CellRichText(
        TextBlock(
            InlineFont(rFont=palette.font, sz=size, b=True, color=palette.ink[2:]),
            brand["wordmark_first"],
        ),
        TextBlock(
            InlineFont(rFont=palette.font, sz=size, b=True, color=palette.accent[2:]),
            brand["wordmark_second"],
        ),
    )


def _merge(sheet: Worksheet, row: int, first_col: int, last_col: int):
    sheet.merge_cells(start_row=row, start_column=first_col, end_row=row, end_column=last_col)
    return sheet.cell(row=row, column=first_col)


def _pretty_date(value: date | None) -> str:
    return value.strftime("%-d %B %Y") if value else "date not recorded"


# --------------------------------------------------------------------------
# Tab 1 — WEEKLY BRIEF
# --------------------------------------------------------------------------


def _build_brief(sheet: Worksheet, report: CustomerReport, config: dict, palette: Palette) -> None:
    copy = config["copy"]
    first = 2
    last = first + len(_BRIEF_COLUMNS) - 1
    span = _BRIEF_WIDTH * len(_BRIEF_COLUMNS)

    sheet.column_dimensions["A"].width = _GUTTER
    for index in range(len(_BRIEF_COLUMNS)):
        sheet.column_dimensions[get_column_letter(first + index)].width = _BRIEF_WIDTH
    sheet.column_dimensions[get_column_letter(last + 1)].width = _GUTTER

    row = 2
    sheet.row_dimensions[row].height = 30
    mark = sheet.cell(row=row, column=first, value=_wordmark(palette, config["brand"]))
    mark.alignment = Alignment(vertical="center")
    note = _merge(sheet, row, first + 4, last)
    note.value = copy["sample_note"]
    note.font = Font(name=palette.font, size=9, italic=True, color=palette.faint)
    note.alignment = Alignment(horizontal="right", vertical="center")

    row += 1
    eyebrow = _merge(sheet, row, first, last)
    eyebrow.value = f"{copy['report_type'].upper()}  ·  {copy['brief_title'].upper()}"
    eyebrow.font = Font(name=palette.font, size=9, bold=True, color=palette.accent)
    sheet.row_dimensions[row].height = 16

    row += 2
    title = _merge(sheet, row, first, last)
    title.value = f"Prepared for {report.customer_name}"
    title.font = Font(name=palette.font, size=22, bold=True, color=palette.ink)
    title.alignment = Alignment(vertical="center")
    sheet.row_dimensions[row].height = 30

    row += 1
    week = _merge(sheet, row, first, last)
    published = _pretty_date(report.publication_date)
    week.value = f"Journal week {report.journal_number}  ·  published {published}"
    week.font = Font(name=palette.font, size=11, color=palette.muted)
    sheet.row_dimensions[row].height = 18

    # Four metric tiles, two columns each.
    row += 2
    _, strongest_count, strongest_label = (
        report.category_counts[0] if report.category_counts else ("", 0, "—")
    )
    tiles = [
        (str(report.total), "Opportunities matched to you"),
        (str(report.top_count), "Top priority this week"),
        (str(len(report.category_counts)), "Product categories"),
        (str(strongest_count), f"Largest cluster: {strongest_label}"),
    ]
    value_row, label_row = row, row + 1
    sheet.row_dimensions[value_row].height = 34
    sheet.row_dimensions[label_row].height = 26
    for index, (value, label) in enumerate(tiles):
        left = first + index * 2
        right = left + 1
        _box(sheet, value_row, label_row, left, right, palette, fill=palette.card)
        cell = _merge(sheet, value_row, left, right)
        cell.value = value
        cell.font = Font(name=palette.font, size=24, bold=True, color=palette.accent)
        cell.alignment = Alignment(horizontal="center", vertical="bottom")
        caption = _merge(sheet, label_row, left, right)
        caption.value = label
        caption.font = Font(name=palette.font, size=9, color=palette.faint)
        caption.alignment = Alignment(horizontal="center", vertical="top", wrap_text=True)

    # What the report represents.
    row = label_row + 2
    body = str(copy["what_this_is"]).format(customer=report.customer_name)
    text = _merge(sheet, row, first, last)
    text.value = body
    text.font = Font(name=palette.font, size=10.5, color=palette.muted)
    text.alignment = Alignment(wrap_text=True, vertical="top")
    sheet.row_dimensions[row].height = _wrapped_height(body, span, size=11, minimum=44)

    # This week's signal.
    row += 2
    heading = _merge(sheet, row, first, last)
    heading.value = str(copy["signal_title"]).upper()
    heading.font = Font(name=palette.font, size=9, bold=True, color=palette.accent)
    sheet.row_dimensions[row].height = 16

    row += 1
    signal_rows = 2
    _box(sheet, row, row + signal_rows - 1, first, last, palette, fill=palette.accent_tint)
    sheet.merge_cells(
        start_row=row, start_column=first, end_row=row + signal_rows - 1, end_column=last
    )
    signal = sheet.cell(row=row, column=first)
    signal.value = report.signal
    signal.font = Font(name=palette.font, size=11, color=palette.accent_dark)
    signal.alignment = Alignment(wrap_text=True, vertical="center", indent=1)
    height = _wrapped_height(report.signal, span - 4, size=11, minimum=40)
    sheet.row_dimensions[row].height = height / 2
    sheet.row_dimensions[row + signal_rows - 1].height = height / 2
    row += signal_rows

    # The cards.
    row += 2
    section = _merge(sheet, row, first, last)
    section.value = str(copy["top_section_title"]).upper()
    section.font = Font(name=palette.font, size=9, bold=True, color=palette.accent)
    sheet.row_dimensions[row].height = 16

    row += 1
    hint = _merge(sheet, row, first, last)
    hint.value = copy["top_section_note"]
    hint.font = Font(name=palette.font, size=10, color=palette.faint)
    sheet.row_dimensions[row].height = 16

    row += 2
    for rank, opportunity in enumerate(report.highlights, start=1):
        row = _brief_card(sheet, row, rank, opportunity, config, palette, first, last)
        row += 1

    row += 1
    disclaimer = _merge(sheet, row, first, last)
    disclaimer.value = copy["angle_disclaimer"]
    disclaimer.font = Font(name=palette.font, size=9, italic=True, color=palette.faint)
    disclaimer.alignment = Alignment(wrap_text=True, vertical="top")
    sheet.row_dimensions[row].height = 28

    row += 1
    footer = _merge(sheet, row, first, last)
    footer.value = copy["footer"]
    footer.font = Font(name=palette.font, size=9, color=palette.faint)
    footer.alignment = Alignment(wrap_text=True, vertical="top")
    sheet.row_dimensions[row].height = 28

    _set_page(sheet, palette, row + 2, last + 1)
    sheet.freeze_panes = "A7"
    sheet.page_setup.orientation = "portrait"
    sheet.page_setup.fitToWidth = 1
    sheet.sheet_properties.pageSetUpPr.fitToPage = True


def _priority_style(priority: str, palette: Palette) -> tuple[str | None, str]:
    if priority == "TOP MATCH":
        return palette.top_fill, palette.top_text
    if priority == "STRONG":
        return palette.strong_fill, palette.strong_text
    return None, palette.relevant_text


def _brief_card(
    sheet: Worksheet,
    row: int,
    rank: int,
    opportunity: CustomerRow,
    config: dict,
    palette: Palette,
    first: int,
    last: int,
) -> int:
    """One opportunity, as a senior reader needs it and no more."""
    label_width = _BRIEF_WIDTH
    value_span = _BRIEF_WIDTH * (last - first) - 2

    lines = [
        ("Product", opportunity.product),
        ("Why now", opportunity.why_now),
        ("Relevance", opportunity.why_relevant),
        ("Sales angle", opportunity.sales_angle),
    ]
    top = row
    bottom = top + 1 + len(lines)
    _box(sheet, top, bottom, first, last, palette)

    name = _merge(sheet, top, first, first + 4)
    name.value = f"{rank}.  {opportunity.brand}"
    name.font = Font(name=palette.font, size=14, bold=True, color=palette.ink)
    name.alignment = Alignment(vertical="center", indent=1)
    sheet.row_dimensions[top].height = 26

    badge = _merge(sheet, top, first + 5, last)
    badge.value = opportunity.priority
    fill, colour = _priority_style(opportunity.priority, palette)
    badge.font = Font(name=palette.font, size=10, bold=True, color=colour)
    badge.alignment = Alignment(horizontal="right", vertical="center", indent=1)
    if fill:
        badge.fill = _fill(fill)

    meta_row = top + 1
    meta = _merge(sheet, meta_row, first, last)
    pieces = [opportunity.company, opportunity.location, opportunity.product_category]
    meta.value = "  ·  ".join(piece for piece in pieces if piece)
    meta.font = Font(name=palette.font, size=10, color=palette.muted)
    meta.alignment = Alignment(vertical="center", indent=1)
    sheet.row_dimensions[meta_row].height = 18

    for index, (label, value) in enumerate(lines):
        current = meta_row + 1 + index
        tag = sheet.cell(row=current, column=first, value=label)
        tag.font = Font(name=palette.font, size=9, bold=True, color=palette.faint)
        tag.alignment = Alignment(vertical="top", indent=1)
        cell = _merge(sheet, current, first + 1, last)
        cell.value = value
        cell.font = Font(name=palette.font, size=10.5, color=palette.ink)
        cell.alignment = Alignment(wrap_text=True, vertical="top")
        sheet.row_dimensions[current].height = _wrapped_height(
            value, value_span, size=11, minimum=18
        )
    del label_width
    return bottom


# --------------------------------------------------------------------------
# Tab 2 — OPPORTUNITIES
# --------------------------------------------------------------------------

# (header, column width, alignment). The last three are intentionally empty:
# they belong to the customer's sales team, and LaunchTrace never writes to them.
_COLUMNS: list[tuple[str, float, str]] = [
    ("Priority", 12.5, "left"),
    ("Brand", 21, "left"),
    ("Company", 26, "left"),
    ("Location", 14, "left"),
    ("Product Category", 22, "left"),
    ("Product", 30, "left"),
    ("Stage", 14, "left"),
    ("Why Now", 42, "left"),
    ("Why Relevant to You", 44, "left"),
    ("Suggested Sales Angle", 44, "left"),
    ("Website", 24, "left"),
    ("Trade Mark", 15, "left"),
    ("Company Number", 15, "left"),
    ("Filed", 11, "center"),
    ("Published", 12, "center"),
    ("Owner", 14, "left"),
    ("Sales Status", 15, "left"),
    ("Sales Notes", 30, "left"),
]

_HEADER_ROW = 4
_FIRST_DATA_ROW = _HEADER_ROW + 1
_EDITABLE_COLUMNS = 3


def _build_opportunities(
    sheet: Worksheet, report: CustomerReport, config: dict, palette: Palette
) -> None:
    copy = config["copy"]
    for index, (_, width, _align) in enumerate(_COLUMNS, start=1):
        sheet.column_dimensions[get_column_letter(index)].width = width
    last_col = len(_COLUMNS)

    heading = _merge(sheet, 1, 1, 6)
    heading.value = _wordmark(palette, config["brand"], size=13)
    heading.alignment = Alignment(vertical="center")
    sheet.row_dimensions[1].height = 22

    subtitle = _merge(sheet, 2, 1, 8)
    subtitle.value = (
        f"{report.customer_name}  ·  {copy['report_type']}  ·  journal week "
        f"{report.journal_number}  ·  {report.total} opportunities"
    )
    subtitle.font = Font(name=palette.font, size=10, color=palette.muted)
    sheet.row_dimensions[2].height = 16

    marker = _merge(sheet, 1, 9, last_col)
    marker.value = copy["sample_note"]
    marker.font = Font(name=palette.font, size=9, italic=True, color=palette.faint)
    marker.alignment = Alignment(horizontal="right", vertical="center")

    disclaimer = _merge(sheet, 3, 1, 10)
    disclaimer.value = copy["angle_disclaimer"]
    disclaimer.font = Font(name=palette.font, size=9, italic=True, color=palette.faint)
    disclaimer.alignment = Alignment(horizontal="left", vertical="center")
    sheet.row_dimensions[3].height = 16

    header_font = Font(name=palette.font, size=10, bold=True, color="FFFFFFFF")
    header_fill = _fill(palette.accent_dark)
    for index, (name, _width, align) in enumerate(_COLUMNS, start=1):
        cell = sheet.cell(row=_HEADER_ROW, column=index, value=name)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal=align, vertical="center", wrap_text=True)
    sheet.row_dimensions[_HEADER_ROW].height = 28

    body = Font(name=palette.font, size=10, color=palette.ink)
    muted = Font(name=palette.font, size=10, color=palette.muted)
    link = Font(name=palette.font, size=10, color=palette.accent, underline="single")
    edge = _thin(palette.line)

    for offset, row_data in enumerate(report.rows):
        r = _FIRST_DATA_ROW + offset
        values = [
            row_data.priority,
            row_data.brand,
            row_data.company,
            row_data.location,
            row_data.product_category,
            row_data.product,
            row_data.stage,
            row_data.why_now,
            row_data.why_relevant,
            row_data.sales_angle,
            row_data.website,
            row_data.trademark_number,
            row_data.company_number,
            row_data.filing_date,
            row_data.publication_date,
            None,
            None,
            None,
        ]
        for index, value in enumerate(values, start=1):
            cell = sheet.cell(row=r, column=index, value=value)
            cell.font = body
            cell.border = Border(bottom=edge)
            cell.alignment = Alignment(
                horizontal=_COLUMNS[index - 1][2], vertical="top", wrap_text=True
            )
        sheet.cell(row=r, column=1).font = Font(
            name=palette.font,
            size=10,
            bold=True,
            color=_priority_style(row_data.priority, palette)[1],
        )
        sheet.cell(row=r, column=2).font = Font(
            name=palette.font, size=10, bold=True, color=palette.ink
        )
        for column in (8, 9, 10):
            sheet.cell(row=r, column=column).font = muted

        website_cell = sheet.cell(row=r, column=11)
        if row_data.website_url:
            website_cell.hyperlink = row_data.website_url
            website_cell.value = re.sub(r"^https?://", "", row_data.website_url)
            website_cell.font = link
        else:
            website_cell.font = Font(name=palette.font, size=10, italic=True, color=palette.faint)

        trademark_cell = sheet.cell(row=r, column=12)
        trademark_cell.hyperlink = row_data.trademark_url
        trademark_cell.font = link

        company_cell = sheet.cell(row=r, column=13)
        if row_data.company_url:
            company_cell.hyperlink = row_data.company_url
            company_cell.font = link

        for column in (14, 15):
            sheet.cell(row=r, column=column).number_format = "dd mmm yyyy"

        editable = _fill(palette.accent_tint)
        for column in range(last_col - _EDITABLE_COLUMNS + 1, last_col + 1):
            cell = sheet.cell(row=r, column=column)
            cell.fill = editable
            cell.border = Border(bottom=edge, left=_thin(palette.line))

        sheet.row_dimensions[r].height = max(
            _wrapped_height(row_data.why_now, 42, minimum=30),
            _wrapped_height(row_data.why_relevant, 44, minimum=30),
            _wrapped_height(row_data.sales_angle, 44, minimum=30),
        )

    last_row = _FIRST_DATA_ROW + len(report.rows) - 1
    table = Table(
        displayName="LaunchTraceOpportunities",
        ref=f"A{_HEADER_ROW}:{get_column_letter(last_col)}{last_row}",
    )
    table.tableStyleInfo = TableStyleInfo(
        name="TableStyleLight1",
        showRowStripes=False,
        showColumnStripes=False,
        showFirstColumn=False,
        showLastColumn=False,
    )
    sheet.add_table(table)

    status_column = get_column_letter(last_col - 1)
    options = ",".join(config["sales_status_options"])
    validation = DataValidation(
        type="list",
        formula1=f'"{options}"',
        allow_blank=True,
        showDropDown=False,
        promptTitle="Sales status",
        prompt="Pick a status, or type your own.",
    )
    sheet.add_data_validation(validation)
    validation.add(f"{status_column}{_FIRST_DATA_ROW}:{status_column}{last_row}")

    # One subtle emphasis, on the column a manager sorts by.
    priority_range = f"A{_FIRST_DATA_ROW}:A{last_row}"
    sheet.conditional_formatting.add(
        priority_range,
        FormulaRule(formula=[f'$A{_FIRST_DATA_ROW}="TOP MATCH"'], fill=_fill(palette.top_fill)),
    )
    sheet.conditional_formatting.add(
        priority_range,
        FormulaRule(formula=[f'$A{_FIRST_DATA_ROW}="STRONG"'], fill=_fill(palette.strong_fill)),
    )

    note_row = last_row + 2
    note = _merge(sheet, note_row, 1, 10)
    note.value = (
        "Owner, Sales Status and Sales Notes are yours — LaunchTrace never writes to them. "
        + str(copy["footer"])
    )
    note.font = Font(name=palette.font, size=9, color=palette.faint)
    note.alignment = Alignment(vertical="center")

    _set_page(sheet, palette, note_row + 1, last_col)
    sheet.sheet_view.showGridLines = False
    sheet.freeze_panes = f"D{_FIRST_DATA_ROW}"
    sheet.page_setup.orientation = "landscape"
    sheet.print_title_rows = f"{_HEADER_ROW}:{_HEADER_ROW}"


# --------------------------------------------------------------------------
# Tab 3 — HOW TO USE
# --------------------------------------------------------------------------


def _build_how_to_use(sheet: Worksheet, config: dict, palette: Palette) -> None:
    copy = config["copy"]
    first, last = 2, 7
    span = 18.0 * (last - first + 1)

    sheet.column_dimensions["A"].width = _GUTTER
    for index in range(first, last + 1):
        sheet.column_dimensions[get_column_letter(index)].width = 18.0
    sheet.column_dimensions[get_column_letter(last + 1)].width = _GUTTER

    row = 2
    sheet.row_dimensions[row].height = 28
    mark = sheet.cell(row=row, column=first, value=_wordmark(palette, config["brand"], size=16))
    mark.alignment = Alignment(vertical="center")

    row += 1
    title = _merge(sheet, row, first, last)
    title.value = "How to use this report"
    title.font = Font(name=palette.font, size=18, bold=True, color=palette.ink)
    sheet.row_dimensions[row].height = 26

    row += 1
    standfirst = _merge(sheet, row, first, last)
    standfirst.value = "Two minutes, and you will know what every column means."
    standfirst.font = Font(name=palette.font, size=11, color=palette.muted)
    sheet.row_dimensions[row].height = 20

    row += 2
    for block in copy["how_to_use"]:
        heading = _merge(sheet, row, first, last)
        heading.value = block["heading"]
        heading.font = Font(name=palette.font, size=11.5, bold=True, color=palette.accent_dark)
        heading.alignment = Alignment(vertical="center")
        sheet.row_dimensions[row].height = 20

        row += 1
        body = _merge(sheet, row, first, last)
        body.value = block["body"]
        body.font = Font(name=palette.font, size=10.5, color=palette.ink)
        body.alignment = Alignment(wrap_text=True, vertical="top")
        sheet.row_dimensions[row].height = _wrapped_height(block["body"], span, size=11, minimum=30)
        row += 2

    footer = _merge(sheet, row, first, last)
    footer.value = copy["footer"]
    footer.font = Font(name=palette.font, size=9, color=palette.faint)
    footer.alignment = Alignment(wrap_text=True, vertical="top")
    sheet.row_dimensions[row].height = 28

    _set_page(sheet, palette, row + 1, last + 1)
    sheet.freeze_panes = "A6"
    sheet.page_setup.fitToWidth = 1
    sheet.sheet_properties.pageSetUpPr.fitToPage = True


# --------------------------------------------------------------------------


def write_workbook(report: CustomerReport, path: str | Path, config: dict | None = None) -> Path:
    """Render the three tabs and save. Returns the path written."""
    cfg = config or load_config("customer_report.json")
    palette = Palette.from_config(cfg["brand"])

    workbook = Workbook()
    brief = workbook.active
    brief.title = BRIEF_SHEET
    opportunities = workbook.create_sheet(OPPORTUNITIES_SHEET)
    how_to = workbook.create_sheet(HOW_TO_SHEET)

    _build_brief(brief, report, cfg, palette)
    _build_opportunities(opportunities, report, cfg, palette)
    _build_how_to_use(how_to, cfg, palette)

    workbook.properties.title = f"{cfg['copy']['report_type']} — {report.customer_name}"
    workbook.properties.creator = "LaunchTrace"
    workbook.properties.description = (
        f"Weekly opportunity brief for {report.customer_name}, journal week "
        f"{report.journal_number}. Sample — not a live subscription."
    )
    workbook.active = 0

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(path)
    return path


__all__ = [
    "BRIEF_SHEET",
    "HOW_TO_SHEET",
    "OPPORTUNITIES_SHEET",
    "Palette",
    "write_workbook",
]
