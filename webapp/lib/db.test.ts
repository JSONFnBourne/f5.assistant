import { describe, it, expect, afterAll } from 'vitest';
import Database from 'better-sqlite3';
import path from 'path';
import fs from 'fs';

// db.ts restricts KSI_DB_PATH to within <cwd>/../db, and reads the env var at
// module load — so build the fixture INSIDE db/ and set the env BEFORE importing.
// (Assumes vitest runs with cwd = webapp/, which `npm test` guarantees.)
const FIXTURE = path.resolve(process.cwd(), '..', 'db', '__test_fixture__.db');
function rmFixture() {
  for (const f of [FIXTURE, `${FIXTURE}-wal`, `${FIXTURE}-shm`]) {
    if (fs.existsSync(f)) fs.unlinkSync(f);
  }
}
rmFixture();

const build = new Database(FIXTURE);
build.exec(`
  CREATE TABLE documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT, doc_id TEXT UNIQUE, title TEXT, url TEXT, section TEXT,
    keywords TEXT, content_hash TEXT, content TEXT, local_path TEXT,
    last_fetched TEXT, created_at TEXT
  );
  CREATE VIRTUAL TABLE docs_fts USING fts5(title, keywords, content, content=documents, content_rowid=id);
`);
const ins = build.prepare(
  `INSERT INTO documents (source, doc_id, title, url, keywords, content)
   VALUES (@source, @doc_id, @title, @url, @keywords, @content)`
);
const ROWS = [
  { source: 'f5_kb', doc_id: 'K14190', title: 'K14190: Client SSL profile cipher configuration',
    url: 'https://my.f5.com/K14190', keywords: 'ssl cipher', content: 'Configuring cipher suites on the Client SSL profile.' },
  // two docs sharing an identical title -> title-dedup must collapse to one
  { source: 'f5_kb', doc_id: 'K4918a', title: 'K4918: Overview of the F5 critical issue hotfix policy',
    url: 'https://my.f5.com/K4918a', keywords: '', content: 'Critical hotfix policy details, copy one.' },
  { source: 'f5_kb', doc_id: 'K4918b', title: 'K4918: Overview of the F5 critical issue hotfix policy',
    url: 'https://my.f5.com/K4918b', keywords: '', content: 'Critical hotfix policy details, copy two.' },
  { source: 'rfc', doc_id: 'rfc8267', title: 'Network File System Version 4 Minor Version 2',
    url: 'https://rfc-editor.org/rfc8267', keywords: '', content: 'NFSv4.2 protocol operations.' },
  { source: 'f5_security', doc_id: 'K000130024', title: 'K000130024: OpenSSL vulnerability',
    url: 'https://my.f5.com/K000130024', keywords: 'cve-2022-3996 openssl', content: 'OpenSSL vulnerability advisory.' },
  { source: 'irules', doc_id: 'https://clouddocs.f5.com/api/irules/HTTP__redirect.html', title: 'HTTP::redirect',
    url: 'https://clouddocs.f5.com/api/irules/HTTP__redirect.html', keywords: '', content: 'Redirects an HTTP request to another URL.' },
  { source: 'f5_kb', doc_id: 'K7208', title: 'OneConnect profile overview',
    url: 'https://my.f5.com/K7208', keywords: 'oneconnect', content: 'OneConnect enables server-side connection reuse.' },
  { source: 'bugtracker', doc_id: 'ID123456', title: 'Bug ID 123456: SNAT pool leak',
    url: 'https://cdn.f5.com/product/bugtracker/ID123456.html', keywords: 'ID123456', content: 'SNAT automap pool memory leak under load.' },
];
const tx = build.transaction(() => ROWS.forEach(r => ins.run(r)));
tx();
build.exec(`INSERT INTO docs_fts(docs_fts) VALUES('rebuild')`);
build.close();

process.env.KSI_DB_PATH = FIXTURE;
const { searchDocuments } = await import('./db');

afterAll(rmFixture);

describe('searchDocuments — direct-lookup ladder', () => {
  it('resolves a K-number by exact doc_id', async () => {
    const r = await searchDocuments('Tell me about K14190', 5);
    expect(r[0].doc_id).toBe('K14190');
  });

  it('resolves an RFC number by exact doc_id', async () => {
    const r = await searchDocuments('What is in RFC 8267?', 5);
    expect(r.some(d => d.doc_id === 'rfc8267')).toBe(true);
  });

  it('resolves a CVE via the keywords column', async () => {
    const r = await searchDocuments('Which article covers CVE-2022-3996?', 5);
    expect(r.some(d => d.doc_id === 'K000130024')).toBe(true);
  });

  it('resolves an iRule NS::command to its reference page', async () => {
    const r = await searchDocuments('How does HTTP::redirect work?', 5);
    expect(r.some(d => d.source === 'irules' && d.title === 'HTTP::redirect')).toBe(true);
  });
});

describe('searchDocuments — FTS, dedup, filtering', () => {
  it('finds a concept doc by FTS terms', async () => {
    const r = await searchDocuments('oneconnect connection reuse', 5);
    expect(r.some(d => d.doc_id === 'K7208')).toBe(true);
  });

  it('deduplicates identical titles to a single slot', async () => {
    const r = await searchDocuments('hotfix policy', 5);
    const k4918 = r.filter(d => d.title.startsWith('K4918:'));
    expect(k4918.length).toBe(1);
  });

  it('honours the source filter (excludes bugtracker when not listed)', async () => {
    const r = await searchDocuments('snat pool', 5, ['f5_kb']);
    expect(r.every(d => d.source === 'f5_kb')).toBe(true);
    expect(r.some(d => d.doc_id === 'ID123456')).toBe(false);
  });

  it('returns bugtracker docs when that source IS listed', async () => {
    const r = await searchDocuments('snat pool leak', 5, ['bugtracker']);
    expect(r.some(d => d.doc_id === 'ID123456')).toBe(true);
  });

  it('falls back to OR matching when AND yields nothing', async () => {
    // "frobnicate" exists in no doc, so AND(snat, frobnicate) = 0 -> OR fires
    const r = await searchDocuments('snat frobnicate', 5);
    expect(r.length).toBeGreaterThan(0);
    expect(r.some(d => d.doc_id === 'ID123456')).toBe(true);
  });

  it('respects the limit', async () => {
    const r = await searchDocuments('hotfix', 1);
    expect(r.length).toBeLessThanOrEqual(1);
  });
});
