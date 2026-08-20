"""Gold set for citation-accuracy eval.

Facts are taken verbatim from the HTML sources in ``scripts/make_samples.py``
so this file can be checked against the generator rather than trusted on
its own. Two contracts:

* 2026_공급계약서.pdf (supply contract) — total price ￦120,000,000, payment
  within 30 days of inspection, 2-year defect warranty, 6% p.a. late penalty,
  and a payment-schedule table (선급금 30% / 36,000,000, 중도금 40% /
  48,000,000, 잔금 30% / 36,000,000).
* 2025_유지보수계약서.pdf (maintenance contract) — an ANNUAL fee of
  ￦36,000,000 split evenly across months (not a monthly fee), payment
  within 15 days of the tax invoice, 1-year defect warranty.

Neither document mentions a court of jurisdiction for disputes, which is
what makes the negative case a genuine "no answer" rather than a retrieval
miss.
"""
from __future__ import annotations

GOLD_CASES: list[dict] = [
    {
        "id": "supply-price",
        "question": "서버장비 공급계약의 총 계약금액은 얼마인가요?",
        "document_filenames": ["2026_공급계약서.pdf"],
        "expect_answer_contains": ["120,000,000"],
        "expect_citation": {"filename": "2026_공급계약서.pdf"},
        "max_rejected": 0,
    },
    {
        "id": "supply-payment-term",
        "question": "공급계약 대금은 검수 완료 후 며칠 이내에 지급해야 하나요?",
        "document_filenames": ["2026_공급계약서.pdf"],
        "expect_answer_contains": ["30일"],
        "expect_citation": {"filename": "2026_공급계약서.pdf"},
        "max_rejected": 0,
    },
    {
        "id": "supply-warranty",
        "question": "공급계약의 하자보증 기간은 몇 년인가요?",
        "document_filenames": ["2026_공급계약서.pdf"],
        "expect_answer_contains": ["2년"],
        "expect_citation": {"filename": "2026_공급계약서.pdf"},
        "max_rejected": 0,
    },
    {
        "id": "supply-late-penalty",
        "question": "갑이 대금 지급을 지연할 경우 적용되는 지연이자율은 얼마인가요?",
        "document_filenames": ["2026_공급계약서.pdf"],
        "expect_answer_contains": ["6%"],
        "expect_citation": {"filename": "2026_공급계약서.pdf"},
        "max_rejected": 0,
    },
    {
        "id": "supply-schedule-table",
        "question": "공급계약의 중도금은 계약금액의 몇 퍼센트이며 금액은 얼마인가요?",
        "document_filenames": ["2026_공급계약서.pdf"],
        "expect_answer_contains": ["40%", "48,000,000"],
        "expect_citation": {"filename": "2026_공급계약서.pdf", "text_contains": "중도금"},
        "max_rejected": 0,
    },
    {
        "id": "maint-annual-fee",
        "question": "유지보수계약의 연간 유지보수 금액은 얼마인가요?",
        "document_filenames": ["2025_유지보수계약서.pdf"],
        "expect_answer_contains": ["36,000,000"],
        "expect_citation": {"filename": "2025_유지보수계약서.pdf"},
        "max_rejected": 0,
    },
    {
        "id": "maint-payment-term",
        "question": "유지보수계약 대금은 세금계산서 발행일로부터 며칠 이내에 지급하나요?",
        "document_filenames": ["2025_유지보수계약서.pdf"],
        "expect_answer_contains": ["15일"],
        "expect_citation": {"filename": "2025_유지보수계약서.pdf"},
        "max_rejected": 0,
    },
    {
        "id": "maint-warranty",
        "question": "유지보수계약의 하자보증 기간은 몇 년인가요?",
        "document_filenames": ["2025_유지보수계약서.pdf"],
        "expect_answer_contains": ["1년"],
        "expect_citation": {"filename": "2025_유지보수계약서.pdf"},
        "max_rejected": 0,
    },
    {
        "id": "cross-doc-warranty-compare",
        "question": "공급계약서와 유지보수계약서의 하자보증 기간을 비교해 주세요.",
        "document_filenames": ["2026_공급계약서.pdf", "2025_유지보수계약서.pdf"],
        "expect_answer_contains": ["2년", "1년"],
        "max_rejected": 0,
    },
    {
        "id": "no-context-negative",
        "question": "이 계약서들에 명시된 분쟁 발생 시 관할 법원은 어디인가요?",
        "document_filenames": ["2026_공급계약서.pdf", "2025_유지보수계약서.pdf"],
        "expect_answer_contains": [],
        "expect_no_citations": True,
        "max_rejected": 0,
    },
]
