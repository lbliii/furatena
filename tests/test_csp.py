"""Tests for docs-shell CSP allowances."""

from __future__ import annotations

from furatena.catalog.csp import extend_csp_for_google_fonts

_SAMPLE_CSP = (
    "default-src 'self'; img-src 'self' data:; base-uri 'self'; "
    "frame-ancestors 'none'; object-src 'none'; "
    "script-src 'self' 'unsafe-eval' 'nonce-abc' https://unpkg.com https://cdn.jsdelivr.net; "
    "style-src 'self' 'unsafe-inline'"
)


class TestGoogleFontsCSP:
    def test_extends_style_font_and_connect_sources(self) -> None:
        extended = extend_csp_for_google_fonts(_SAMPLE_CSP)
        assert "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com" in extended
        assert "font-src 'self' https://fonts.gstatic.com data:" in extended
        assert (
            "connect-src 'self' https://fonts.googleapis.com https://fonts.gstatic.com" in extended
        )

    def test_idempotent_when_already_extended(self) -> None:
        once = extend_csp_for_google_fonts(_SAMPLE_CSP)
        twice = extend_csp_for_google_fonts(once)
        assert once == twice
