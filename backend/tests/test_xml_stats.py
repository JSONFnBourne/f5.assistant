"""Integration tests for the TMOS *_module.xml streaming parser.

Runs `extract_qkview` against the TMOS reference archive
(`post_upgrade.qkview`) and pins the runtime-stat taxonomy we rely on for
the webapp's runtime-stats panels and certificate expiry view.

Baseline counts come from the archive as of 2026-04-18. If future TMOS
qkview captures legitimately change the shape (new XML categories,
different VS/pool counts), update the assertions here rather than
loosening them.
"""

from __future__ import annotations


class TestXmlStatsTaxonomy:
    def test_xml_stats_present(self, tmos_post_upgrade_data):
        assert tmos_post_upgrade_data.xml_stats is not None

    def test_core_runtime_categories_populated(self, tmos_post_upgrade_data):
        # These are the non-certificate entries corkscrew's xmlStats taxonomy
        # calls out and the reporter exports. If any drop to zero on a
        # well-formed TMOS qkview we've regressed the stream parser or the
        # `_CATEGORY_MAP` lookup.
        xs = tmos_post_upgrade_data.xml_stats
        summary = xs.summary()
        assert summary["virtual_servers"] > 0, summary
        assert summary["pools"] > 0, summary
        assert summary["pool_members"] > 0, summary
        assert summary["tmms"] > 0, summary
        assert summary["interfaces"] > 0, summary
        assert summary["db_variables"] > 0, summary

    def test_certificate_summary_captured(self, tmos_post_upgrade_data):
        # `certificate_summary` was missing from `_CATEGORY_MAP` before
        # session 12 — the map still had corkscrew's stale
        # `certificate_stat` / `certificate_list_stat` names, which no real
        # TMOS qkview emits. 0 captured means we've regressed back.
        xs = tmos_post_upgrade_data.xml_stats
        assert len(xs.certificates) > 0, "no certificate_summary records captured"
        # Rich cert metadata we rely on for the UI's expiry panel.
        cert = xs.certificates[0]
        expected_keys = {
            "name",
            "subject",
            "issuer",
            "expiration_string",
            "expiration_date",
            "fingerprint",
            "certificate_key_size",
        }
        missing = expected_keys - set(cert.fields.keys())
        assert not missing, f"certificate_summary missing fields: {missing}"

    def test_top_expiring_certificates_sorted_ascending(self, tmos_post_upgrade_data):
        # `top_expiring_certificates` must surface the *soonest*-to-expire
        # certs first so the UI's expiry panel flags real operational risk.
        # Ordering must hold whether the archive contains user-imported
        # certs or not — a homelab VE with nothing but the shipped trust
        # bundles returns an empty list here (session 16's dedupe fix),
        # which is trivially sorted.
        xs = tmos_post_upgrade_data.xml_stats
        top = xs.top_expiring_certificates(10)

        def _epoch(rec):
            try:
                return int(rec.fields.get("expiration_date", "0") or "0")
            except ValueError:
                return 0

        epochs = [_epoch(r) for r in top]
        assert epochs == sorted(epochs), epochs

    def test_trust_bundle_filtered_from_expiry_panel(self, tmos_post_upgrade_data):
        # The shipped `ca-bundle.crt` / `f5-ca-bundle.crt` trust stores
        # explode into 900+ `certificate_summary` rows named
        # `.../<bundle>.crt.NNN`. Session 16 filters them out of the
        # expiry panel so user-imported certs aren't drowned. If a row
        # with a trailing `.crt.<digits>` ever makes it through, the
        # filter's regressed.
        import re
        _bundle_re = re.compile(r"\.crt\.\d+$")
        xs = tmos_post_upgrade_data.xml_stats
        for r in xs.top_expiring_certificates(50):
            name = r.fields.get("name") or ""
            assert not _bundle_re.search(name), f"bundle entry leaked: {name}"

    def test_summary_counts_dedupe_by_name(self, tmos_post_upgrade_data):
        # Runtime stat categories emit N replica rows per resource (one
        # per TMM / plane / sampling window). Session 16 made
        # `summary()` return distinct-resource counts; raw `len()` on
        # the underlying list should be strictly higher than the
        # summary count on any archive with real traffic.
        xs = tmos_post_upgrade_data.xml_stats
        summary = xs.summary()
        assert summary["virtual_servers"] < len(xs.virtual_servers)
        assert summary["pools"] < len(xs.pools)
        assert summary["tmms"] < len(xs.tmms)
        assert summary["cpus"] < len(xs.cpus)
