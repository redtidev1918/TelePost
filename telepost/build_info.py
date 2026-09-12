"""Single committed source of build identity.

The release pipeline (PyInstaller bundle via scripts/build-release, or the
Docker image) may additionally drop a top-level ``_release_version.py`` next
to the entry points; when present it overrides these committed defaults.
"""

# Bumped automatically by release-please (extra-files in
# release-please-config.json). Release bundles and images override it at build
# time with the exact version/commit; this constant is the local-dev fallback.
RELEASE_VERSION = "2.17.4"  # x-release-please-version
RELEASE_COMMIT = "dev"
BUILD_DATE = "dev"
SERVICE = "telepost"


def release_info() -> dict:
    """Return {service, version, commit, build_date}, preferring build-time file."""
    try:
        import _release_version  # type: ignore
        return {
            "service": SERVICE,
            "version": getattr(_release_version, "RELEASE_VERSION", RELEASE_VERSION)
            or RELEASE_VERSION,
            "commit": getattr(_release_version, "RELEASE_COMMIT", RELEASE_COMMIT)
            or RELEASE_COMMIT,
            "build_date": getattr(_release_version, "BUILD_DATE", BUILD_DATE)
            or BUILD_DATE,
        }
    except Exception:
        return {
            "service": SERVICE,
            "version": RELEASE_VERSION,
            "commit": RELEASE_COMMIT,
            "build_date": BUILD_DATE,
        }
