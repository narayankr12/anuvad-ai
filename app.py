"""
English -> Hindi book translator using Google Gemini (chapter-wise, resumable, multi-key).

Flow:
  1. Detect chapters (PDF bookmarks -> "Chapter N" headings -> fixed blocks)
  2. Translate page by page; every finished page is checkpointed (RAM + disk)
  3. If an API key hits rate limit / no credit -> switch to the next key.
     If no keys remain -> pause. Add a new key, click Continue, and it resumes.
  4. Each completed chapter gets its own Hindi PDF; a combined PDF is also built.
"""

import hashlib
import io
import json
import os
import re
import time
from pathlib import Path
from xml.sax.saxutils import escape

import fitz  # PyMuPDF
import httpx
import streamlit as st
from google import genai
from google.genai import errors as genai_errors
from google.genai import types

# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #
DEFAULT_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
FONT_PATH = Path(__file__).parent / "fonts" / "NotoSansDevanagari-Regular.ttf"
CACHE_DIR = Path(__file__).parent / ".cache"
CACHE_DIR.mkdir(exist_ok=True)

MAX_CHARS_PER_CHUNK = 3500
CONTEXT_CHARS = 600
RETRIES_BEFORE_SWITCH = 3      # rate-limit retries on one key before we give up on it
FALLBACK_PAGES_PER_PART = 20   # used only if no chapters can be detected

SYSTEM_PROMPT = """You are an expert English-to-Hindi literary translator.
Translate the given text into natural, fluent, modern Hindi (Devanagari script),
the way a skilled human translator would render a published book: preserve tone,
meaning, and style rather than translating word by word.

Rules:
- Output ONLY the Hindi translation. No preamble, notes, or explanations.
- Keep the same paragraph structure: paragraphs are separated by a blank line;
  output the same number of paragraphs in the same order.
- Transliterate personal names into Devanagari (keep them consistent).
- Keep numbers, and translate headings as headings.
- If a "PREVIOUS CONTEXT" block is provided, use it only to keep terminology,
  names and tone consistent. Do NOT translate or repeat it.
- If the text is a page number, blank, or meaningless fragment, return it unchanged."""


class KeyExhausted(Exception):
    """Raised when the current API key can't be used any more (limit / no credit / invalid)."""


# --------------------------------------------------------------------------- #
# Chapter detection
# --------------------------------------------------------------------------- #
CHAPTER_RE = re.compile(
    r"^\s*(chapter|part)\s+([0-9]+|[ivxlcdm]+|one|two|three|four|five|six|seven|eight|nine|ten)\b",
    re.I,
)


def detect_chapters(doc: fitz.Document) -> list[dict]:
    """Return [{'title', 'start', 'end'}] with 0-based inclusive page indexes."""
    total = len(doc)
    starts: list[tuple[str, int]] = []

    # 1) PDF bookmarks (top-level entries)
    toc = [t for t in doc.get_toc() if t[0] == 1 and 1 <= t[2] <= total]
    if len(toc) >= 2:
        starts = [(t[1].strip(), t[2] - 1) for t in toc]

    # 2) Look for "Chapter N" / "Part N" near the top of pages
    if not starts:
        for i in range(total):
            lines = [l.strip() for l in doc[i].get_text().splitlines() if l.strip()][:5]
            for line in lines:
                if CHAPTER_RE.match(line):
                    starts.append((line[:80], i))
                    break

    # 3) Fallback: fixed-size parts
    if len(starts) < 2:
        starts = [
            (f"Part {n + 1} (pages {s + 1}-{min(s + FALLBACK_PAGES_PER_PART, total)})", s)
            for n, s in enumerate(range(0, total, FALLBACK_PAGES_PER_PART))
        ]

    # Clean up: sort, dedupe by page, and cover front matter before the first chapter
    seen, cleaned = set(), []
    for title, p in sorted(starts, key=lambda x: x[1]):
        if p not in seen:
            seen.add(p)
            cleaned.append((title, p))
    if cleaned[0][1] > 0:
        cleaned.insert(0, ("Front matter", 0))

    chapters = []
    for n, (title, p) in enumerate(cleaned):
        end = cleaned[n + 1][1] - 1 if n + 1 < len(cleaned) else total - 1
        chapters.append({"title": title, "start": p, "end": end})
    return chapters


# --------------------------------------------------------------------------- #
# Text extraction
# --------------------------------------------------------------------------- #
def extract_page_paragraphs(page: fitz.Page) -> list[str]:
    blocks = [b for b in page.get_text("blocks") if b[6] == 0]
    blocks.sort(key=lambda b: (round(b[1]), b[0]))
    paragraphs = []
    for b in blocks:
        text = re.sub(r"-\n(?=[a-z])", "", b[4])
        text = re.sub(r"\s*\n\s*", " ", text)
        text = re.sub(r"\s{2,}", " ", text).strip()
        if text:
            paragraphs.append(text)
    return paragraphs


# --------------------------------------------------------------------------- #
# Translation
# --------------------------------------------------------------------------- #
def chunk_paragraphs(paragraphs: list[str], max_chars: int = MAX_CHARS_PER_CHUNK) -> list[list[str]]:
    pieces = []
    for p in paragraphs:
        if len(p) <= max_chars:
            pieces.append(p)
            continue
        buf = ""
        for s in re.split(r"(?<=[.!?])\s+", p):
            if buf and len(buf) + len(s) + 1 > max_chars:
                pieces.append(buf)
                buf = s
            else:
                buf = f"{buf} {s}".strip()
        if buf:
            pieces.append(buf)

    chunks, current, size = [], [], 0
    for p in pieces:
        if current and size + len(p) > max_chars:
            chunks.append(current)
            current, size = [], 0
        current.append(p)
        size += len(p)
    if current:
        chunks.append(current)
    return chunks


def translate_chunk(client, model, paragraphs, context) -> list[str]:
    """Translate one chunk with Gemini. Raises KeyExhausted if this key is unusable."""
    content = ""
    if context:
        content += f"PREVIOUS CONTEXT (do not translate):\n{context}\n\n---\n\n"
    content += "TEXT TO TRANSLATE:\n" + "\n\n".join(paragraphs)

    cfg = types.GenerateContentConfig(
        system_instruction=SYSTEM_PROMPT,
        temperature=0.3,
        max_output_tokens=16384,   # Hindi uses many tokens; leave headroom
    )

    for attempt in range(RETRIES_BEFORE_SWITCH):
        last = attempt == RETRIES_BEFORE_SWITCH - 1
        try:
            resp = client.models.generate_content(model=model, contents=content, config=cfg)
            text = (resp.text or "").strip()
            if not text:                      # blocked / empty answer -> retry, then keep original
                if last:
                    return paragraphs
                time.sleep(3)
                continue
            return [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()] or paragraphs

        except genai_errors.APIError as e:
            code = getattr(e, "code", None)
            msg = str(e).lower()
            if code == 429:                   # per-minute or daily free-tier limit
                if last:
                    raise KeyExhausted("rate/quota limit reached")
                time.sleep(min(20 * (attempt + 1), 65))
            elif code in (401, 403) or (code == 400 and "api key" in msg):
                raise KeyExhausted("invalid API key or no permission")
            elif code is not None and code >= 500:
                if last:
                    raise
                time.sleep(5 * (attempt + 1))
            else:
                raise                         # e.g. 404 wrong model name: show the real error

        except httpx.TransportError:
            if last:
                raise
            time.sleep(5 * (attempt + 1))
    return paragraphs


def translate_page(client, model, paragraphs, prev_tail) -> list[str]:
    result, context = [], prev_tail
    for chunk in chunk_paragraphs(paragraphs):
        result.extend(translate_chunk(client, model, chunk, context))
        context = " ".join(chunk)[-CONTEXT_CHARS:]
    return result


# --------------------------------------------------------------------------- #
# Text cleanup: replace/remove characters the font has no glyph for (they show as boxes)
# --------------------------------------------------------------------------- #
_FONT_CMAP = None
_CHAR_FIXES = {"\u2011": "-", "\u2212": "-", "\u00ad": "", "\ufeff": "", "\u2028": " ", "\u2029": " "}


def clean_text(text: str, font_path: Path) -> str:
    global _FONT_CMAP
    if _FONT_CMAP is None:
        from fontTools.ttLib import TTFont as _TT
        _FONT_CMAP = _TT(str(font_path)).getBestCmap()
    out = []
    for ch in text:
        ch = _CHAR_FIXES.get(ch, ch)
        for c in ch:
            if c in "\n\t " or ord(c) in _FONT_CMAP:
                out.append(c)
    return "".join(out)


# --------------------------------------------------------------------------- #
# PDF generation (ReportLab or fpdf2+HarfBuzz)
# `groups` = list of sections; each section is a flat list of paragraphs and
# starts on a new PDF page. Text flows across pages automatically.
# --------------------------------------------------------------------------- #
def build_pdf_reportlab(groups: list[list[str]], font_path: Path, font_size: int) -> bytes:
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer

    if "HindiFont" not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont("HindiFont", str(font_path)))

    style = ParagraphStyle(
        "Hindi", fontName="HindiFont", fontSize=font_size, leading=font_size * 1.6,
        alignment=TA_LEFT, spaceAfter=font_size * 0.6, wordWrap="LTR",
    )
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=20 * mm, rightMargin=20 * mm,
                            topMargin=20 * mm, bottomMargin=20 * mm, title="Hindi Translation")
    story = []
    for i, paragraphs in enumerate(groups):
        story += [Paragraph(escape(clean_text(p, font_path)), style) for p in paragraphs]
        if i < len(groups) - 1:
            story.append(PageBreak())
    doc.build(story or [Spacer(1, 1)])
    return buf.getvalue()


def build_pdf_fpdf2(groups: list[list[str]], font_path: Path, font_size: int) -> bytes:
    from fpdf import FPDF

    pdf = FPDF(format="A4")
    pdf.set_margins(20, 20, 20)
    pdf.set_auto_page_break(True, margin=20)
    pdf.add_font("HindiFont", "", str(font_path))
    pdf.set_text_shaping(True)  # HarfBuzz: correct conjuncts + matras
    for paragraphs in groups:
        pdf.add_page()
        pdf.set_font("HindiFont", size=font_size)
        for p in paragraphs:
            pdf.multi_cell(0, font_size * 0.6, clean_text(p, font_path), new_x="LMARGIN", new_y="NEXT")
            pdf.ln(2)
    return bytes(pdf.output())


# --------------------------------------------------------------------------- #
# Checkpointing (survives browser refresh / next-day resume)
# --------------------------------------------------------------------------- #
def checkpoint_path(book_id: str) -> Path:
    return CACHE_DIR / f"{book_id}.json"


def load_checkpoint(book_id: str) -> dict[int, list[str]]:
    p = checkpoint_path(book_id)
    if p.exists():
        return {int(k): v for k, v in json.loads(p.read_text(encoding="utf-8")).items()}
    return {}


def save_checkpoint(book_id: str, cache: dict[int, list[str]]) -> None:
    checkpoint_path(book_id).write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def chapter_done(ch: dict, cache: dict) -> bool:
    return all(p in cache for p in range(ch["start"], ch["end"] + 1))


def chapter_paragraphs(ch: dict, cache: dict) -> list[str]:
    return [para for p in range(ch["start"], ch["end"] + 1) for para in cache[p]]


def parse_keys(raw: str) -> list[str]:
    keys = [k.strip() for k in raw.splitlines() if k.strip()]
    env = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if env and env not in keys:
        keys.append(env)
    return keys


# --------------------------------------------------------------------------- #
# Streamlit UI
# --------------------------------------------------------------------------- #
def main():
    st.set_page_config(page_title="English → Hindi Book Translator", page_icon="📖")
    st.title("📖 English → Hindi Book Translator")
    st.caption("Powered by Google Gemini. Chapter-wise translation. If an API key hits its limit, add another and continue.")

    ss = st.session_state
    ss.setdefault("exhausted", set())      # keys that already failed
    ss.setdefault("chapter_pdfs", {})      # chapter index -> pdf bytes

    with st.sidebar:
        st.header("Settings")
        keys_raw = st.text_area(
            "API keys (one per line)", height=120,
            help="Get a free key at aistudio.google.com/apikey. Add a backup key now, or paste a new one later.",
        )
        model = st.text_input("Gemini model", DEFAULT_MODEL,
                              help="If you get a model-not-found error, type another Gemini model name here.")
        delay = st.slider("Delay between pages (seconds)", 0, 15, 5,
                          help="Free tier has a requests-per-minute limit. A delay avoids hitting it.")
        backend = st.radio("PDF engine", ["fpdf2 + HarfBuzz (recommended for Hindi)", "ReportLab (may misplace ि matra)"])
        font_size = st.slider("Font size", 9, 16, 12)
        if st.button("Reset exhausted-key list"):
            ss["exhausted"] = set()

    keys = parse_keys(keys_raw)
    if not FONT_PATH.exists():
        st.error(f"Font not found at `{FONT_PATH}`. Download NotoSansDevanagari-Regular.ttf.")
        st.stop()

    uploaded = st.file_uploader("Upload an English PDF", type=["pdf"])
    if not uploaded:
        return

    data = uploaded.getvalue()
    book_id = hashlib.md5(data).hexdigest()[:16]
    doc = fitz.open(stream=data, filetype="pdf")

    # New book -> load its saved progress (if any) and detect chapters
    if ss.get("book_id") != book_id:
        ss["book_id"] = book_id
        ss["cache"] = load_checkpoint(book_id)
        ss["chapters"] = detect_chapters(doc)
        ss["chapter_pdfs"] = {}
    cache: dict[int, list[str]] = ss["cache"]
    chapters: list[dict] = ss["chapters"]

    # ---- Chapter table + selection ----
    st.subheader(f"{len(chapters)} chapters detected · {len(doc)} pages")
    labels = [
        f"{'✅' if chapter_done(c, cache) else '⬜'} {i + 1}. {c['title']}  (pages {c['start'] + 1}–{c['end'] + 1})"
        for i, c in enumerate(chapters)
    ]
    chosen = st.multiselect("Chapters to translate", range(len(chapters)),
                            default=[i for i, c in enumerate(chapters) if not chapter_done(c, cache)],
                            format_func=lambda i: labels[i])

    pages_todo = [p for i in chosen for p in range(chapters[i]["start"], chapters[i]["end"] + 1) if p not in cache]
    pages_total = sum(chapters[i]["end"] - chapters[i]["start"] + 1 for i in chosen)
    st.write(f"Pages remaining in selection: **{len(pages_todo)}** "
             f"(already translated: {pages_total - len(pages_todo)})")

    label = "Continue" if cache else "Translate"
    if st.button(label, type="primary", disabled=not chosen or not pages_todo):
        progress = st.progress(0.0, text="Starting…")
        status = st.empty()
        client, current_key, paused = None, None, False

        for ci in chosen:
            ch = chapters[ci]
            for p in range(ch["start"], ch["end"] + 1):
                if p in cache:
                    continue

                done = pages_total - len([x for x in pages_todo if x not in cache])
                progress.progress(done / max(pages_total, 1),
                                  text=f"Chapter {ci + 1}: page {p + 1} ({done}/{pages_total})")

                paragraphs = extract_page_paragraphs(doc[p])
                prev_tail = " ".join(extract_page_paragraphs(doc[p - 1]))[-CONTEXT_CHARS:] if p > 0 else ""

                # Retry this same page with the next key whenever a key is exhausted
                while True:
                    if current_key is None:
                        usable = [k for k in keys if k not in ss["exhausted"]]
                        if not usable:
                            paused = True
                            break
                        current_key = usable[0]
                        client = genai.Client(api_key=current_key)
                        status.info(f"Using key …{current_key[-4:]}")
                    try:
                        cache[p] = translate_page(client, model, paragraphs, prev_tail) if paragraphs else []
                        save_checkpoint(book_id, cache)   # progress is safe from here
                        if paragraphs and delay:
                            time.sleep(delay)             # stay under free-tier requests/minute
                        break
                    except KeyExhausted as e:
                        ss["exhausted"].add(current_key)
                        ss["last_reason"] = f"Key …{current_key[-4:]} failed: {e}"
                        st.toast(f"Key …{current_key[-4:]} stopped: {e}", icon="⚠️")
                        current_key = None
                    except Exception as e:
                        st.error(f"Failed on page {p + 1}: {e}")
                        paused = True
                        break
                if paused:
                    break

            # Build this chapter's PDF the moment it's complete
            if chapter_done(ch, cache) and (ci, backend, font_size) not in ss["chapter_pdfs"]:
                builder = build_pdf_reportlab if backend.startswith("ReportLab") else build_pdf_fpdf2
                ss["chapter_pdfs"][(ci, backend, font_size)] = builder([chapter_paragraphs(ch, cache)], FONT_PATH, font_size)
            if paused:
                break

        if paused:
            progress.empty()
            st.warning("⏸ Paused: all API keys are exhausted (or an error occurred). "
                       "Your progress is saved. Add a new key in the sidebar, then click **Continue**.")
            if ss.get("last_reason"):
                st.error(ss["last_reason"])
        else:
            progress.progress(1.0, text="Done ✅")
            st.rerun()

    # ---- Downloads ----
    finished = [i for i, c in enumerate(chapters) if chapter_done(c, cache)]
    if finished:
        st.subheader("Downloads")
        builder = build_pdf_reportlab if backend.startswith("ReportLab") else build_pdf_fpdf2

        if st.button("Build combined PDF of all finished chapters"):
            groups = [chapter_paragraphs(chapters[i], cache) for i in finished]
            ss["combined_pdf"] = builder(groups, FONT_PATH, font_size)
            ss["combined_key"] = (backend, font_size)
        if "combined_pdf" in ss and ss.get("combined_key") == (backend, font_size):
            st.download_button("⬇️ Combined Hindi PDF", ss["combined_pdf"],
                               file_name=Path(uploaded.name).stem + "_hindi.pdf", mime="application/pdf")

        for i in finished:
            if (i, backend, font_size) not in ss["chapter_pdfs"]:
                ss["chapter_pdfs"][(i, backend, font_size)] = builder([chapter_paragraphs(chapters[i], cache)], FONT_PATH, font_size)
            st.download_button(f"⬇️ Chapter {i + 1}: {chapters[i]['title'][:40]}",
                               ss["chapter_pdfs"][(i, backend, font_size)], file_name=f"chapter_{i + 1}_hindi.pdf",
                               mime="application/pdf", key=f"dl_{i}")


if __name__ == "__main__":
    main()
