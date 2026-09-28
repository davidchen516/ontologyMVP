"""PDF 解析与 EvidenceFragment 切分（issue #6）。

- 解析保留：页码、章节、段落、字符偏移、解析器版本、页数与文本统计；
- 质量门禁：空白页比例 / 最小字符数——纯扫描文档显式 PARSE_NEEDS_REVIEW，
  绝不标为正常解析，绝不进入自动 Claim 抽取；
- Prompt 注入红线：文档正文一律按数据处理。本模块没有任何执行、网络、
  工具调用代码路径；解析器只产出文本与位置。
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any

import pypdf

PARSER_NAME = "pypdf"
PARSER_VERSION = f"pypdf-{pypdf.__version__}"

# 一页视为"有效文本页"的最小字符数；低于阈值的页面计为空白页
MIN_CHARS_PER_TEXT_PAGE = 40
# 空白页比例超过该阈值 → 纯扫描疑似 → PARSE_NEEDS_REVIEW
MAX_BLANK_PAGE_RATIO = 0.6
# 全文档最小总字符数
MIN_TOTAL_CHARS = 120

_SECTION_RE = re.compile(
    r"^(第[一二三四五六七八九十百千0-9]+[章节部分]"
    r"|[Cc]hapter\s+\d+|[Ss]ection\s+\d+"
    r"|\d+(\.\d+)*\s*[、\.])"
)


@dataclass(frozen=True)
class ParsedPage:
    page_number: int
    text: str
    char_start: int
    char_end: int


@dataclass(frozen=True)
class ParsedDocument:
    pages: tuple[ParsedPage, ...]
    total_chars: int
    blank_page_count: int
    parser_version: str
    needs_review: bool
    quality: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class FragmentDraft:
    document_version_id: Any | None
    page_number: int
    section_title: str | None
    paragraph_index: int
    char_start: int
    char_end: int
    quote_text: str
    normalized_text: str
    checksum: str


class DocumentParseError(Exception):
    """解析失败：调用方必须转 FAILED_*，不得进入抽取队列。"""


def parse_pdf(data: bytes) -> ParsedDocument:
    """解析 PDF 字节 → 页文本 + 偏移 + 质量统计。数据只作数据。"""
    try:
        reader = pypdf.PdfReader(__import__("io").BytesIO(data))
    except Exception as exc:  # noqa: BLE001 - 损坏 PDF 必须被分类捕获
        raise DocumentParseError(f"unreadable pdf: {type(exc).__name__}: {exc}") from exc

    pages: list[ParsedPage] = []
    offset = 0
    blank = 0
    for index, page in enumerate(reader.pages):
        try:
            text = page.extract_text() or ""
        except Exception as exc:  # noqa: BLE001
            raise DocumentParseError(
                f"page {index + 1} extraction failed: {type(exc).__name__}"
            ) from exc
        stripped = text.strip()
        if len(stripped) < MIN_CHARS_PER_TEXT_PAGE:
            blank += 1
        page_start = offset
        pages.append(
            ParsedPage(
                page_number=index + 1,
                text=text,
                char_start=page_start,
                char_end=page_start + len(text),
            )
        )
        offset += len(text)

    total_chars = sum(len(p.text.strip()) for p in pages)
    page_count = len(pages)
    blank_ratio = (blank / page_count) if page_count else 1.0
    needs_review = bool(
        page_count == 0 or total_chars < MIN_TOTAL_CHARS
        or blank_ratio > MAX_BLANK_PAGE_RATIO
    )
    quality = {
        "page_count": page_count,
        "total_chars": total_chars,
        "blank_page_count": blank,
        "blank_page_ratio": round(blank_ratio, 3),
        "parser_version": PARSER_VERSION,
    }
    return ParsedDocument(
        pages=tuple(pages),
        total_chars=total_chars,
        blank_page_count=blank,
        parser_version=PARSER_VERSION,
        needs_review=needs_review,
        quality=quality,
    )


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def build_fragments(
    parsed: ParsedDocument, *, document_version_id: Any = None
) -> list[FragmentDraft]:
    """按段落切分（双换行/单换行均可），保留页码/章节/偏移；checksum 去重键。"""
    fragments: list[FragmentDraft] = []
    for page in parsed.pages:
        section_title: str | None = None
        paragraphs = [p for p in re.split(r"\n\s*\n|\n", page.text) if p.strip()]
        for paragraph_index, paragraph in enumerate(paragraphs):
            stripped = paragraph.strip()
            if _SECTION_RE.match(stripped):
                section_title = stripped[:80]
            if len(stripped) < 10:
                continue  # 无效短行不产生片段
            local_start = page.text.find(paragraph)
            char_start = page.char_start + max(local_start, 0)
            normalized = _normalize(stripped)
            checksum = hashlib.sha256(
                f"{page.page_number}|{char_start}|{normalized}".encode()
            ).hexdigest()
            fragments.append(
                FragmentDraft(
                    document_version_id=document_version_id,
                    page_number=page.page_number,
                    section_title=section_title,
                    paragraph_index=paragraph_index,
                    char_start=char_start,
                    char_end=char_start + len(paragraph),
                    quote_text=stripped,
                    normalized_text=normalized,
                    checksum=checksum,
                )
            )
    # 跨页去重：同 checksum 只保留一个
    seen: set[str] = set()
    unique: list[FragmentDraft] = []
    for fragment in fragments:
        if fragment.checksum in seen:
            continue
        seen.add(fragment.checksum)
        unique.append(fragment)
    return unique
