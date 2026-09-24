"""Harness capability profiles.

A profile declares what a harness can do. Where the harness exposes tool enumeration
the profile should be probed rather than assumed; otherwise it is a user declaration
and is recorded in the emitted skill so the user can correct it.
"""

from .. import config
from ..util.io import load_json


class ProfileError(Exception):
    pass


class Profile:
    def __init__(self, data):
        self.id = data["id"]
        self.name = data.get("name", data["id"])
        self.desc = data.get("desc", "")
        self.native = tuple(data.get("native", []))
        self.mcp = tuple(data.get("mcp", []))
        self.probe = data.get("probe")
        self.expiry = data.get("expiry")
        self.notes = data.get("notes", "")

    @property
    def available(self):
        """Everything the harness can reach, native or via an attached server."""
        return frozenset(self.native) | frozenset(self.mcp)

    def has_native(self, capability_id):
        return capability_id in self.native

    def has_mcp(self, capability_id):
        return capability_id in self.mcp

    def has(self, capability_id):
        return capability_id in self.available

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "native": list(self.native),
            "mcp": list(self.mcp),
        }


def load_profiles(profile_dir=None):
    """Load every profile in the directory, keyed by id. Deterministic ordering."""
    directory = profile_dir or config.PROFILE_DIR
    profiles = {}
    for path in sorted(directory.glob("*.json")):
        data = load_json(path)
        profile = Profile(data)
        if profile.id in profiles:
            raise ProfileError(f"duplicate profile id: {profile.id}")
        profiles[profile.id] = profile
    if not profiles:
        raise ProfileError(f"no harness profiles found in {directory}")
    return profiles


def get_profile(profiles, profile_id):
    if profile_id not in profiles:
        raise ProfileError(f"unknown profile {profile_id!r}; available: {sorted(profiles)}")
    return profiles[profile_id]


def check_expiry(profile, today=None):
    """Return a warning string if the profile is past its expiry, else None."""
    if not profile.expiry:
        return None
    import datetime

    today = today or datetime.date.today().isoformat()
    if profile.expiry < today:
        return (
            f"profile {profile.id!r} expired {profile.expiry}; "
            f"harness capabilities drift, re-probe with: {profile.probe or 'n/a'}"
        )
    return None
