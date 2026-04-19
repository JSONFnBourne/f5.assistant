"""YAML-based known-issue detection rule engine."""

import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

import yaml

from .indexer import LogIndexer


@dataclass
class Rule:
    """A single known-issue detection rule."""
    name: str
    description: str
    severity: str                          # critical, warning, info
    category: str                          # ltm, system, ha, network, etc.
    patterns: list[dict] = field(default_factory=list)  # {msg_code: str} or {regex: str}
    correlation: Optional[dict] = None     # {type: paired, max_window_minutes: int}
    recommendation: str = ""

    def __post_init__(self):
        # Pre-compile regex patterns
        self._compiled = []
        for p in self.patterns:
            if "regex" in p:
                self._compiled.append(("regex", re.compile(p["regex"], re.IGNORECASE)))
            elif "msg_code" in p:
                self._compiled.append(("msg_code", p["msg_code"]))


@dataclass
class Finding:
    """A detected known issue."""
    rule_name: str
    rule_description: str
    severity: str
    category: str
    recommendation: str
    matched_entries: list[dict] = field(default_factory=list)  # matching log entries
    first_seen: Optional[datetime] = None
    last_seen: Optional[datetime] = None
    count: int = 0

    def to_dict(self) -> dict:
        return {
            "rule_name": self.rule_name,
            "description": self.rule_description,
            "severity": self.severity,
            "category": self.category,
            "recommendation": self.recommendation,
            "count": self.count,
            "first_seen": self.first_seen.isoformat() if self.first_seen else None,
            "last_seen": self.last_seen.isoformat() if self.last_seen else None,
            "sample_entries": self.matched_entries[:5],  # limit to 5 samples
        }


class RuleEngine:
    """Load YAML rules and scan indexed logs for known issues."""

    def __init__(self, rules_dir: str | Path = None, platform: str | None = None):
        """Initialize the rule engine.

        Args:
            rules_dir: Directory containing YAML rule files.
                       If None, uses the default rules/ directory.
            platform: Platform filter ("tmos" or "f5os"). When set, only loads
                      rule files whose top-level ``platform:`` field matches
                      (or is absent). None loads everything — used by tests
                      and legacy callers.
        """
        self.rules: list[Rule] = []
        self.platform = platform.lower() if platform else None

        if rules_dir is None:
            rules_dir = Path(__file__).parent.parent / "rules"

        self._load_rules(Path(rules_dir))

    def _load_rules(self, rules_dir: Path):
        """Load all YAML rule files from the rules directory."""
        if not rules_dir.exists():
            return

        for yaml_file in rules_dir.glob("*.yaml"):
            with open(yaml_file, "r") as f:
                data = yaml.safe_load(f)

            if not data or "rules" not in data:
                continue

            file_platform = (data.get("platform") or "").lower() or None
            if self.platform and file_platform and file_platform != self.platform:
                continue

            for rule_data in data["rules"]:
                rule = Rule(
                    name=rule_data.get("name", "unknown"),
                    description=rule_data.get("description", ""),
                    severity=rule_data.get("severity", "info"),
                    category=rule_data.get("category", "general"),
                    patterns=rule_data.get("patterns", []),
                    correlation=rule_data.get("correlation"),
                    recommendation=rule_data.get("recommendation", ""),
                )
                self.rules.append(rule)

    def scan(self, indexer: LogIndexer, progress_callback=None) -> list[Finding]:
        """Scan indexed logs against all rules.

        Args:
            indexer: LogIndexer with populated data
            progress_callback: Optional callable(status_message)

        Returns:
            List of Finding objects, sorted by severity then count
        """
        findings = []

        for i, rule in enumerate(self.rules):
            if progress_callback:
                progress_callback(f"Scanning rule {i + 1}/{len(self.rules)}: {rule.name}")

            finding = self._evaluate_rule(rule, indexer)
            if finding and finding.count > 0:
                findings.append(finding)

        # Sort: critical first, then by count
        severity_order = {"critical": 0, "warning": 1, "info": 2}
        findings.sort(key=lambda f: (severity_order.get(f.severity, 9), -f.count))

        return findings

    def _evaluate_rule(self, rule: Rule, indexer: LogIndexer) -> Optional[Finding]:
        """Evaluate a single rule against the log index."""
        all_matches = []

        for pattern_type, pattern_value in rule._compiled:
            if pattern_type == "msg_code":
                # Query by message code
                entries = indexer.query(msg_code=pattern_value, limit=10000)
                all_matches.extend(entries)
            elif pattern_type == "regex":
                # Search by regex — use FTS for initial filter, then regex refine
                # Try to extract a simple keyword for FTS
                regex_pattern = pattern_value
                entries = indexer.query(limit=50000)  # Get all entries for regex scan
                for entry in entries:
                    if regex_pattern.search(entry.get("message", "")) or \
                       regex_pattern.search(entry.get("raw_line", "")):
                        all_matches.append(entry)

        if not all_matches:
            return None

        # Handle correlation rules (e.g., paired events like down+up = flap)
        if rule.correlation and rule.correlation.get("type") == "paired":
            return self._evaluate_paired_correlation(rule, all_matches)

        # Simple rule: just count matching entries
        finding = Finding(
            rule_name=rule.name,
            rule_description=rule.description,
            severity=rule.severity,
            category=rule.category,
            recommendation=rule.recommendation,
            matched_entries=all_matches[:10],  # Keep first 10 as samples
            count=len(all_matches),
        )

        if all_matches:
            timestamps = [
                datetime.fromisoformat(e["timestamp"])
                for e in all_matches
                if e.get("timestamp")
            ]
            if timestamps:
                finding.first_seen = min(timestamps)
                finding.last_seen = max(timestamps)

        return finding

    def _evaluate_paired_correlation(
        self, rule: Rule, all_matches: list[dict]
    ) -> Optional[Finding]:
        """Evaluate a paired correlation rule (e.g., down then up = flap).

        Merge-sweep over ``timestamp_epoch``: pairs are matched whenever
        events from the two groups fall within ``max_window_minutes`` of each
        other in either direction. Each event participates in at most one
        pair. O(N + M), no per-comparison datetime parsing.
        """
        if len(rule._compiled) < 2:
            return None

        window_seconds = rule.correlation.get("max_window_minutes", 30) * 60.0

        pattern1_type, pattern1_val = rule._compiled[0]
        pattern2_type, pattern2_val = rule._compiled[1]

        def _matches(entry: dict, ptype: str, pval) -> bool:
            if ptype == "msg_code":
                return entry.get("msg_code", "") == pval
            return bool(pval.search(entry.get("message", "") or ""))

        # Split into two groups; keep sort order from indexer.query (epoch ASC).
        group1: list[dict] = []
        group2: list[dict] = []
        for entry in all_matches:
            if _matches(entry, pattern1_type, pattern1_val):
                group1.append(entry)
            if _matches(entry, pattern2_type, pattern2_val):
                group2.append(entry)

        if not group1 or not group2:
            return None

        group1.sort(key=lambda e: e.get("timestamp_epoch") or 0.0)
        group2.sort(key=lambda e: e.get("timestamp_epoch") or 0.0)

        pairs = 0
        pair_samples: list[dict] = []
        i = j = 0
        n1, n2 = len(group1), len(group2)

        while i < n1 and j < n2:
            e1, e2 = group1[i], group2[j]
            t1 = e1.get("timestamp_epoch") or 0.0
            t2 = e2.get("timestamp_epoch") or 0.0

            # Don't pair an entry with itself when a single log line
            # matched both patterns.
            if e1.get("id") is not None and e1.get("id") == e2.get("id"):
                j += 1
                continue

            if abs(t2 - t1) < window_seconds:
                pairs += 1
                if len(pair_samples) < 10:
                    pair_samples.extend([e1, e2])
                i += 1
                j += 1
            elif t1 <= t2:
                i += 1
            else:
                j += 1

        if pairs == 0:
            return None

        epochs = [
            e["timestamp_epoch"]
            for e in (*group1, *group2)
            if e.get("timestamp_epoch") is not None
        ]
        first_seen = datetime.fromtimestamp(min(epochs)) if epochs else None
        last_seen = datetime.fromtimestamp(max(epochs)) if epochs else None

        return Finding(
            rule_name=rule.name,
            rule_description=rule.description,
            severity=rule.severity,
            category=rule.category,
            recommendation=rule.recommendation,
            matched_entries=pair_samples,
            count=pairs,
            first_seen=first_seen,
            last_seen=last_seen,
        )
