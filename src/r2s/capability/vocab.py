"""Vocabulary and stand-in catalog enforcement.

The contract: an unknown capability ID is a HARD ERROR, never silently treated as
"missing". Silently treating it as missing routes the operation to ask-user or drop,
which hides a converter bug as a capability gap. Same for a stand-in that is not in
the catalog -- an invented stand-in is indistinguishable from fabrication.
"""

from .. import config
from ..util.io import load_json


class VocabError(Exception):
    """Raised on any unknown ID or malformed vocabulary entry."""


class CapabilityVocab:
    def __init__(self, data):
        self.version = data["version"]
        self.kinds = tuple(data["kinds"])
        self._caps = data["capabilities"]

    @classmethod
    def load(cls, path=None):
        return cls(load_json(path or config.CAPABILITIES_FILE))

    @property
    def ids(self):
        return frozenset(self._caps)

    def has(self, capability_id):
        return capability_id in self._caps

    def kind(self, capability_id):
        entry = self._caps.get(capability_id)
        if entry is None:
            raise VocabError(f"unknown capability id: {capability_id!r}")
        return entry["kind"]

    def describe(self, capability_id):
        entry = self._caps.get(capability_id)
        if entry is None:
            raise VocabError(f"unknown capability id: {capability_id!r}")
        return entry["desc"]

    def validate(self, capability_ids, where):
        """Hard-fail on the first unknown ID. Returns the ids unchanged if valid."""
        unknown = [c for c in capability_ids if c not in self._caps]
        if unknown:
            raise VocabError(
                f"unknown capability id(s) in {where}: {sorted(unknown)}. "
                f"Add them to capabilities.json or correct the inventory."
            )
        return list(capability_ids)

    def by_kind(self):
        out = {k: [] for k in self.kinds}
        for cid, entry in self._caps.items():
            out[entry["kind"]].append(cid)
        return {k: sorted(v) for k, v in out.items()}


class StandinCatalog:
    """Curated degraded substitutes. Referenced by ID; never defined inline."""

    def __init__(self, data):
        self.version = data.get("version", "0.0.0")
        self._entries = {e["id"]: e for e in data["standins"]}
        # capability -> stand-in id. Curated, so a stand-in is always a declared
        # choice rather than something the extractor invented.
        self.defaults = dict(data.get("defaults") or {})

    @classmethod
    def load(cls, path=None):
        return cls(load_json(path or config.STANDIN_INDEX))

    @property
    def ids(self):
        return frozenset(self._entries)

    def get(self, standin_id):
        entry = self._entries.get(standin_id)
        if entry is None:
            raise VocabError(
                f"unknown stand-in id: {standin_id!r}. Stand-ins must come from the "
                f"curated catalog; inline definitions are rejected by design."
            )
        return entry

    def validate(self, standin_id, where):
        """Returns the resolved entry. Raises if unknown, or if fidelity is dishonest."""
        if standin_id is None:
            return None
        entry = self.get(standin_id)
        fidelity = entry.get("fidelity")
        if fidelity is None or not isinstance(fidelity, (int, float)):
            raise VocabError(f"stand-in {standin_id!r} ({where}) declares no numeric fidelity")
        if not (0.0 <= fidelity < 1.0):
            raise VocabError(
                f"stand-in {standin_id!r} ({where}) declares fidelity {fidelity}; "
                f"must be >= 0.0 and < 1.0. A stand-in at 1.0 is not a stand-in."
            )
        return entry

    def all(self):
        return list(self._entries.values())
