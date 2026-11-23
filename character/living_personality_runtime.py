"""Soryuka Living Personality 2.0 — deterministic, stdlib-only bridge.

This module is a configuration/selection helper for a future Python API, not an LLM,
not a background agent, and not a memory system. It performs no network or disk writes.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
V1 = ROOT / "personality.json"
V2 = ROOT / "living_personality_v2.json"
CHARACTER = ROOT / "soryuka.json"

REQUIRED_EVENTS = {
    "technical_question", "risk_or_distress", "learning_session",
    "support_request", "joke_or_compliment", "fiction_or_lore",
}
PROTECTED_FIELDS = (
    "name", "identity", "legend", "motto", "appearance.tail_count",
    "personality.relationship_to_master", "personality.values",
)


def read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"Expected object at {path.name}")
    return value


def validate_profiles() -> dict[str, Any]:
    """Validate canonical character invariants and feature coverage without mutating data."""
    character, v1, v2 = read_json(CHARACTER), read_json(V1), read_json(V2)
    features = v2.get("features", [])
    ids = [item.get("id") for item in features]
    if ids != list(range(1, 61)) or v2.get("selected_ids") != ids:
        raise ValueError("All 60 unique, ordered feature IDs are required")
    if character.get("id") != "soryuka" or v1.get("character_id") != "soryuka":
        raise ValueError("Profile references must match Soryuka")
    if character.get("appearance", {}).get("tail_count") != 9:
        raise ValueError("The canonical character must have exactly nine tails")
    if len(v1.get("nine_tails", [])) != 9:
        raise ValueError("The v1 personality must preserve nine tail descriptions")
    states = {item.get("id") for item in v1.get("emotional_portrayal", {}).get("states", [])}
    modes = v2.get("context_modes", {})
    mapped = v2.get("explicit_event_modes", {})
    if set(mapped) != REQUIRED_EVENTS:
        raise ValueError("Missing context routing events")
    if any(mode not in modes or modes[mode].get("emotional_state") not in states for mode in mapped.values()):
        raise ValueError("An event maps to an undefined mode or emotional state")
    if not v2.get("development_policy", {}).get("proposal_only"):
        raise ValueError("Personality updates must require review")
    return {"character": character, "v1": v1, "v2": v2}


def select_style(event: str | None = None) -> dict[str, str]:
    """Pick a mode using an EXPLICIT event supplied by trusted application logic.

    Do not infer a user's emotions from message text or from private information.
    Unknown/unset events use a calm, gentle default.
    """
    profiles = validate_profiles()
    v2 = profiles["v2"]
    mode = v2["explicit_event_modes"].get(event, "gentle")
    definition = v2["context_modes"][mode]
    return {
        "mode": mode,
        "voice": definition["voice"],
        "character_emotional_state": definition["emotional_state"],
        "personalization": definition["personalization"],
    }


def build_persona_context(event: str | None = None) -> dict[str, Any]:
    """Return a bounded persona contract for inclusion in a future chat request."""
    files = validate_profiles()
    v1, v2, character = files["v1"], files["v2"], files["character"]
    style = select_style(event)
    return {
        "character_name": character["name"]["russian"],
        "character_ideograms": character["name"]["chinese_characters"],
        "identity": character["identity"],
        "canon_motto": character["motto"],
        "essence": v1["canon"]["essence"],
        "relationship": v1["relationship"]["status"],
        "values": v1["values_order"],
        "style": style,
        "roleplay_boundary": v2["canonical_guardrails"]["role_vs_reality"],
        "truthfulness": v2["canonical_guardrails"]["truthfulness"],
        "change_policy": v2["canonical_guardrails"]["changes"],
    }


def propose_personality_change(field: str, proposed_value: Any, reason: str) -> dict[str, Any]:
    """Create a REVIEW-ONLY change proposal; never edit files or auto-approve."""
    if not field or not isinstance(field, str) or not reason.strip():
        raise ValueError("Field and reason must be provided")
    protected = any(field == prefix or field.startswith(prefix + ".") for prefix in PROTECTED_FIELDS)
    return {
        "field": field,
        "proposed_value": proposed_value,
        "reason": reason,
        "status": "requires_owner_approval" if protected else "proposal_pending_review",
        "applied": False,
        "requires_audit": True,
        "rollback_required": True,
    }


if __name__ == "__main__":
    verified = validate_profiles()
    print(json.dumps({
        "valid": True,
        "version": verified["v2"]["schema_version"],
        "features": len(verified["v2"]["features"]),
        "tails": verified["character"]["appearance"]["tail_count"],
        "default_style": select_style(),
    }, ensure_ascii=True, indent=2))
