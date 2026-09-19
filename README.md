# 📖 Anuvad — English → Hindi PDF Book Translator

Upload an English PDF book, translate it into natural Hindi **chapter by chapter** with
Google Gemini, and download clean Hindi PDFs. Built with Streamlit.

If one API key hits its limit, the app switches to your next key, or pauses and lets you
paste a new one. **Nothing already translated is ever lost.**

---

## ✨ Features

- **Chapter-wise translation.** Chapters are detected from the PDF's bookmarks, then from
  "Chapter N" headings, and finally by splitting into 20-page parts.
- **Resumable.** Every finished page is saved to `.cache/`. Close the app, come back
  tomorrow, upload the same book, and click **Continue**.
- **Multi-key support.** Paste several Gemini API keys (one per line). When a key hits its
  limit the app moves to the next one, and if all are used up it pauses instead of failing.
- **Rate-limit friendly.** Automatic retries with waiting, plus an adjustable delay
  between pages for free-tier requests-per-minute limits.
- **Correct Hindi rendering.** Uses a bundled Noto Sans Devanagari font and an
  **fpdf2 + HarfBuzz** engine, so conjuncts (क्ष, ज्ञ, श्र) and the short **ि** matra come
  out right.
- **Flexible output.** A separate PDF for each finished chapter, plus one combined PDF.
- **Context-aware.** The end of the previous page is passed along so names and terms stay
  consistent across pages.

---

## 🚀 Quick start (Windows / Mac / Linux)

### 1. Requirements

- **Python 3.10 or newer** (3.12 or 3.13 recommended). Check with `python --version`.
  Older versions such as 3.6 will fail with `No matching distribution found for anthropic`
  or similar errors.
- A free **Gemini API key**: https://aistudio.google.com/apikey

### 2. Install

Open a terminal **inside the project folder** (the one that contains `app.py`):

```bash
python -m venv venv

# Windows (PowerShell)
venv\Scripts\activate
# Mac / Linux
source venv/bin/activate

pip install -r requirements.txt
```

### 3. Run

```bash
streamlit run app.py
```

The app opens at http://localhost:8501.

---

## 🧭 How to use

1. Paste your Gemini API key in the sidebar. For backups, add one key per line.
2. Upload your English PDF. The detected chapters appear as a list.
3. **Test first.** Pick one short chapter and click **Translate**. Read the Hindi output.
4. Select more chapters and click **Translate** (or **Continue** after a pause).
5. Download each chapter PDF as soon as it finishes. When done, click
   **Build combined PDF of all finished chapters**.

### Sidebar settings

| Setting | What it does |
|---|---|
| API keys | One Gemini key per line. Failed keys are skipped automatically. |
| Gemini model | Default `gemini-2.5-flash`. Change it if you get a "model not found" error. |
| Delay between pages | Seconds to wait between pages. Raise it if you hit rate limits. |
| PDF engine | **fpdf2 + HarfBuzz** (recommended). ReportLab can misplace the ि matra. |
| Font size | Font size of the generated PDF. |
| Reset exhausted-key list | Makes the app try previously failed keys again. |

---

## 📚 Translating a big book (e.g. 300 pages)

- Use a **text-based PDF.** If you cannot select text with the mouse, it is a scan and this
  app has no OCR, so those pages will come out empty.
- Run it **on your own computer** rather than a free cloud host. Progress is kept in `.cache/`,
  and the memory is not limited.
- Keep the laptop **awake** (no sleep) and the browser tab **open** while it runs.
- Do **3–5 chapters per session**, and download each chapter PDF right away.
- Free-tier Gemini has daily limits. When they run out, add another key or click
  **Continue** the next day. It resumes from the exact page where it stopped.
- Proofread the result. Machine translation can still make mistakes.

---

## 🛠️ Troubleshooting

| Problem | Fix |
|---|---|
| `Could not open requirements file` | Your terminal is in the wrong folder. Use `cd` to go to the folder containing `requirements.txt`, and check with `dir` (Windows) or `ls`. |
| `No matching distribution found` | Python is too old. Install Python 3.10+ and recreate the venv. |
| `python --version` shows an old version | Create the venv with the full path of the new Python, e.g. `& "$env:LocalAppData\Programs\Python\Python313\python.exe" -m venv venv` |
| Paused, "all API keys exhausted" | Read the red error under it. Add a new key, click **Reset exhausted-key list**, then **Continue**. |
| "invalid API key" | Re-copy the key. Make sure there are no extra spaces. |
| "rate/quota limit reached" | Free-tier limit. Raise the delay, wait, add another key, or continue tomorrow. |
| "model not found" | Type a currently available Gemini model name in the sidebar. |
| ि matra in the wrong place, or ▯ boxes | Switch the PDF engine to **fpdf2 + HarfBuzz**. |
| Blank pages in the output | The source page is an image (scanned). OCR is not supported. |

---

## 🌐 Deploying online

**Streamlit Community Cloud** (free)

1. Push this folder to a GitHub repo (include the `fonts/` folder).
2. Go to https://share.streamlit.io → **Create app** → choose repo, branch `main`, file `app.py`.
3. Paste your API keys in the sidebar each session. **Never commit keys to the repo.**

Things to know: on the cloud, `.cache/` is wiped when the app restarts or sleeps, so
download each chapter as soon as it finishes. Free hosting also has limited memory, so
very large PDFs are better run locally. Netlify cannot host Streamlit apps.
**Hugging Face Spaces** (Streamlit SDK) is another free option with more RAM.

---

## 🗂️ Project structure

```
.
├── app.py                 # the whole app
├── requirements.txt
├── fonts/
│   └── NotoSansDevanagari-Regular.ttf   # static font (variable fonts break ReportLab)
├── .cache/                # auto-created: saved translation progress (git-ignored)
└── README.md
```

---

## ⚠️ Limitations

- Output is a **reflowed text translation**. Images, tables, columns and the original layout
  are not reproduced.
- Headers, footers and page numbers are treated as text and may appear in the output.
- No OCR for scanned books.
- Free-tier Gemini may use submitted text to improve Google's products. Use a paid tier
  for private or sensitive books.
- Only translate books you have the right to translate. Publishing a translation of
  someone else's copyrighted book needs the rights holder's permission.

---

## 🔒 Security

- Keys are entered in the sidebar and kept only in your browser session.
- Never put API keys in the code, in the repo, or in screenshots.
- If a key leaks, delete it in Google AI Studio and create a new one.
