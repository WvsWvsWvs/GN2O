# GN2O

GN2O (GoodNotes to Obsidian) converts handwritten GoodNotes PDFs into organized Obsidian notes, reviews them against a learning goal, and connects study insights with Anki.

## What it does

```text
GoodNotes PDF
    ↓
Page transcription
    ↓
Obsidian Markdown
    ↓
Goal-aware review
    ↓
Insights Hub
    ↓
Anki analysis and Cloze-card proposals
```

GN2O supports:

- Google Drive PDF downloads
- Local PDF processing
- Handwriting transcription through a Gemini-compatible API
- Markdown formatting and diagram rendering
- Page-level transcription caching
- Goal-aware academic reviews
- Anki deck and subdeck synchronization
- Bayesian mastery estimates and forecast graphs
- LLM-generated Cloze-card proposals
- Obsidian approval and Anki synchronization

## Requirements

- Python 3.10+
- An Obsidian vault
- Anki Desktop with the AnkiConnect add-on for Anki features
- A Gemini-compatible API key

Install dependencies:

```bash
python3 -m pip install -r requirements.txt
```

## Configuration

Copy the example configuration:

```bash
cp .env.example .env
```

Set at least:

```env
OPENAI_API_KEY=your-api-key
OPENAI_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai/
MODEL_NAME=gemini-3.1-flash-lite
GOOGLE_DRIVE_FOLDER_ID=your-folder-id
OBSIDIAN_VAULT_PATH=/absolute/path/to/your/vault
NOTES_DIR=./Notes
```

The project uses the OpenAI-compatible API interface. The default model is chosen for low-cost multimodal processing; change it only when a task requires a stronger model.

## PDF processing

Put a GoodNotes PDF in `Notes/` and run the full pipeline:

```bash
python3 main.py --file "Linear Algebra.pdf"
```

The note is written to:

```text
<Obsidian vault>/GN2O/Linear Algebra.md
```

Process all local and Google Drive PDFs once:

```bash
python3 main.py --once
```

Run continuous local-folder watching and Google Drive polling:

```bash
python3 main.py
```

## Caching and cost control

The normal pipeline hashes rendered pages and reuses unchanged transcriptions. New or modified pages are sent in batches of up to five. A review is generated only when pages changed.

Cache and tracking files are local and ignored by Git:

```text
Notes/.page_cache.json
Notes/.format_processed.json
Notes/.drive_processed.json
```

Preview work without API calls:

```bash
python3 main.py --dry-run --file "Linear Algebra.pdf"
python3 main.py --status
python3 main.py --check-setup
```

## Formatting existing notes

Format existing top-level GN2O notes once:

```bash
python3 main.py --format-existing
```

Existing files are backed up before replacement. Backups are retained according to `BACKUP_RETENTION`.

## Insights Hubs

An Insights Hub stores a subject’s goal, success criteria, Anki configuration, review summaries, mastery estimates, forecast, and proposed cards.

Create or synchronize an Anki-only hub:

```bash
python3 main.py \
  --sync-anki \
  --anki-only \
  --subject "MCAT" \
  --deck "Focused MCAT"
```

Synchronize a parent deck and every subdeck into nested hubs:

```bash
python3 main.py --sync-anki-tree --deck "Focused MCAT"
```

Anki subdecks such as `Focused MCAT::Chemistry::Acids and Bases` become nested hubs under:

```text
GN2O/Focused MCAT/Chemistry/Acids and Bases/Hub.md
```

## Anki card proposals

Generate unchecked Cloze proposals from a subject’s notes, goal, and latest review:

```bash
python3 main.py \
  --generate-anki-proposals \
  --subject "Linear Algebra"
```

Review the proposals in the hub and change approved cards to:

```markdown
- [x] Approve
```

Preview approved cards:

```bash
python3 main.py --sync-approved-cards --subject "Linear Algebra"
```

Create them in Anki only with explicit confirmation:

```bash
python3 main.py \
  --sync-approved-cards \
  --subject "Linear Algebra" \
  --confirm
```

Anki Desktop must be open with AnkiConnect running.

## Obsidian plugin

The desktop-only GN2O Sync plugin is located in `obsidian-plugin/gn2o-sync/`. Install it into:

```text
<vault>/.obsidian/plugins/gn2o-sync/
```

Enable it in Obsidian, configure the absolute GN2O project path, and open a subject `Hub.md`. The plugin provides commands for generating proposals, previewing approved cards, and syncing approved cards.

## Security and privacy

Never commit `.env`, `credentials.json`, `token.pickle`, PDFs, personal notes, caches, or Obsidian vault data. These are excluded by `.gitignore`. API calls may contain the page images or text needed for transcription and review; use an API provider and model appropriate for your privacy requirements.

## License

GN2O is licensed under the [GNU General Public License v3.0 or later](https://www.gnu.org/licenses/gpl-3.0.html).
