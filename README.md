# Pustak-OCR

Local tool for turning scanned Devanagari books into clean text: upload a PDF, get a
page-by-page OCR draft, correct it in a browser with the scan and the text side by side,
export DOCX, EPUB, or TXT.

Everything runs on your machine. No page images or text leave it — except the pages you
explicitly send to Claude for suggestions, if you enable that.

**[Project page →](https://aashishbharti04.github.io/pustak-ocr/)**

> This is a localhost tool with **no authentication**. Don't deploy it to a public host:
> anyone who found the URL could upload to your disk and, if `ANTHROPIC_API_KEY` were set
> there, spend your Claude credits via the suggest endpoint.

## Install

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

### Tesseract (required, install it yourself)

The Python packages are installed; the OCR engine is a separate binary.

1. Install the Windows build from the UB Mannheim Tesseract project.
2. During install, tick **Additional language data -> Hindi** (`hin`). Without it OCR
   will refuse to start.
3. Either add the install dir to `PATH`, or point the app at it:

```powershell
$env:PUSTAK_TESSERACT_CMD = "C:\Program Files\Tesseract-OCR\tesseract.exe"
```

The home page tells you which of these is missing, and won't let a run fail silently.

## Run

```powershell
.\.venv\Scripts\python.exe -m uvicorn pustak_ocr.app:app --reload --port 8077
```

Open <http://localhost:8077>.

### Claude suggestions (optional)

The review UI can ask Claude to read the page scan and flag spans where the OCR
disagrees with what's printed. Needs credentials:

```powershell
$env:ANTHROPIC_API_KEY = "sk-ant-..."   # or run `ant auth login`
```

Without them everything else works; the Claude panel just shows what's missing.

## How it works

```
PDF -> PyMuPDF render @300 DPI -> denoise + deskew -> Tesseract(hin) -> SQLite
                                                                          |
                                            review UI (scan | editor) <---+
                                              |            ^              |
                                    page image + OCR text  |              |
                                              v            |              |
                                        Claude ---- span suggestions      |
                                        (you accept/reject each)          |
                                                                          |
                                       python-docx / ebooklib  <----------+
```

- **Ingest + OCR** run in a background thread per book. Progress polls every 3s.
- **Resumable**: pages are committed one at a time, and a re-run skips anything already
  OCR'd. If a 282-page job dies at page 200 you re-run and it does the last 82.
- **Raw text is never overwritten.** Corrections go in a separate column, so you can
  always diff what Tesseract got wrong.

## Review UI

| Key | Action |
|---|---|
| `Ctrl+S` | save |
| `Ctrl+Enter` | save, mark reviewed, next page |
| `Alt+←` / `Alt+→` | previous / next page |

Edits autosave 1s after you stop typing. The page grid on the book screen flags anything
below 80% confidence in amber — do those first.

Type a chapter title into "Chapter starts here" on the page where a chapter opens. DOCX
turns those into `Heading 1` with a page break; EPUB makes each one its own chapter
document with a TOC entry. Pages before the first mark become an untitled leading
section, so front matter is never silently dropped.

## The Claude layer, and why it works the way it does

This is the part worth understanding before you trust it.

**It suggests; it never writes.** Claude returns a list of spans — `original` →
`corrected`, with a reason and a confidence. Nothing changes until you click Apply.
Raw OCR lives in its own column and is never overwritten, so you can always see what
Tesseract actually produced.

That design is not politeness, it's the whole point. An LLM told to "fix OCR errors,
don't rewrite" will do exactly that ~95% of the time — and on the word it genuinely
can't recover, it will emit a *different plausible Hindi word that fits the sentence*.
Raw OCR errors look like garbage and your eye catches them; a fluent fabrication reads
like prose and your eye slides right over it. Left unsupervised, an LLM pass converts
visible errors into invisible ones. That is a worse book, not a better one.

Two mechanisms keep it honest:

1. **The model sees the scan.** The page image goes with the text on every request. A
   model that can't see the page can only guess fluently at a damaged matra; one that
   can is actually reading it. The prompt says every suggestion must be justified by
   what is visibly printed.
2. **Every span must be anchored.** `original` has to occur verbatim in the OCR text or
   the suggestion is discarded server-side before you ever see it (`ai.py:_anchored`). A
   span the model invented cannot be located, so it cannot be applied. The panel tells
   you how many were dropped this way — a number that is consistently above zero is a
   signal to distrust that page's suggestions, not to ignore the counter.

**Use it to route attention, not to correct text.** Reviewing eight flagged words takes
about thirty seconds; reading a whole page takes about four minutes. That is where the
time actually goes.

### Cost

Roughly **$0.03–0.08 per page** (Opus 4.8, one full-page image plus the OCR text, at
$5/$25 per Mtok). A 282-page book lands somewhere around **$10–25**. That's an estimate,
not a measurement — run ten pages and check your actual bill before committing to a
whole book. `PUSTAK_CLAUDE_EFFORT=medium` cuts it; `claude-sonnet-5` cuts it further at
some accuracy cost on hard glyphs.

## Configuration

| Env var | Default | Notes |
|---|---|---|
| `PUSTAK_DATA_DIR` | `./data` | images + SQLite live here |
| `PUSTAK_DPI` | `300` | below 300 Devanagari matras start dropping out |
| `PUSTAK_TESSERACT_CMD` | — | full path to `tesseract.exe` if not on PATH |
| `PUSTAK_TESSERACT_LANG` | `hin` | `hin+eng` for mixed-script books |
| `PUSTAK_TESSERACT_CONFIG` | `--psm 6` | psm 6 = uniform block, right for prose |
| `PUSTAK_LOW_CONF` | `80` | confidence below this is flagged |
| `ANTHROPIC_API_KEY` | — | needed only for the Claude panel |
| `PUSTAK_CLAUDE_MODEL` | `claude-opus-4-8` | `claude-sonnet-5` is cheaper, weaker on hard glyphs |
| `PUSTAK_CLAUDE_EFFORT` | `high` | `medium` trades some accuracy for cost |

## A note on preprocessing

The default path is **grayscale -> denoise -> deskew**, and binarization is off.

This is deliberate and differs from most OCR tutorials. Tesseract binarizes internally
(Otsu), and handing it a pre-binarized page usually makes Devanagari *worse* — adaptive
thresholding tends to eat the shirorekha and thin the matras, which is exactly the detail
you can't afford to lose. The "Force binarization" checkbox is there for genuinely bad
scans (heavy show-through, uneven lighting). A/B it on your own pages before trusting it.

Deskew uses a projection profile rather than Hough lines, which suits Devanagari well:
the shirorekha makes every text line a strong horizontal bar, so row variance peaks
sharply at the true angle. Measured on synthetic pages it recovers ±6° to within 0.01°.

## Disk

300 DPI grayscale PNG is roughly 3-5 MB/page, so a 282-page book runs ~1 GB in `data/`.
The source PDF is kept alongside so a re-run needs no re-upload.

## Known gaps

**Not verified end to end.** Two paths have never been run against the real thing,
because neither Tesseract nor Claude credentials were available on the machine this was
built on:

- **The Tesseract `hin` recognition call.** Everything around it is exercised — render,
  preprocess, database, review UI, export. The recognition step itself is unproven; your
  first real run is the first real test.
- **The live Claude request.** The anchoring guard, image encoding, error paths, and the
  Apply flow are all tested. The actual API call and the model's real suggestion quality
  on your scans are not. Run ten pages and read every suggestion critically before you
  start trusting the panel.

Other gaps:

- **Single OCR engine.** No PaddleOCR cross-check, no Cloud Vision. The seam is
  `pustak_ocr/ocr.py:run()` — same signature, returns text + confidence. Engine
  *disagreement* would be a strong, free attention signal; it isn't built.
- **One worker thread per book**, so OCR is single-core. Fine unattended; a pool would cut
  a 282-page run substantially.
- **Suggestions are per-page and on demand.** No batch pre-pass over the whole book.
- **No layout analysis.** Single-column prose only — no tables, figures, or footnotes.
- **No auth.** Bind to localhost only. Don't expose this to a network.
- Running the helper scripts in a Windows console may need `$env:PYTHONIOENCODING='utf-8'`
  or Devanagari `print()` hits a cp1252 error. The web app is unaffected.

## Rights

The tool doesn't care what you feed it, but you should. Digitizing a full book is
reproduction regardless of whether OCR or a keyboard does the typing. Use it on your own
manuscripts, public-domain texts, or works you have permission to copy.
