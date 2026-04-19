# F5 Assistant — Admin Reference

## Starting the Application

```bash
cd /home/jsonbourne/projects/Claude/F5
bash start.sh
```

| Service | URL | Log |
|---------|-----|-----|
| Frontend (Next.js) | http://localhost:3000 | `logs/frontend.log` |
| Backend (QKView API) | http://localhost:8000 | `logs/backend.log` |
| Ollama (LLM) | http://localhost:11434 | `logs/ollama.log` |

---

## Changing the LLM Model

The Knowledge Base and iRules Chat both use Ollama. The default model is `llama3.2:latest`.

**To change the model:**

1. Pull the new model:
   ```bash
   ollama pull <model-name>
   ```
2. Set the environment variable before starting, or add it to a `.env.local` file in `webapp/`:
   ```bash
   OLLAMA_MODEL=mistral:7b
   OLLAMA_URL=http://127.0.0.1:11434
   ```
3. Restart the frontend (`npm run dev` in `webapp/`).

Good local models for F5 Q&A (in order of preference for this hardware):
- `llama3.2:latest` — default, fast on 8 GB VRAM
- `mistral:7b` — strong instruction following
- `llama3.1:8b` — more capable, tighter on VRAM

---

## Knowledge Base — How It Works

1. User submits a question.
2. `lib/knowledgeClassifier.ts` classifies it as `f5`, `rfc`, or `general`.
3. `lib/db.ts` searches `db/knowledge.db` using SQLite FTS5:
   - Direct K-article lookup if a K-number (e.g. `K14783`) is in the query.
   - Direct CVE lookup if a CVE ID is in the query.
   - Direct iRules command lookup if `Namespace::command` syntax is detected.
   - Full-text search on remaining slots, ranked by BM25 (title weighted 10×, keywords 5×, content 1×).
4. Retrieved documents are injected as context into the LLM prompt.
5. The LLM answers strictly from that context — no hallucination.
6. Sources are returned alongside the answer.

**To force the LLM to use a specific K-article**, include the K-number in your question:
> "According to K14783, what settings are in the Client SSL profile?"

---

## Knowledge Database (`db/knowledge.db`)

**Engine:** SQLite with FTS5 full-text search  
**Size:** ~46,000 documents

| Column | Description |
|--------|-------------|
| `source` | Document origin: `f5_kb`, `f5_security`, `irules`, `rfc`, `clouddocs`, `xc_techdocs` |
| `doc_id` | Unique ID, e.g. `K14783`, `rfc7231` |
| `title` | Document title |
| `url` | Canonical URL |
| `section` | Sub-section within the document |
| `keywords` | Comma-separated tags ingested with the document |
| `content` | Full text content |
| `last_fetched` | When the document was last scraped |

**Source row counts (approximate):**

| Source | Rows | What it contains |
|--------|------|-----------------|
| `f5_kb` | 29,446 | F5 Support K-articles |
| `rfc` | 9,657 | IETF RFC standards |
| `f5_security` | 4,711 | F5 security advisories |
| `irules` | 1,574 | iRules Commands, Events, Operators |
| `xc_techdocs` | 717 | F5 Distributed Cloud / XC docs |
| `clouddocs` | 116 | F5 CloudDocs product guides |

**To inspect the database directly:**
```bash
python3 -c "
import sqlite3
db = sqlite3.connect('db/knowledge.db')
c = db.cursor()
c.execute(\"SELECT source, COUNT(*) FROM documents GROUP BY source\")
print(c.fetchall())
"
```

**To check if a specific K-article is indexed:**
```bash
python3 -c "
import sqlite3
db = sqlite3.connect('db/knowledge.db')
c = db.cursor()
c.execute(\"SELECT doc_id, title, length(content) FROM documents WHERE doc_id='K14783'\")
print(c.fetchone())
"
```

---

## Look and Feel

All UI is in `webapp/app/`. The app uses **Tailwind CSS** with a dark/light theme toggle.

**Color scheme per feature:**

| Feature | Accent Color | Tailwind class |
|---------|-------------|----------------|
| QKView Analyzer | Violet | `violet-500` |
| Knowledge Base | Amber | `amber-500` |
| iRule Index | Emerald | `emerald-500` |
| iRule Generator | Amber | `amber-500` |
| iRule Validator | Blue | `blue-500` |

**To change the home page tile descriptions:** edit `webapp/app/page.tsx`.  
**To change the nav bar links:** edit `webapp/app/layout.tsx`.  
**To change the Knowledge Base welcome message or placeholder text:** edit `webapp/app/knowledge/page.tsx` (look for the initial `messages` state and the `placeholder` prop on the textarea).

---

## Environment Variables (`webapp/.env.local`)

Create this file if it does not exist. None are required — defaults work for local use.

```bash
OLLAMA_URL=http://127.0.0.1:11434     # Ollama server address
OLLAMA_MODEL=llama3.2:latest           # Model to use for all chat features
KSI_DB_PATH=                           # Override path to knowledge.db (leave blank for default)
```

---

## QKView Analyzer Backend

- Source: `backend/main.py`
- Python venv: `.venv/` (at repo root)
- Accepts `.tar.gz` QKView uploads up to 1 GB via `POST /analyze`
- Parses: `bigip.conf`, `bigip_base.conf`, BIG-IP logs, tmstat data
- Runs rule engine (`rule_engine.py`) for known issue detection

**To restart just the backend:**
```bash
source .venv/bin/activate
cd backend
uvicorn main:app --host 127.0.0.1 --port 8000
```

---

## Known Limitations

- **No product version filtering**: the DB does not have a `product_version` column, so you cannot restrict answers to "BIG-IP 17.1+ only". All K-articles regardless of version are in scope.
- **Retrieval is keyword-based (FTS), not semantic**: a question phrased very differently from how a document is written may miss relevant results. Including K-numbers or exact F5 terms (e.g. "Client SSL profile", "TMSH", "iRule") improves results.
- **LLM runs locally via Ollama**: response quality depends on the model pulled. Larger models (7B+) give better answers but use more VRAM.
