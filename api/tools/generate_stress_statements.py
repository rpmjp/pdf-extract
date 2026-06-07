import argparse
import json
import random
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageFilter
from reportlab.lib import colors
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas


@dataclass
class Txn:
    day: int
    description: str
    kind: str
    amount: float


@dataclass
class Case:
    slug: str
    bank: str
    holder: str
    account: str
    period: str
    opening: float
    txns: list[Txn]
    expected_status: str = "verified"
    currency: str = "$"
    date_format: str = "iso"
    scanned: bool = False
    noisy: bool = False
    multipage: bool = False
    prose: bool = False
    omit_balances: bool = False
    closing_override: float | None = None
    notes: str = ""


def money(value: float, currency: str, euro_format: bool = False) -> str:
    if euro_format:
        whole, cents = f"{abs(value):,.2f}".split(".")
        formatted = whole.replace(",", ".") + "," + cents
        return f"{currency}{formatted}"
    return f"{currency}{value:,.2f}"


def tx_date(day: int, fmt: str) -> str:
    if fmt == "uk":
        return f"{day:02d}/03/2026"
    if fmt == "jp":
        return f"2026/{day:02d}/03"
    return f"2026-03-{day:02d}"


def balances(case: Case) -> list[float]:
    running = case.opening
    out = []
    for txn in case.txns:
        running += txn.amount if txn.kind == "deposit" else -txn.amount
        out.append(round(running, 2))
    return out


def closing(case: Case) -> float:
    return case.closing_override if case.closing_override is not None else balances(case)[-1]


def statement_lines(case: Case) -> list[str]:
    euro = case.currency == "EUR "
    lines = [
        case.bank,
        f"Account holder: {case.holder}",
        f"Account number: {case.account}",
        f"Statement period: {case.period}",
        f"Opening balance: {money(case.opening, case.currency, euro)}",
        f"Closing balance: {money(closing(case), case.currency, euro)}",
        "",
    ]

    if case.prose:
        lines.append("Transactions narrative")
        running = case.opening
        for txn in case.txns:
            running += txn.amount if txn.kind == "deposit" else -txn.amount
            direction = "credited" if txn.kind == "deposit" else "debited"
            balance_text = "" if case.omit_balances else f" Running balance {money(running, case.currency, euro)}."
            lines.append(
                f"On {tx_date(txn.day, case.date_format)}, {txn.description} {direction} "
                f"{money(txn.amount, case.currency, euro)}.{balance_text}"
            )
        return lines

    lines.extend(
        [
            "Date        Description                         Debit        Credit       Balance",
            "----------  ----------------------------------  -----------  -----------  -----------",
        ]
    )
    for txn, bal in zip(case.txns, balances(case)):
        debit = money(txn.amount, case.currency, euro) if txn.kind == "withdrawal" else ""
        credit = money(txn.amount, case.currency, euro) if txn.kind == "deposit" else ""
        bal_text = "" if case.omit_balances else money(bal, case.currency, euro)
        lines.append(f"{tx_date(txn.day, case.date_format):<10}  {txn.description:<34}  {debit:>11}  {credit:>11}  {bal_text:>11}")
    return lines


def draw_text_pdf(path: Path, case: Case):
    c = canvas.Canvas(str(path), pagesize=LETTER)
    width, height = LETTER
    y = height - 48
    page_count = 1
    c.setFont("Helvetica-Bold", 14)
    for i, line in enumerate(statement_lines(case)):
        if y < 54 or (case.multipage and i > 0 and i % 18 == 0):
            c.showPage()
            page_count += 1
            y = height - 48
            c.setFont("Helvetica-Bold", 10)
            c.drawString(48, y, f"{case.bank} - continued - page {page_count}")
            y -= 24
        if i == 0:
            c.setFont("Helvetica-Bold", 16)
        elif line.startswith("Date") or line.startswith("Transactions"):
            c.setFont("Helvetica-Bold", 10)
        else:
            c.setFont("Courier", 8.5 if len(line) > 100 else 9.5)
        c.drawString(48, y, line)
        y -= 18
    c.save()


def draw_scanned_pdf(path: Path, case: Case):
    lines = statement_lines(case)
    image = Image.new("RGB", (1700, 2200), "white")
    draw = ImageDraw.Draw(image)
    try:
        font = ImageFont.truetype("DejaVuSansMono.ttf", 28)
        bold = ImageFont.truetype("DejaVuSans-Bold.ttf", 34)
    except Exception:
        font = ImageFont.load_default()
        bold = font
    y = 90
    for i, line in enumerate(lines):
        draw.text((90, y), line, fill="black", font=bold if i == 0 else font)
        y += 44
    if case.noisy:
        pixels = image.load()
        random.seed(case.slug)
        for _ in range(25000):
            x = random.randrange(image.width)
            y = random.randrange(image.height)
            shade = random.randrange(170, 245)
            pixels[x, y] = (shade, shade, shade)
        image = image.rotate(random.choice([-1.2, 1.1]), expand=True, fillcolor="white")
        image = image.filter(ImageFilter.GaussianBlur(radius=0.35))

    c = canvas.Canvas(str(path), pagesize=LETTER)
    c.drawImage(ImageReader(image), 18, 18, width=LETTER[0] - 36, height=LETTER[1] - 36, preserveAspectRatio=True)
    c.save()


def base_txns() -> list[Txn]:
    return [
        Txn(2, "Payroll deposit - Northstar Ltd", "deposit", 2850.75),
        Txn(4, "ATM withdrawal downtown", "withdrawal", 160.00),
        Txn(7, "Market purchase", "withdrawal", 82.34),
        Txn(12, "Online bill payment utilities", "withdrawal", 214.19),
        Txn(16, "Mobile deposit refund", "deposit", 73.40),
        Txn(21, "Card payment transfer", "withdrawal", 500.00),
    ]


def cases() -> list[Case]:
    tx = base_txns()
    return [
        Case("us_clean_retail", "Hudson River Bank", "Robert J. Pierre", "****4821", "March 1, 2026 - March 31, 2026", 3250.00, tx),
        Case("joint_account", "Garden State Credit Union", "Robert J. Pierre and Sofia A. Reyes", "Joint ****8842", "March 2026", 6140.55, tx + [Txn(24, "Joint account grocery reimbursement", "deposit", 125.00)]),
        Case("returned_item", "Union Metro Bank", "Maya Chen", "****1130", "March 2026", 1200.00, tx + [Txn(25, "Returned mobile deposit item", "withdrawal", 250.00), Txn(26, "Returned item fee", "withdrawal", 15.00)]),
        Case("nsf_reversal", "North Coast Bank", "Darius King", "****7012", "March 2026", 900.00, tx + [Txn(20, "ACH debit returned NSF", "deposit", 180.00), Txn(20, "NSF fee", "withdrawal", 35.00)]),
        Case("uk_date_format", "Canary Wharf Bank", "Amelia Foster", "Sort 20-10-44 / ****3001", "01/03/2026 - 31/03/2026", 2210.44, tx, currency="GBP ", date_format="uk"),
        Case("euro_decimal_comma", "Banco Atlántico", "Lucía Martín", "ES****7710", "1 März 2026 - 31 März 2026", 1880.20, tx, currency="EUR ", date_format="uk"),
        Case("japan_date_format", "Sakura Trust", "Kenji Tanaka", "普通 ****4420", "2026/03/01 - 2026/03/31", 440000.00, [Txn(3, "Salary payment", "deposit", 310000), Txn(5, "Rent transfer", "withdrawal", 140000), Txn(9, "Transit card top-up", "withdrawal", 10000), Txn(15, "Insurance premium", "withdrawal", 12500)], currency="JPY ", date_format="jp"),
        Case("multi_currency_notes", "Meridian International", "Priya Shah", "****9901", "March 2026", 5000.00, tx + [Txn(18, "FX card purchase EUR 42.10 converted", "withdrawal", 45.66), Txn(19, "Foreign transaction fee", "withdrawal", 1.37)]),
        Case("wire_fee", "First Harbor Bank", "Noah Williams", "****6088", "March 2026", 7200.00, tx + [Txn(10, "Incoming wire - client", "deposit", 1800.00), Txn(10, "Wire processing fee", "withdrawal", 18.00)]),
        Case("zero_like_reference", "Pioneer Bank", "Owen Patel", "****4177", "March 2026", 2600.00, tx + [Txn(22, "Check #000104 cleared", "withdrawal", 104.00)]),
        Case("prose_no_balances", "Narrative Savings", "Emma Johnson", "****2109", "March 2026", 1500.00, tx, prose=True, omit_balances=True),
        Case("multipage_long", "Summit National", "Grace Lee", "****7788", "March 2026", 10000.00, tx + [Txn(i, f"Point of sale purchase #{i}", "withdrawal", 10 + i) for i in range(1, 22)], multipage=True),
        Case("scanned_clean", "Image Federal Bank", "Liam Garcia", "****3489", "March 2026", 3100.00, tx, scanned=True),
        Case("scanned_noisy", "Noisy Scan Credit Union", "Ava Thompson", "****9191", "March 2026", 4100.00, tx, scanned=True, noisy=True),
        Case("bad_closing_balance", "Mismatch Bank", "Henry Brown", "****1515", "March 2026", 2200.00, tx, expected_status="needs_review", closing_override=9999.99),
        Case("missing_running_balances", "Sparse Statement Bank", "Ella Wilson", "****6161", "March 2026", 1700.00, tx, expected_status="needs_review", omit_balances=True),
        Case("same_day_many", "Chronicle Bank", "Benjamin Davis", "****7227", "March 2026", 2800.00, [Txn(8, "Coffee shop", "withdrawal", 4.95), Txn(8, "Book store", "withdrawal", 31.25), Txn(8, "Payroll correction", "deposit", 120.00), Txn(8, "Parking meter", "withdrawal", 8.00)]),
        Case("large_amounts", "Capital Reserve", "Victoria Miller", "****8090", "March 2026", 125000.00, [Txn(3, "Treasury transfer in", "deposit", 50000), Txn(6, "Vendor wire outbound", "withdrawal", 43750.25), Txn(16, "Quarterly tax payment", "withdrawal", 12000)]),
        Case("small_cents", "Micro Savings", "Leo Martinez", "****3030", "March 2026", 10.00, [Txn(2, "Interest credit", "deposit", 0.17), Txn(3, "Account fee", "withdrawal", 0.05), Txn(4, "Round-up transfer", "deposit", 1.23)]),
        Case("negative_printed_debits", "Legacy Ledger Bank", "Nora Anderson", "****9090", "March 2026", 900.00, [Txn(2, "Debit shown as -45.20 in source", "withdrawal", 45.20), Txn(5, "Credit shown as +100.00 in source", "deposit", 100.00)]),
        Case("loan_autopay", "Teachers Community Bank", "Samuel Clark", "****5151", "March 2026", 3600.00, tx + [Txn(28, "Auto loan payment principal", "withdrawal", 420.55)]),
        Case("payroll_split", "Coastal Employees CU", "Isabella Rodriguez", "****2323", "March 2026", 2300.00, [Txn(1, "Payroll net pay", "deposit", 1900), Txn(1, "Payroll savings split", "withdrawal", 300), Txn(2, "Rent", "withdrawal", 1200)]),
        Case("foreign_names", "Banque du Rhône", "Zoë Faure", "FR****4455", "Mars 2026", 3400.00, tx, currency="EUR ", date_format="uk"),
        Case("returned_check_redposit", "Liberty Mutual Bank", "Chris Morgan", "****6262", "March 2026", 800.00, [Txn(4, "Check deposit", "deposit", 450), Txn(8, "Deposited check returned", "withdrawal", 450), Txn(12, "Replacement ACH credit", "deposit", 450), Txn(15, "Returned deposit fee", "withdrawal", 12)]),
    ]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="/app/stress_output")
    parser.add_argument("--run-id", default=date.today().isoformat())
    args = parser.parse_args()

    out_dir = Path(args.out) / args.run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest = []
    for case in cases():
        file_name = f"stress_{args.run_id}_{case.slug}.pdf"
        path = out_dir / file_name
        if case.scanned:
            draw_scanned_pdf(path, case)
        else:
            draw_text_pdf(path, case)
        manifest.append(
            {
                "slug": case.slug,
                "file": str(path),
                "filename": file_name,
                "bank": case.bank,
                "expected_status": case.expected_status,
                "expected_txn_count": len(case.txns),
                "expected_closing": closing(case),
                "scanned": case.scanned,
                "noisy": case.noisy,
                "notes": case.notes,
            }
        )
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))
    print(json.dumps({"out_dir": str(out_dir), "cases": len(manifest)}, indent=2))


if __name__ == "__main__":
    main()
