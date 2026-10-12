"""Generate the Larkway synthetic dataset: six policy/contract PDFs and the workbook.

Everything here is fictional — the company, customers, carriers, people and figures.
The sources are deliberately imperfect, because that is what the agent is built to
handle: a deprecated policy that still reads as authoritative, contracts that
override the general rules, and closed tickets whose recorded answers are wrong.

    python scripts/generate_dataset.py

Needs `fpdf2` (dev-only, see requirements-dev.txt) and a TrueType font with a bullet
glyph. After regenerating, rebuild the index and re-verify the policy pack:

    python -m app.ingest && python scripts/verify_policy_pack.py
"""
from __future__ import annotations

import sys
from pathlib import Path

from fpdf import FPDF
from openpyxl import Workbook

ROOT = Path(__file__).resolve().parent.parent
DOCS_DIR = ROOT / "data" / "source_docs"
WORKBOOK = ROOT / "data" / "larkway_dataset.xlsx"

FONT_CANDIDATES = [
    Path(r"C:\Windows\Fonts\arial.ttf"),
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    Path("/Library/Fonts/Arial.ttf"),
]

# A document is a list of (kind, text) blocks:
#   title | meta | h (numbered heading or KI heading) | p | b (bullet) | row (table row)
Doc = list[tuple[str, str]]

SUPPORT_POLICY_V5: Doc = [
    ("title", "Larkway Support Policy v5"),
    ("meta", "Status: CURRENT"),
    ("meta", "Effective: 1 July 2026"),
    ("meta", "Supersedes: Support Policy v4"),
    ("h", "1. Scope and source precedence"),
    ("p", "This policy sets the default severity levels and first-response targets for "
          "Larkway support. A signed customer agreement can override these defaults. "
          "When sources disagree, apply the signed customer agreement first, then this "
          "support policy and the current SOPs, then current product documentation. "
          "Past tickets and internal notes are background only and may record incorrect "
          "advice."),
    ("h", "2. Severity definitions"),
    ("b", "P1 - Critical: Customers cannot create any shipments (a full production "
          "outage), a confirmed security incident or suspected credential exposure, or "
          "any other event causing immediate serious business risk with no workaround."),
    ("b", "P2 - High: A major feature is unavailable or badly degraded for a customer, "
          "but core operations can continue or a workaround exists."),
    ("b", "P3 - Normal: A minor defect, a how-to question, a configuration request, or "
          "an issue with limited operational impact."),
    ("h", "3. Default first-response targets"),
    ("row", "Plan | P1 | P2 | P3"),
    ("row", "Enterprise | 20 minutes, 24x7 | 90 minutes | 1 business day"),
    ("row", "Scale | 1 business hour | 3 business hours | 2 business days"),
    ("row", "Starter | 3 business hours | 1 business day | 3 business days"),
    ("h", "4. Escalation"),
    ("p", "Escalate every P1 incident immediately. If a first-response target has already "
          "been breached, say so plainly and recommend escalation instead of hiding the "
          "uncertainty."),
]

SUPPORT_POLICY_V4: Doc = [
    ("title", "Larkway Support Policy v4"),
    ("meta", "Status: DEPRECATED - DO NOT USE FOR CURRENT REQUESTS"),
    ("meta", "Effective: 1 March 2025"),
    ("meta", "Superseded by: Support Policy v5 effective 1 July 2026"),
    ("h", "1. Severity and response targets"),
    ("p", "P1 covers full production outages and serious security incidents. P2 covers "
          "major feature degradation. P3 covers minor issues and questions."),
    ("row", "Plan | P1 | P2 | P3"),
    ("row", "Enterprise | 45 minutes | 3 hours | 2 business days"),
    ("row", "Scale | 3 business hours | 6 business hours | 3 business days"),
    ("row", "Starter | 6 business hours | 2 business days | 4 business days"),
    ("p", "Note: This file is kept for historical reference only and must not be used as "
          "current policy."),
]

SOP_V3: Doc = [
    ("title", "Larkway Cancellation & Service Credit SOP v3"),
    ("meta", "Status: CURRENT"),
    ("meta", "Effective: 1 August 2026"),
    ("h", "1. Order cancellation"),
    ("b", "DRAFT: Can be cancelled at no charge."),
    ("b", "BOOKED, not yet PICKED_UP: Can be cancelled. No fee within 20 minutes of "
          "booking. After 20 minutes, charge INR 350 unless a customer agreement "
          "explicitly waives the cancellation fee."),
    ("b", "PICKED_UP: Do not cancel. Start the return-to-origin workflow if the customer "
          "wants the parcel back."),
    ("b", "DELIVERED: Cannot be cancelled."),
    ("h", "2. Failed-pickup service credits"),
    ("p", "Under the default policy, a customer qualifies for a service credit when pickup "
          "happens more than 90 minutes after the end of the scheduled pickup window, the "
          "carrier is at fault, and the customer caused no part of the delay. The default "
          "credit is the lower of INR 600 or 15% of the shipment fee. A signed customer "
          "agreement may replace the default delay threshold, credit amount, or cap."),
    ("h", "3. Approval and uncertainty"),
    ("b", "Any single credit above INR 1,500 needs manager approval."),
    ("b", "Do not promise a credit while carrier fault, pickup timing, or customer fault "
          "is unknown."),
    ("b", "When records conflict, name the conflict and get verification before any "
          "state-changing action."),
]

PRODUCT_GUIDE: Doc = [
    ("title", "Larkway Product Operations Guide"),
    ("meta", "Status: CURRENT"),
    ("meta", "Updated: 15 September 2026"),
    ("h", "1. Plan capabilities"),
    ("b", "Bulk Upload: Available on Scale and Enterprise. Supported file size is up to "
          "8,000 rows per CSV."),
    ("b", "Starter: Bulk Upload is not included."),
    ("b", "Shipment status: BOOKED means the shipment exists but Larkway has not yet "
          "received pickup confirmation from the carrier. PICKED_UP means the carrier has "
          "confirmed pickup."),
    ("h", "2. Current known issues"),
    ("h", "KI-314 - Bulk Upload timeouts on large files"),
    ("p", "Opened: 9 September 2026 Status: Investigating"),
    ("p", "Some Scale and Enterprise customers see intermittent timeouts on CSV uploads "
          "above roughly 2,500 rows, even though the supported limit is still 8,000 rows. "
          "Workaround: split the upload into files under 2,500 rows. Single shipment "
          "creation is not affected."),
    ("h", "KI-322 - TransVelo pickup confirmation lag"),
    ("p", "Opened: 14 September 2026 Status: Monitoring"),
    ("p", "TransVelo pickup confirmation webhooks can arrive up to 35 minutes late. A "
          "parcel may already be collected while Larkway still shows BOOKED. Before "
          "telling a customer a pickup did not happen, check the carrier's status or wait "
          "out the known delay window."),
    ("h", "3. Resolved issue"),
    ("h", "KI-290 - Label printing: Resolved 2 September 2026."),
    ("p", "Do not use this resolved issue to explain new incidents unless the evidence "
          "specifically matches it."),
]

HALCYON_AGREEMENT: Doc = [
    ("title", "Larkway - Halcyon Foods Enterprise Agreement"),
    ("meta", "Account: CUS-101"),
    ("meta", "Customer: Halcyon Foods"),
    ("meta", "Term: 1 February 2026 to 31 January 2027"),
    ("meta", "Status: ACTIVE"),
    ("h", "1. Support terms"),
    ("p", "For Halcyon Foods, the following first-response targets replace Larkway's "
          "standard support-policy targets:"),
    ("b", "P1: 10 minutes, 24x7"),
    ("b", "P2: 45 minutes"),
    ("b", "P3: 6 business hours"),
    ("h", "2. Shipment cancellation"),
    ("p", "Halcyon Foods may cancel any BOOKED shipment before pickup with no cancellation "
          "fee, however long ago the shipment was booked. Once a shipment is PICKED_UP, the "
          "standard return-to-origin process applies."),
    ("h", "3. Service credits"),
    ("p", "Total service credits in any calendar month are capped at INR 8,000. Unless "
          "this agreement says otherwise, the current Larkway service-credit SOP applies."),
    ("h", "4. Account contact"),
    ("p", "Dedicated CSM: Kavya Nair."),
]

BRIGHTMOOR_AGREEMENT: Doc = [
    ("title", "Larkway - Brightmoor Textiles Service Agreement"),
    ("meta", "Account: CUS-102"),
    ("meta", "Customer: Brightmoor Textiles"),
    ("meta", "Plan: Scale"),
    ("meta", "Term: 1 April 2026 to 31 March 2027"),
    ("meta", "Status: ACTIVE"),
    ("h", "1. Support terms"),
    ("b", "P1: 1 business hour"),
    ("b", "P2: 2 business hours"),
    ("b", "P3: 2 business days"),
    ("b", "No weekend or after-hours support coverage."),
    ("h", "2. Cancellation terms"),
    ("p", "No special cancellation-fee waiver applies. Use the current Larkway "
          "Cancellation & Service Credit SOP."),
    ("h", "3. Failed-pickup credits"),
    ("p", "If a pickup happens more than 3 hours after the end of the scheduled pickup "
          "window, the carrier is at fault, and the customer is not at fault, Brightmoor "
          "Textiles receives a fixed INR 2,000 service credit. This clause replaces the "
          "default failed-pickup credit amount and timing threshold in the SOP."),
]

DOCUMENTS: dict[str, Doc] = {
    "01_Support_Policy_v5_CURRENT.pdf": SUPPORT_POLICY_V5,
    "02_Support_Policy_v4_DEPRECATED.pdf": SUPPORT_POLICY_V4,
    "03_Cancellation_and_Service_Credit_SOP_v3.pdf": SOP_V3,
    "04_Product_Operations_Guide.pdf": PRODUCT_GUIDE,
    "05_Halcyon_Foods_Enterprise_Agreement.pdf": HALCYON_AGREEMENT,
    "06_Brightmoor_Textiles_Service_Agreement.pdf": BRIGHTMOOR_AGREEMENT,
}


def _font() -> Path:
    for path in FONT_CANDIDATES:
        if path.exists():
            return path
    sys.exit("No TrueType font with a bullet glyph found; edit FONT_CANDIDATES.")


def render(doc: Doc, out: Path, font: Path) -> None:
    pdf = FPDF(format="A4")
    pdf.set_margins(20, 18, 20)
    pdf.set_auto_page_break(True, margin=18)
    pdf.add_font("body", "", str(font))
    pdf.add_page()
    width = pdf.w - pdf.l_margin - pdf.r_margin
    for kind, text in doc:
        if kind == "title":
            pdf.set_font("body", size=16)
            pdf.multi_cell(width, 9, text)
            pdf.ln(2)
        elif kind == "meta":
            pdf.set_font("body", size=9.5)
            pdf.multi_cell(width, 5.5, text)
        elif kind == "h":
            pdf.ln(4)
            pdf.set_font("body", size=12)
            pdf.multi_cell(width, 7, text)
            pdf.ln(1)
        elif kind == "p":
            pdf.set_font("body", size=10.5)
            pdf.multi_cell(width, 5.8, text)
            pdf.ln(1.5)
        elif kind == "b":
            pdf.set_font("body", size=10.5)
            pdf.set_x(pdf.l_margin + 3)
            pdf.multi_cell(width - 3, 5.8, "\u25cf " + text)
            pdf.ln(0.8)
        elif kind == "row":
            # One line per row, cells in reading order, so the table extracts as a
            # predictable run of text the policy pack can quote verbatim.
            pdf.set_font("body", size=10.5)
            pdf.multi_cell(width, 6.2, "    ".join(c.strip() for c in text.split("|")))
        else:
            raise ValueError(kind)
    pdf.output(str(out))


# ---------------------------------------------------------------------------
# Workbook. Snapshot: Saturday 19 Sep 2026, 14:00 IST — a weekend on purpose, so
# business-hours targets have not been running since Friday 18:00.
# ---------------------------------------------------------------------------
README = [
    ("Larkway Support Agent - Synthetic Dataset", None),
    ("Dataset snapshot", "2026-09-19 14:00 Asia/Kolkata"),
    ("Currency", "INR"),
    ("Notes", "Fictional dataset for the Larkway support-agent project. All companies, "
              "people and figures are invented."),
    ("Important", "Some historical ticket resolutions are wrong on purpose. Treat them as "
                  "background, not as policy."),
]

ACCOUNTS = [
    ("account_id", "account_name", "plan", "status", "csm", "contract_file",
     "premium_support", "notes"),
    ("CUS-101", "Halcyon Foods", "Enterprise", "active", "Kavya Nair",
     "05_Halcyon_Foods_Enterprise_Agreement.pdf", True,
     "Key account. Contract has custom SLA and cancellation terms."),
    ("CUS-102", "Brightmoor Textiles", "Scale", "active", "Imran Sheikh",
     "06_Brightmoor_Textiles_Service_Agreement.pdf", False,
     "Scale customer with contract-specific failed-pickup credit terms."),
    ("CUS-103", "Pinecrest Home", "Starter", "active", "Leela Thomas", None, False,
     "No signed agreement on file; standard policies apply."),
    ("CUS-104", "Orbitron Medical", "Enterprise", "active", "Kavya Nair", None, False,
     "Enterprise plan with no custom agreement; the standard Enterprise policy applies."),
]

ORDERS = [
    ("order_id", "account_id", "carrier", "status", "booked_at", "pickup_window_start",
     "pickup_window_end", "pickup_actual_at", "shipment_fee_inr", "carrier_fault",
     "customer_fault", "cancellation_requested_at", "notes"),
    ("SHP-5001", "CUS-101", "TransVelo", "BOOKED", "2026-09-19 11:30", "2026-09-19 13:30",
     "2026-09-19 14:30", None, 6000.0, False, False, "2026-09-19 14:00",
     "Customer wants to cancel. Larkway shows the shipment as not yet collected."),
    ("SHP-5002", "CUS-101", "Coastline Couriers", "PICKED_UP", "2026-09-19 09:15",
     "2026-09-19 10:00", "2026-09-19 11:00", "2026-09-19 10:40", 7200.0, False, False,
     "2026-09-19 12:30", "Customer asked to cancel after the parcel was collected."),
    ("SHP-6001", "CUS-102", "MetroDash", "BOOKED", "2026-09-19 12:00", "2026-09-19 15:00",
     "2026-09-19 16:00", None, 2600.0, False, False, "2026-09-19 13:10",
     "Cancellation requested 70 minutes after booking; not yet collected."),
    ("SHP-6002", "CUS-102", "QuickHaul", "BOOKED", "2026-09-19 06:00", "2026-09-19 07:00",
     "2026-09-19 08:00", None, 3400.0, True, False, None,
     "Pickup missed. QuickHaul accepted fault. Still not collected at the snapshot."),
    ("SHP-7001", "CUS-103", "MetroDash", "BOOKED", "2026-09-19 13:48", "2026-09-19 15:30",
     "2026-09-19 16:30", None, 1500.0, False, False, "2026-09-19 14:00",
     "Cancellation requested a few minutes after booking."),
    ("SHP-8001", "CUS-104", "TransVelo", "DELIVERED", "2026-09-17 15:00", "2026-09-18 10:00",
     "2026-09-18 11:00", "2026-09-18 10:25", 4800.0, False, False, None,
     "Delivered."),
    ("SHP-8002", "CUS-104", "Coastline Couriers", "BOOKED", "2026-09-19 07:30",
     "2026-09-19 09:00", "2026-09-19 10:00", None, 5000.0, None, None, None,
     "Pickup missed. The carrier has not confirmed the cause yet."),
]

TICKETS = [
    ("ticket_id", "account_id", "created_at", "status", "subject", "description",
     "channel", "assigned_to", "last_customer_message_at", "historical_resolution"),
    ("CASE-301", "CUS-101", "2026-09-19 13:40", "open",
     "Cannot create any shipments - every request fails",
     "Every user at Halcyon Foods gets an HTTP 503 error when creating any shipment since "
     "13:35. Existing shipments can still be viewed.",
     "phone", "Arjun", "2026-09-19 13:55", None),
    ("CASE-302", "CUS-102", "2026-09-18 16:30", "open",
     "Bulk upload times out for 3,100-row CSV",
     "The upload stalls at about 60% and then times out. Creating shipments one at a time "
     "still works.",
     "chat", "Meera", "2026-09-18 17:10", None),
    ("CASE-303", "CUS-103", "2026-09-19 10:15", "open",
     "How do we add a second warehouse address?",
     "Customer wants to add a pickup address for their new warehouse.",
     "email", "Arjun", "2026-09-19 10:15", None),
    ("CASE-304", "CUS-101", "2026-09-19 13:50", "open",
     "TransVelo order still shows BOOKED after pickup",
     "The driver collected the parcel about 15 minutes ago, but Larkway still shows BOOKED.",
     "chat", "Meera", "2026-09-19 13:58", None),
    ("CASE-305", "CUS-104", "2026-09-19 12:10", "open",
     "Possible API key exposure",
     "A developer pushed a production API key to a public code repository and wants to "
     "know what to do.",
     "email", "Arjun", "2026-09-19 12:40", None),
    ("CASE-306", "CUS-104", "2026-09-19 12:50", "open",
     "Bulk upload times out for 2,800-row CSV",
     "Uploads stall and time out partway through. Creating single shipments still works.",
     "chat", "Meera", "2026-09-19 13:20", None),
    ("CASE-240", "CUS-101", "2026-08-28 15:20", "closed",
     "Cancellation fee after 20 minutes",
     "Halcyon Foods asked whether a BOOKED shipment could be cancelled two hours after "
     "booking, before pickup.",
     "email", "Meera", "2026-08-28 16:05",
     "Agent told the customer a INR 350 cancellation fee applied after 20 minutes."),
    ("CASE-251", "CUS-102", "2026-09-12 10:30", "closed",
     "Bulk upload times out for large CSV",
     "Brightmoor reported timeouts when uploading 3,000-row CSV files.",
     "chat", "Arjun", "2026-09-12 11:15",
     "Agent told the customer the Scale plan only supports 2,500 rows."),
]


def write_workbook() -> None:
    wb = Workbook()
    sheets = {"README": README, "accounts": ACCOUNTS, "orders": ORDERS, "tickets": TICKETS}
    first = True
    for name, rows in sheets.items():
        ws = wb.active if first else wb.create_sheet()
        ws.title = name
        first = False
        for row in rows:
            ws.append(list(row))
    wb.save(WORKBOOK)


def main() -> None:
    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    font = _font()
    for filename, doc in DOCUMENTS.items():
        render(doc, DOCS_DIR / filename, font)
        print(f"wrote {DOCS_DIR / filename}")
    write_workbook()
    print(f"wrote {WORKBOOK}")


if __name__ == "__main__":
    main()
