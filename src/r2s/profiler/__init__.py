"""Repo profiling: what kind of thing is this, and where does it start?"""

from . import classify as _classify
from . import entrypoints as _entrypoints
from . import manifests as _manifests
from . import suitability as _suitability


class ProfileResult:
    def __init__(self, manifests, entrypoints, repo_class, class_confidence, class_evidence):
        self.manifests = manifests
        self.entrypoints = entrypoints
        self.repo_class = repo_class
        self.class_confidence = class_confidence
        self.class_evidence = class_evidence

    @property
    def dependencies(self):
        deps = set()
        for names in (self.manifests.get("ecosystems") or {}).values():
            deps.update(names)
        return deps

    def to_dict(self):
        return {
            "languages": self.manifests.get("languages", []),
            "manifests": self.manifests.get("manifests", {}),
            "ecosystems": {
                k: sorted(v) for k, v in (self.manifests.get("ecosystems") or {}).items()
            },
            "repo_class": self.repo_class,
            "repo_class_confidence": self.class_confidence,
            "entrypoints": [e.to_dict() for e in self.entrypoints],
            "has_ci": self.manifests.get("has_ci", False),
            "has_docker": self.manifests.get("has_docker", False),
            "has_make": self.manifests.get("has_make", False),
        }


def profile(snapshot):
    manifests = _manifests.detect(snapshot)
    entrypoints = _entrypoints.discover(snapshot)
    repo_class, confidence, evidence = _classify.classify(snapshot, manifests, entrypoints)
    return ProfileResult(manifests, entrypoints, repo_class, confidence, evidence)


def assess_suitability(snapshot, result, operations):
    return _suitability.assess(
        snapshot, result.manifests, result.entrypoints, result.repo_class, operations
    )
