"""
version.py — Single source of truth for the ChainEX version string.

Import this everywhere a version is displayed so there is only one place
to bump it when cutting a release.

Usage:
    from version import VERSION
"""

VERSION: str = "v2.2"
