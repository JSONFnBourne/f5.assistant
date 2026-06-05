import Database from 'better-sqlite3';
import path from 'path';

// Canonical path to the unified knowledge database.
// If KSI_DB_PATH is set, validate it stays within the expected db/ directory.
const _defaultDbPath = path.join(process.cwd(), '..', 'db', 'knowledge.db');
const _rawEnvPath = process.env.KSI_DB_PATH;

function resolveDbPath(): string {
  if (!_rawEnvPath) return _defaultDbPath;
  const resolved = path.resolve(_rawEnvPath);
  const expectedBase = path.resolve(path.join(process.cwd(), '..', 'db'));
  // Use path.relative to avoid separator edge cases
  const rel = path.relative(expectedBase, resolved);
  if (rel.startsWith('..') || path.isAbsolute(rel)) {
    throw new Error(`KSI_DB_PATH "${resolved}" is outside the permitted db/ directory.`);
  }
  return resolved;
}

const DB_PATH = resolveDbPath();

const db = new Database(DB_PATH, { readonly: true, fileMustExist: true });

// Prepared-statement cache keyed by SQL string. better-sqlite3 statements are
// reusable and compiled once; reusing them avoids re-parsing on every query.
const stmtCache = new Map<string, Database.Statement>();
function prep(sql: string): Database.Statement {
  let stmt = stmtCache.get(sql);
  if (!stmt) {
    stmt = db.prepare(sql);
    stmtCache.set(sql, stmt);
  }
  return stmt;
}

export interface SearchResult {
  id: number;
  source: string;
  doc_id: string;
  title: string;
  url: string;
  section: string;
  content: string;
  snippet: string;
}

// Common English stop words that carry no retrieval signal.
const STOP_WORDS = new Set([
  'a', 'an', 'the', 'is', 'it', 'its', 'be', 'are', 'was', 'were', 'been',
  'do', 'does', 'did', 'have', 'has', 'had', 'will', 'would', 'could', 'should',
  'may', 'might', 'shall', 'can', 'need',
  'i', 'me', 'my', 'we', 'our', 'you', 'your', 'he', 'she', 'they', 'them',
  'this', 'that', 'these', 'those', 'what', 'which', 'who', 'whom', 'whose',
  'and', 'but', 'or', 'nor', 'for', 'so', 'yet', 'of', 'in', 'on', 'at',
  'to', 'from', 'by', 'with', 'as', 'into', 'through',
  'any', 'all', 'some', 'no', 'not', 'only', 'than', 'too', 'very',
  'just', 'also', 'both', 'each', 'how', 'when', 'where', 'why',
  'tell', 'give', 'know', 'show', 'find', 'get', 'let', 'make', 'see',
  'please', 'explain', 'describe', 'about', 'regarding', 'related',
  'reference', 'information', 'provide', 'detail', 'details', 'more',
  'there', 'here', 'then', 'now', 'up', 'out', 'if', 'use', 'used',
  // Verbs that are common phrasing but not retrieval signals
  'using', 'uses', 'work', 'works', 'working',
  'example', 'examples', 'sample', 'syntax', 'governs', 'govern',
  // "rfc" appears in every RFC document — useless as a discriminator
  'rfc',
]);

// K-number pattern: K followed by 4+ digits (e.g. K14190, K000133373)
const K_NUMBER_RE = /\bk\d{4,}\b/gi;

// CVE identifier pattern
const CVE_RE = /\bcve-\d{4}-\d+\b/gi;

// RFC number pattern: "RFC 8267", "RFC8267", "rfc-1156" -> doc_id 'rfc8267'
const RFC_NUMBER_RE = /\brfc[\s-]?(\d{1,5})\b/gi;

// iRules Tcl namespace command pattern (e.g. TCP::collect, HTTP::redirect, SSL::cipher)
const IRULE_CMD_RE = /\b([A-Za-z]+)::([A-Za-z_]+)\b/g;

// Strip FTS5 special chars (including hyphens — FTS5 treats '-' as NOT operator),
// then remove stop words, returning significant AND-matched terms only.
function extractFtsTerms(query: string): string {
  const sanitized = query
    .replace(/[^\w\s]/g, ' ')   // strip all non-word/non-space (including hyphens/dots)
    .replace(/\s{2,}/g, ' ')
    .trim()
    .slice(0, 512);

  const terms = sanitized
    .toLowerCase()
    .split(/\s+/)
    .filter(t => t.length >= 2 && !STOP_WORDS.has(t));

  return terms.join(' ');
}

export async function searchDocuments(
  query: string,
  limit: number = 5,
  sources?: string[]
): Promise<SearchResult[]> {
  const results: SearchResult[] = [];
  const seenIds = new Set<number>();

  // ── 1. Direct K-number lookup ────────────────────────────────────────────
  // If the query contains a K-number (e.g. "K14190"), fetch it directly by
  // doc_id before running FTS — phrase queries never match these.
  const kMatches = query.match(K_NUMBER_RE);
  if (kMatches) {
    for (const kRaw of kMatches) {
      const kNum = kRaw.toUpperCase();
      let directSql = `
        SELECT id, source, doc_id, title, url, section, content, '' as snippet
        FROM documents WHERE doc_id = ?
      `;
      const directParams: any[] = [kNum];
      if (sources && sources.length > 0) {
        const placeholders = sources.map(() => '?').join(',');
        directSql += ` AND source IN (${placeholders})`;
        directParams.push(...sources);
      }
      try {
        const directRows = prep(directSql).all(...directParams) as SearchResult[];
        for (const row of directRows) {
          if (!seenIds.has(row.id)) {
            seenIds.add(row.id);
            results.push(row);
          }
        }
      } catch (err) {
        console.error('Direct K-number lookup error:', err);
      }
    }
  }

  // ── 1a. Direct RFC-number lookup ─────────────────────────────────────────
  // "RFC 8267" / "rfc8267" -> fetch doc_id 'rfc8267' directly. A bare RFC number
  // ranks poorly in FTS (the digits aren't identifier-weighted and 'rfc' is a
  // stop word), so an exact lookup is required — mirrors the K-number branch.
  // Unlike K-numbers, this intentionally ignores the mode source filter: an
  // explicitly named RFC is self-disambiguating and should resolve in any mode.
  const rfcMatches = [...query.matchAll(RFC_NUMBER_RE)];
  if (rfcMatches.length > 0) {
    const rfcSql = `
      SELECT id, source, doc_id, title, url, section, content, '' as snippet
      FROM documents WHERE doc_id = ?
    `;
    for (const m of rfcMatches) {
      const rfcId = `rfc${m[1]}`;
      try {
        const rfcRows = prep(rfcSql).all(rfcId) as SearchResult[];
        for (const row of rfcRows) {
          if (!seenIds.has(row.id)) {
            seenIds.add(row.id);
            results.push(row);
          }
        }
      } catch (err) {
        console.error('Direct RFC-number lookup error:', err);
      }
    }
  }

  // ── 1b. Direct CVE keyword search ───────────────────────────────────────
  // CVE IDs are stored as keywords; phrase-match them in the FTS keywords column.
  const cveMatches = query.match(CVE_RE);
  if (cveMatches && results.length < limit) {
    for (const cveRaw of cveMatches) {
      const cveLower = cveRaw.toLowerCase();
      let cveSql = `
        SELECT id, source, doc_id, title, url, section, content, '' as snippet
        FROM documents WHERE keywords LIKE ?
      `;
      const cveParams: any[] = [`%${cveLower}%`];
      if (sources && sources.length > 0) {
        const placeholders = sources.map(() => '?').join(',');
        cveSql += ` AND source IN (${placeholders})`;
        cveParams.push(...sources);
      }
      cveSql += ` LIMIT ?`;
      cveParams.push(limit - results.length);
      try {
        const cveRows = prep(cveSql).all(...cveParams) as SearchResult[];
        for (const row of cveRows) {
          if (!seenIds.has(row.id)) {
            seenIds.add(row.id);
            results.push(row);
          }
        }
      } catch (err) {
        console.error('Direct CVE lookup error:', err);
      }
    }
  }

  // ── 1c. Direct iRules command reference lookup ───────────────────────────
  // For queries containing TCL namespace commands like TCP::collect or HTTP::redirect,
  // fetch the canonical clouddocs/irules reference page by title match first,
  // so it appears in context before K-article troubleshooting entries.
  const iruleCmdMatches = [...query.matchAll(IRULE_CMD_RE)];
  if (iruleCmdMatches.length > 0 && results.length < limit) {
    const cmdSql = `
      SELECT id, source, doc_id, title, url, section, content, '' as snippet
      FROM documents
      WHERE (title LIKE ? OR url LIKE ?)
        AND source IN ('irules', 'clouddocs')
      LIMIT ?
    `;
    for (const m of iruleCmdMatches) {
      const cmdTitle = `${m[1]}::${m[2]}`;   // e.g. "TCP::collect"
      try {
        const cmdRows = prep(cmdSql).all(
          `%${cmdTitle}%`,
          `%${cmdTitle.replace('::', '__')}%`,
          limit - results.length,
        ) as SearchResult[];
        for (const row of cmdRows) {
          if (!seenIds.has(row.id)) {
            seenIds.add(row.id);
            results.push(row);
          }
        }
      } catch (err) {
        console.error('Direct iRules command lookup error:', err);
      }
    }
  }

  // If direct lookups already filled the limit, return early.
  if (results.length >= limit) return results.slice(0, limit);

  // ── 2. FTS term-based search ─────────────────────────────────────────────
  // Use stop-word-filtered terms with FTS5 implicit AND matching.
  // This avoids phrase-matching the entire conversational query verbatim.
  const ftsTerms = extractFtsTerms(query);
  if (!ftsTerms) return results;

  let sql = `
    SELECT
      d.id, d.source, d.doc_id, d.title, d.url, d.section, d.content,
      snippet(docs_fts, 2, '<b>', '</b>', '...', 64) as snippet
    FROM docs_fts f
    JOIN documents d ON f.rowid = d.id
    WHERE docs_fts MATCH ?
  `;

  const params: any[] = [ftsTerms];

  if (sources && sources.length > 0) {
    const placeholders = sources.map(() => '?').join(',');
    sql += ` AND d.source IN (${placeholders})`;
    params.push(...sources);
  }

  // Exclude already-found docs from direct lookup
  if (seenIds.size > 0) {
    const excl = Array.from(seenIds).map(() => '?').join(',');
    sql += ` AND d.id NOT IN (${excl})`;
    params.push(...Array.from(seenIds));
  }

  // bm25 column weights: title=10, keywords=5, content=1
  // Heavily favors documents where the query terms appear in the title
  // (e.g. "Overview of the Client SSL profile") over body-only matches.
  // Fetch 3× the requested limit to leave headroom for title deduplication
  // (e.g. 3,300+ docs all titled "K4918: Overview of the F5 critical issue
  // hotfix policy" would otherwise flood the top slots).
  sql += ` ORDER BY bm25(docs_fts, 10.0, 5.0, 1.0) LIMIT ?`;
  params.push((limit - results.length) * 3);

  // First try: AND matching — all terms must appear in each document.
  try {
    const ftsRows = prep(sql).all(...params) as SearchResult[];
    for (const row of ftsRows) {
      if (!seenIds.has(row.id)) {
        seenIds.add(row.id);
        results.push(row);
      }
    }
  } catch (error) {
    console.error('FTS AND search error:', error);
  }

  // Second try: OR matching — fires only if AND returned nothing.
  // Handles queries like "Which RFC governs IPsec?" where "governs" is rare
  // in RFC text, causing AND to miss documents that clearly match on "ipsec".
  if (results.length === 0 && ftsTerms.includes(' ')) {
    const orExpr = ftsTerms.split(' ').join(' OR ');
    const orParams = [orExpr, ...params.slice(1)];  // swap only the MATCH expression
    // Note: params already uses 3× limit from the AND pass above
    try {
      const orRows = prep(sql).all(...orParams) as SearchResult[];
      for (const row of orRows) {
        if (!seenIds.has(row.id)) {
          seenIds.add(row.id);
          results.push(row);
        }
      }
    } catch (error) {
      console.error('FTS OR search error:', error);
    }
  }

  // Deduplicate by normalized title — prevents documents sharing an identical
  // title (e.g. thousands of K4918-titled bug articles) from consuming multiple
  // result slots. The first occurrence (highest BM25 score) wins.
  const seenTitles = new Set<string>();
  const deduped: SearchResult[] = [];
  for (const r of results) {
    const key = r.title.trim().toLowerCase();
    if (seenTitles.has(key)) continue;
    seenTitles.add(key);
    deduped.push(r);
    if (deduped.length >= limit) break;
  }

  return deduped;
}

export default db;
