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
#
# This is one flat set checked against the package name as read from whichever
# ecosystem it came from, so a name shared across ecosystems is ignored in all of them.
# That is a deliberate trade: the alternative is a per-ecosystem table that has to be
# kept in step with four manifests, and the failure mode of the flat set is a
# *missing* requirement for a package that happens to collide with a framework name.
# A missing requirement is visible in the inventory as an unresolved dependency; a
# spurious `external.call` on every route of every Rust service is not.
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
        # --- Rust (cargo) -------------------------------------------------------------
        # Web frameworks and async runtimes. A framework is how the repo is built, not
        # a capability it needs; ignoring it is what stops `axum` becoming an
        # `external.call` requirement on every route.
        "axum",
        "actix-web",
        "actix",
        "rocket",
        "warp",
        "poem",
        "tide",
        "salvo",
        "hyper",
        "tower",
        "tower-http",
        "tonic",
        "tonic-build",
        "prost",
        "http",
        "http-body",
        "http-body-util",
        "tokio",
        "tokio-util",
        "async-std",
        "smol",
        "futures",
        "futures-util",
        "async-trait",
        "mio",
        # CLI parsing, serialisation, logging and error plumbing.
        "clap",
        "clap-derive",
        "structopt",
        "argh",
        "serde",
        "serde_json",
        "serde_yaml",
        "serde_derive",
        "tracing",
        "tracing-subscriber",
        "log",
        "env_logger",
        "anyhow",
        "thiserror",
        "eyre",
        "color-eyre",
        # Build and test tooling.
        "criterion",
        "proptest",
        "quickcheck",
        "mockall",
        "rstest",
        "tempfile",
        "assert_cmd",
        "predicates",
        "insta",
        "quote",
        "syn",
        "proc-macro2",
        "cc",
        "bindgen",
        # --- Go ------------------------------------------------------------------------
        # Module paths, because that is what `read_go_deps` normalises to.
        "github.com/gin-gonic/gin",
        "github.com/labstack/echo/v4",
        "github.com/go-chi/chi/v5",
        "github.com/gofiber/fiber/v2",
        "github.com/gorilla/mux",
        "github.com/gorilla/websocket",
        "github.com/gorilla/sessions",
        "github.com/spf13/cobra",
        "github.com/spf13/pflag",
        "github.com/urfave/cli/v2",
        "github.com/alecthomas/kong",
        "github.com/spf13/viper",
        "github.com/joho/godotenv",
        "github.com/kelseyhightower/envconfig",
        "github.com/stretchr/testify",
        "github.com/stretchr/objx",
        "github.com/onsi/ginkgo/v2",
        "github.com/onsi/gomega",
        "go.uber.org/mock",
        "github.com/golang/mock",
        "go.uber.org/zap",
        "github.com/sirupsen/logrus",
        "github.com/rs/zerolog",
        "github.com/google/uuid",
        "github.com/pkg/errors",
        "golang.org/x/sync",
        "golang.org/x/time",
        # --- JVM (maven / gradle) ------------------------------------------------------
        # `groupId:artifactId` for Maven, `group:artifact` for Gradle.
        "org.springframework.boot:spring-boot-starter-web",
        "org.springframework.boot:spring-boot-starter-test",
        "org.springframework.boot:spring-boot-starter-parent",
        "org.springframework.boot:spring-boot-starter",
        "org.springframework.boot:spring-boot-starter-actuator",
        "org.springframework.boot:spring-boot-starter-validation",
        "org.springframework.boot:spring-boot-devtools",
        "org.springframework:spring-web",
        "org.springframework:spring-webmvc",
        "org.springframework:spring-context",
        "org.springframework:spring-core",
        "org.springframework:spring-beans",
        "org.springframework:spring-tx",
        "org.junit.jupiter:junit-jupiter",
        "org.junit.jupiter:junit-jupiter-api",
        "org.junit.jupiter:junit-jupiter-engine",
        "org.mockito:mockito-core",
        "org.assertj:assertj-core",
        "org.projectlombok:lombok",
        "com.fasterxml.jackson.core:jackson-databind",
        "ch.qos.logback:logback-classic",
        "org.slf4j:slf4j-api",
        "org.hibernate.validator:hibernate-validator",
        "org.springframework.boot:spring-boot",
        "org.springframework.boot:spring-boot-autoconfigure",
        "org.springframework.boot:spring-boot-starter-data-jpa",
        "org.springframework.boot:spring-boot-starter-json",
        "io.spring.dependency-management",
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
