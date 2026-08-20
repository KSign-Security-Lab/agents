"""Generate Korean sample documents for pipeline verification.

Real files, real Korean text, real tables — so the conversion and geometry paths
are exercised the way an actual upload would exercise them.
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "samples")
OUT.mkdir(parents=True, exist_ok=True)

CONTRACT_HTML = """<!doctype html><html><head><meta charset="utf-8">
<style>
 body { font-family: 'NanumBarunGothic','Noto Sans CJK KR',sans-serif; font-size: 11pt; }
 h1 { font-size: 20pt; text-align:center; }
 h2 { font-size: 14pt; margin-top: 18pt; }
 table { border-collapse: collapse; width: 100%; font-size: 10pt; }
 td, th { border: 1px solid #444; padding: 4pt 6pt; }
</style></head><body>
<h1>2026년도 서버장비 공급계약서</h1>
<p>발주자(이하 "갑")와 수급자(이하 "을")는 다음과 같이 계약을 체결한다.</p>

<h2>제1조 (계약의 목적)</h2>
<p>본 계약은 갑이 필요로 하는 서버 장비를 을이 공급하고 설치하는 것을 목적으로 한다.
납품 장소는 갑이 지정하는 데이터센터로 한다.</p>

<h2>제2조 (계약금액)</h2>
<p>총 계약금액은 일억 이천만원(￦120,000,000)으로 하며, 부가가치세는 별도로 한다.</p>

<h2>제3조 (대금지급 조건)</h2>
<p>대금은 납품 및 검수가 완료된 날로부터 30일 이내에 을이 지정하는 계좌로 지급한다.
갑은 검수 완료 후 7일 이내에 검수결과를 을에게 통보하여야 한다.</p>

<h2>제4조 (하자보증)</h2>
<p>하자보증 기간은 준공일로부터 2년으로 한다. 하자보증 기간 중 발생한 하자에 대하여
을은 자신의 비용으로 이를 보수하여야 한다.</p>

<h2>제5조 (지연배상금)</h2>
<p>갑이 대금 지급을 지연하는 경우 지연일수에 대하여 연 6%의 지연이자를 가산하여 지급한다.
을이 납품을 지연하는 경우 지연일수 1일당 계약금액의 1천분의 1을 배상한다.</p>

<h2>제6조 (지급 일정표)</h2>
<table border="1">
<tr><th>구분</th><th>지급 시기</th><th>지급 비율</th><th>금액(원)</th></tr>
<tr><td>선급금</td><td>계약 체결 후 14일 내</td><td>30%</td><td>36,000,000</td></tr>
<tr><td>중도금</td><td>납품 완료 시</td><td>40%</td><td>48,000,000</td></tr>
<tr><td>잔금</td><td>검수 완료 후 30일 내</td><td>30%</td><td>36,000,000</td></tr>
</table>

<h2>제7조 (연락처)</h2>
<table>
<tr><td>구분</td><td>담당자</td><td>연락처</td></tr>
<tr><td>갑</td><td>김건오</td><td>02-1234-5678</td></tr>
<tr><td>을</td><td>박지원</td><td>02-8765-4321</td></tr>
</table>
</body></html>"""

MAINT_HTML = """<!doctype html><html><head><meta charset="utf-8">
<style>
 body { font-family: 'NanumBarunGothic','Noto Sans CJK KR',sans-serif; font-size: 11pt; }
 h1 { font-size: 20pt; text-align:center; } h2 { font-size: 14pt; margin-top: 18pt; }
</style></head><body>
<h1>2025년도 시스템 유지보수 계약서</h1>
<h2>제1조 (용역의 범위)</h2>
<p>을은 갑의 정보시스템에 대하여 월 1회 이상 정기 점검을 수행하고, 장애 발생 시
4시간 이내에 현장에 출동하여야 한다.</p>
<h2>제2조 (계약금액)</h2>
<p>연간 유지보수 금액은 삼천육백만원(￦36,000,000)으로 하며 매월 균등 분할한다.</p>
<h2>제3조 (대금지급 조건)</h2>
<p>대금은 매월 말일을 기준으로 산정하며, 을이 세금계산서를 발행한 날로부터
15일 이내에 지급한다.</p>
<h2>제4조 (하자보증)</h2>
<p>하자보증 기간은 검수일로부터 1년으로 한다.</p>
<h2>제5조 (계약의 해지)</h2>
<p>갑은 을이 정당한 사유 없이 2회 이상 정기 점검을 이행하지 아니한 경우
계약을 해지할 수 있다.</p>
</body></html>"""

def html_to(html: str, stem: str, target_ext: str, lo_filter: str) -> Path:
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / f"{stem}.html"
        src.write_text(html, encoding="utf-8")
        with tempfile.TemporaryDirectory() as profile:
            subprocess.run(
                ["soffice", f"-env:UserInstallation=file://{profile}",
                 "--headless", "--norestore", "--convert-to", lo_filter,
                 "--outdir", str(OUT), str(src)],
                capture_output=True, text=True, timeout=300, check=False)
        produced = OUT / f"{stem}{target_ext}"
        if not produced.exists():
            raise SystemExit(f"failed to produce {produced}")
        return produced

made = []
made.append(html_to(CONTRACT_HTML, "2026_공급계약서", ".pdf", "pdf:writer_pdf_Export"))
made.append(html_to(CONTRACT_HTML, "2026_공급계약서", ".docx", "docx:MS Word 2007 XML"))
made.append(html_to(MAINT_HTML, "2025_유지보수계약서", ".pdf", "pdf:writer_pdf_Export"))
made.append(html_to(MAINT_HTML, "2025_유지보수계약서", ".odt", "odt:writer8"))

# A scanned document: render a PDF to an image and wrap it back up, so it has
# no text layer and must go through OCR.
import pymupdf
with pymupdf.open(str(OUT / "2025_유지보수계약서.pdf")) as doc:
    scanned = pymupdf.open()
    for page in doc:
        pix = page.get_pixmap(dpi=200)
        p = scanned.new_page(width=page.rect.width, height=page.rect.height)
        p.insert_image(p.rect, stream=pix.tobytes("png"))
    dst = OUT / "스캔본_유지보수계약서.pdf"
    scanned.save(str(dst)); scanned.close()
    made.append(dst)

# A plain text file and an image.
txt = OUT / "회의메모.txt"
txt.write_text("2026-03-14 계약 검토 회의\n\n"
               "- 공급계약서 지급조건은 검수 후 30일로 확정한다.\n"
               "- 유지보수계약서 하자보증 1년은 2년으로 연장 요청한다.\n"
               "- 다음 회의는 3월 21일 오후 2시.\n", encoding="utf-8")
made.append(txt)

with pymupdf.open(str(OUT / "2026_공급계약서.pdf")) as doc:
    pix = doc[0].get_pixmap(dpi=150)
    img = OUT / "계약서_1페이지.png"
    pix.save(str(img)); made.append(img)

for p in made:
    print(f"  {p.name:<34} {p.stat().st_size:>9,} bytes")
