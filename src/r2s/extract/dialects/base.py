"""T0: the dialect registry.

A dialect is a small plugin that recognises one family of declaration sites and
returns normalised candidates. This is the generalization of "look for a pipeline
declaration like agents/ and schedules/" -- it works for any repo because it matches
declaration *shapes*, not a specific repo's layout.
"""

from dataclasses import dataclass, field


@dataclass
class Candidate:
    """A proposed operation, before evidence fusion."""

    op_id: str
    name: str
    dialect: str
    declaration_kind: str
    loc: str
    order_hint: int = 0
    inputs: list = field(default_factory=list)
    phase_hint: str | None = None
    effect_hint: str | None = None
    optional_hint: bool = False
    detail: str = ""
    # Line range of the function this declaration is attached to, where there is one.
    # T1 uses it to attribute effect sites to the right operation by containment. A
    # declaration with no enclosing function (an argparse subparser, for instance) has
    # none, which is why attribution for those is flagged as weak.
    body_range: tuple | None = None

    def evidence(self):
        return {"source": "declared", "loc": self.loc, "detail": self.detail or self.dialect}

    def to_dict(self):
        return {
            "op_id": self.op_id,
            "name": self.name,
            "dialect": self.dialect,
            "declaration_kind": self.declaration_kind,
            "loc": self.loc,
            "order_hint": self.order_hint,
            "inputs": list(self.inputs),
            "phase_hint": self.phase_hint,
            "optional_hint": self.optional_hint,
        }


class Dialect:
    """Base class. Subclasses set `id`, `repo_classes` and implement `match`."""

    id = "base"
    description = ""
    # Empty means "fits every repo class".
    repo_classes: tuple[str, ...] = ()

    def match(self, snapshot, profile):
        """Yield Candidate objects. Must be deterministic in output order."""
        raise NotImplementedError

    def fits(self, repo_class):
        return not self.repo_classes or repo_class in self.repo_classes
