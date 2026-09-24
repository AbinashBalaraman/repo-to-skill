"""T2: the provider catalog.

The scaling asset. Maps package/import names to capabilities, credentials, effect
class and gate hints. Every entry added improves extraction for every repo.

Two rules that matter more than coverage:

  1. An ambiguous package must never be resolved by import alone. `boto3` could be
     storage, database, queue or notification. Import-only detection marks it
     uncertain and lowers confidence rather than requiring all four -- over-requiring
     wrongly blocks operations.
  2. An unknown package must never yield "requires nothing". It becomes the generic
     external.call fallback at low confidence. Silently assuming nothing is needed is
     the dangerous direction.
"""

from .. import config
from ..util.io import load_json

# Tooling, frameworks and build systems. Their presence says something about the repo
# class, but nothing about a capability, so they must not generate requirements.
IGNORED_PACKAGES = frozenset(
    {
        "pytest",
        "pytest-cov",
        "black",
        "ruff",
        "flake8",
        "mypy",
        "isort",
        "coverage",
        "tox",
        "nox",
        "pre-commit",
        "setuptools",
        "wheel",
        "build",
        "twine",
        "hatch",
        "poetry",
        "pip",
        "pipenv",
        "virtualenv",
        "pylint",
        "bandit",
        "pytest-asyncio",
        "django",
        "flask",
        "fastapi",
        "starlette",
        "uvicorn",
        "gunicorn",
        "werkzeug",
        "jinja2",
        "pydantic",
        "attrs",
        "dataclasses-json",
        "typing-extensions",
        "express",
        "typescript",
        "eslint",
        "prettier",
        "webpack",
        "vite",
        "jest",
        "mocha",
        "chai",
        "nodemon",
        "ts-node",
        "tsx",
        "esbuild",
        "rollup",
        "python-dotenv",
        "dotenv",
        "pyyaml",
        "toml",
        "click",
        "typer",
        "rich",
        "tqdm",
        "colorama",
        "loguru",
        "structlog",
        "tenacity",
        "retrying",
    }
)


class ProviderMatch:
    def __init__(self, provider, matched_on, ecosystem):
        self.provider = provider
        self.matched_on = matched_on
        self.ecosystem = ecosystem

    @property
    def id(self):
        return self.provider["id"]

    @property
    def capabilities(self):
        return list(self.provider.get("capabilities", []))

    @property
    def credentials(self):
        return list(self.provider.get("credentials", []))

    @property
    def effect(self):
        return self.provider.get("effect")

    @property
    def gate_hints(self):
        return list(self.provider.get("gate_hints", []))

    @property
    def ambiguous(self):
        return bool(self.provider.get("ambiguous"))

    @property
    def note(self):
        return self.provider.get("ambiguous_note", "")

    def evidence(self):
        detail = f"{self.ecosystem}:{self.matched_on} -> {self.id}"
        if self.ambiguous:
            detail += " (ambiguous)"
        return {"source": "inferred", "loc": "manifest", "detail": detail}


class ProviderCatalog:
    def __init__(self, data):
        self.version = data.get("version", "0.0.0")
        self.providers = data["providers"]
        self.fallback = data.get("fallback", {})
        self._index = {}
        for provider in self.providers:
            for ecosystem, names in (provider.get("match") or {}).items():
                for name in names:
                    self._index[(ecosystem, name.lower())] = provider

    @classmethod
    def load(cls, path=None):
        return cls(load_json(path or config.PROVIDERS_FILE))

    def lookup(self, ecosystem, name):
        return self._index.get((ecosystem, name.lower()))

    def match(self, ecosystems):
        """Return (matches, unknown_packages). Deterministic ordering."""
        matches = []
        unknown = []
        for ecosystem in sorted(ecosystems):
            for name in sorted(ecosystems[ecosystem]):
                lowered = name.lower()
                if lowered in IGNORED_PACKAGES:
                    continue
                provider = self.lookup(ecosystem, lowered)
                if provider:
                    matches.append(ProviderMatch(provider, lowered, ecosystem))
                else:
                    unknown.append((ecosystem, lowered))
        return matches, unknown

    def fallback_match(self, ecosystem, name):
        """The external.call fallback for an unrecognised dependency."""
        return ProviderMatch(
            {
                "id": "unknown-package",
                "capabilities": [self.fallback.get("capability", "external.call")],
                "credentials": [],
                "effect": self.fallback.get("effect", "effectful-external"),
                "gate_hints": [],
            },
            name,
            ecosystem,
        )
