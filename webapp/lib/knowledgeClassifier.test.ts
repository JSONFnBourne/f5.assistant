import { describe, it, expect } from 'vitest';
import { classifyQuery, isBugIntent, sourcesForQuery, MODE_SOURCES } from './knowledgeClassifier';

describe('classifyQuery', () => {
  it('classifies F5 product questions as f5', () => {
    expect(classifyQuery('How do I configure a SNAT pool on BIG-IP LTM?')).toBe('f5');
    expect(classifyQuery('What does an iRule do on a virtual server?')).toBe('f5');
    expect(classifyQuery('How do I add a VLAN to a tenant on rSeries F5OS?')).toBe('f5');
  });

  it('classifies protocol/RFC questions as rfc', () => {
    expect(classifyQuery('What does RFC 7231 say about the 404 status code?')).toBe('rfc');
    expect(classifyQuery('Explain the TCP three-way handshake and SYN/ACK.')).toBe('rfc');
  });

  it('falls back to general when no strong signal', () => {
    expect(classifyQuery('What is the weather like today?')).toBe('general');
  });

  it('treats K-numbers, CVEs and NS::cmd as strong F5 signals (+3)', () => {
    expect(classifyQuery('Explain K14783')).toBe('f5');
    expect(classifyQuery('Which article covers CVE-2022-1388?')).toBe('f5');
    expect(classifyQuery('How is HTTP::redirect used?')).toBe('f5');
  });

  it('is case-insensitive', () => {
    expect(classifyQuery('BIG-IP LTM SNAT')).toBe(classifyQuery('big-ip ltm snat'));
  });
});

describe('isBugIntent', () => {
  it('detects a bug-tracker ID (ID######)', () => {
    expect(isBugIntent('What does F5 bug ID1000069 describe?')).toBe(true);
    expect(isBugIntent('summarize ID 898373')).toBe(true);
  });

  it('detects the standalone words bug / defect', () => {
    expect(isBugIntent('Is there a known bug with mcpd memory leaks?')).toBe(true);
    expect(isBugIntent('any defects in the SSL handshake path?')).toBe(true);
    expect(isBugIntent('list known bugs after upgrade')).toBe(true);
  });

  it('does NOT fire on debug, or IDs embedded in words, or plain how-tos', () => {
    expect(isBugIntent('how do I debug an iRule?')).toBe(false);   // "debug" != "bug"
    expect(isBugIntent('configure a RAID 5000 array')).toBe(false); // "ID" not word-start
    expect(isBugIntent('How do I add a VLAN to a tenant?')).toBe(false);
  });
});

describe('sourcesForQuery (bugtracker routing)', () => {
  const f5Base = MODE_SOURCES.f5 as string[];

  it('omits bugtracker for ordinary f5 how-to queries', () => {
    const src = sourcesForQuery('How do I add a VLAN to a tenant?', 'f5');
    expect(src).toEqual(f5Base);
    expect(src).not.toContain('bugtracker');
  });

  it('includes bugtracker only for bug-intent f5 queries', () => {
    const src = sourcesForQuery('Is there a bug where the virtual server has no listener?', 'f5');
    expect(src).toContain('bugtracker');
    // base sources are still present
    for (const s of f5Base) expect(src).toContain(s);
  });

  it('includes bugtracker for a direct bug-ID lookup', () => {
    expect(sourcesForQuery('What is bug ID1000061?', 'f5')).toContain('bugtracker');
  });

  it('does not add bugtracker outside f5 mode', () => {
    expect(sourcesForQuery('bug in RFC 7231?', 'rfc')).toEqual(MODE_SOURCES.rfc);
    expect(sourcesForQuery('any bugs?', 'general')).toBeUndefined(); // general = all sources, no filter
  });

  it('does not mutate the shared MODE_SOURCES.f5 array', () => {
    const before = [...f5Base];
    sourcesForQuery('bug ID1000061', 'f5');
    expect(MODE_SOURCES.f5).toEqual(before);
  });
});
