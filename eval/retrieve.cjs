// eval/retrieve.cjs — retrieval shim for the RAG eval harness.
//
// Reuses the REAL retriever: it requires the tsc-compiled copies of
//   webapp/lib/db.ts                -> searchDocuments (the FTS5/BM25 + direct
//                                      lookup ladder)
//   webapp/lib/knowledgeClassifier  -> classifyQuery
// No retrieval logic is reimplemented here.
//
// Must be invoked with:
//   cwd  = <repo>/webapp           so db.ts's default DB path
//                                  (process.cwd()/../db/knowledge.db) resolves
//                                  to the live DB (opened read-only by db.ts)
//   NODE_PATH = <repo>/webapp/node_modules   so better-sqlite3 resolves
//
// Usage:  node ../eval/retrieve.cjs <questions.jsonl>
// Output: JSON array on stdout: [{id, mode, results:[{doc_id,title,url,content,source}]}]

const fs = require("fs");
const { searchDocuments } = require("./_gen/db.js");
const { classifyQuery, sourcesForQuery } = require("./_gen/knowledgeClassifier.js");
// Source resolution (incl. bug-intent routing) is the SAME function the route
// uses — imported from the compiled lib, so eval and production never drift.

// Request top-10 so the harness can compute hit@5 / hit@10 / MRR.
// (Production /knowledge requests 5, or 8 for general mode.)
const TOP_K = 10;

async function main() {
  const qfile = process.argv[2];
  if (!qfile) {
    console.error("usage: node retrieve.cjs <questions.jsonl>");
    process.exit(2);
  }
  const lines = fs.readFileSync(qfile, "utf-8").split("\n").filter((l) => l.trim());
  const out = [];
  for (const line of lines) {
    const q = JSON.parse(line);
    const mode = classifyQuery(q.question);
    const sources = sourcesForQuery(q.question, mode);
    let results = await searchDocuments(q.question, TOP_K, sources);
    if (!results || results.length === 0) {
      // mirror the route's fallback (unfiltered), at TOP_K
      results = await searchDocuments(q.question, TOP_K);
    }
    out.push({
      id: q.id,
      mode,
      results: (results || []).map((r) => ({
        doc_id: r.doc_id,
        title: r.title,
        url: r.url,
        // cap content: the grounding prompt only uses the first 1000 chars
        content: (r.content || "").slice(0, 1500),
        source: r.source,
      })),
    });
  }
  // Exit only after stdout has fully flushed — process.exit() before the write
  // drains truncates large payloads.
  await new Promise((resolve) => process.stdout.write(JSON.stringify(out), resolve));
}

main()
  .then(() => { process.exitCode = 0; })
  .catch((e) => {
    console.error(e && e.stack ? e.stack : String(e));
    process.exitCode = 1;
  });
