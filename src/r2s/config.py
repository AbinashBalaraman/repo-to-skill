"""Paths, versions and budgets. No dependencies on anything else in r2s."""

from pathlib import Path

from . import __version__

# Single source of truth: pyproject.toml reads this dynamically, so the version is
# declared once and cannot drift between the package and its metadata.
CONVERTER_VERSION = __version__
SCHEMA_VERSION = "1.0.0"

PACKAGE_DIR = Path(__file__).resolve().parent

# Runtime data lives INSIDE the package so it ships in the wheel. Deriving these from
# the repository root (PACKAGE_DIR.parent.parent) works in a source checkout and breaks
# completely under a real install, where that resolves to the venv's lib/ directory.
# Resolve through importlib.resources so an installed wheel and a source checkout both
# work, including from a zipimport or any other non-filesystem loader.
try:
    from importlib.resources import files as _resource_files

    _DATA_ROOT = Path(str(_resource_files("r2s").joinpath("data")))
except Exception:  # pragma: no cover - very old or unusual loaders
    _DATA_ROOT = PACKAGE_DIR / "data"

DATA_DIR = _DATA_ROOT
CATALOG_DIR = DATA_DIR / "catalog"
STANDIN_DIR = CATALOG_DIR / "stand-ins"
PROFILE_DIR = DATA_DIR / "harness-profiles"
SCHEMA_DIR = DATA_DIR / "schemas"

CAPABILITIES_FILE = DATA_DIR / "capabilities.json"
PROVIDERS_FILE = CATALOG_DIR / "providers.json"
EFFECTS_FILE = CATALOG_DIR / "effects.json"
STANDIN_INDEX = STANDIN_DIR / "index.json"

# Test-only. Fixtures are deliberately not packaged: they are goldens for the eval, not
# runtime data. Callers that need them resolve them relative to the repository root.
PROJECT_ROOT = PACKAGE_DIR.parent.parent
FIXTURE_DIR = PROJECT_ROOT / "fixtures"

# Artifact names inside a conversion working directory.
DRAFT_NAME = "inventory.draft.json"
REVIEW_NAME = "inventory.review.md"
INVENTORY_NAME = "inventory.json"
REPORT_NAME = "report.json"

# Agent Skills budgets (agentskills.io/specification).
SKILL_MAX_LINES = 500
SKILL_MAX_TOKENS = 5000
NAME_MAX_CHARS = 64
DESCRIPTION_MAX_CHARS = 1024
COMPATIBILITY_MAX_CHARS = 500

# Extraction guards so a large monorepo cannot hang the run.
MAX_FILES_SCANNED = 20000
MAX_FILE_BYTES = 2_000_000
MAX_TOTAL_BYTES = 200_000_000
MAX_SECONDS = 300

IGNORE_DIRS = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        "node_modules",
        "vendor",
        "dist",
        "build",
        "target",
        ".venv",
        "venv",
        "env",
        "__pycache__",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".tox",
        "site-packages",
        ".next",
        ".nuxt",
        "coverage",
        "htmlcov",
        ".idea",
        ".vscode",
        "bower_components",
    }
)

# Generated code is not a declaration site.
IGNORE_FILE_SUFFIXES = ("_pb2.py", "_pb2_grpc.py", ".min.js", ".bundle.js", ".lock")

# Credential-bearing files. Never read these: the converter parses repository content
# and the emitted inventory is exactly the artifact a user attaches to a bug report or
# commits. Matching is on basename, case-insensitively.
SECRET_FILE_NAMES = frozenset(
    {
        ".env",
        ".env.local",
        ".env.production",
        ".env.development",
        ".env.test",
        ".netrc",
        "_netrc",
        ".npmrc",
        ".pypirc",
        ".dockercfg",
        ".git-credentials",
        "credentials",
        "credentials.json",
        "secrets.json",
        "secrets.yaml",
        "secrets.yml",
        "id_rsa",
        "id_dsa",
        "id_ecdsa",
        "id_ed25519",
        "service-account.json",
        "kubeconfig",
        ".htpasswd",
        "shadow",
    }
)

SECRET_FILE_SUFFIXES = (
    ".pem",
    ".key",
    ".pfx",
    ".p12",
    ".jks",
    ".keystore",
    ".ppk",
    ".env",
    ".envrc",
)
