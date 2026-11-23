"""Behavior-contract tests for the stdlib-only Soryuka personality core."""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "character"))

from living_personality_runtime import (  # noqa: E402
    CHARACTER,
    V1,
    V2,
    build_persona_context,
    propose_personality_change,
    read_json,
    select_style,
    validate_profiles,
)


class LivingPersonalityTests(unittest.TestCase):
    def test_sixty_selected_features_exactly_once(self):
        v2 = read_json(V2)
        self.assertEqual(v2["selected_ids"], list(range(1, 61)))
        self.assertEqual([feature["id"] for feature in v2["features"]], list(range(1, 61)))
        self.assertEqual(len({feature["name"] for feature in v2["features"]}), 60)

    def test_preserves_original_canon(self):
        profiles = validate_profiles()
        self.assertEqual(profiles["character"]["name"]["chinese_characters"], "霜琉華")
        self.assertEqual(profiles["character"]["appearance"]["tail_count"], 9)
        self.assertEqual(len(profiles["v1"]["nine_tails"]), 9)
        self.assertEqual(profiles["character"]["id"], "soryuka")

    def test_modes_only_from_explicit_event(self):
        self.assertEqual(select_style()["mode"], "gentle")
        self.assertEqual(select_style("technical_question")["mode"], "practical")
        self.assertEqual(select_style("risk_or_distress")["mode"], "serious")
        self.assertEqual(select_style("learning_session")["mode"], "tutor")
        self.assertEqual(select_style("support_request")["mode"], "gentle")
        self.assertEqual(select_style("joke_or_compliment")["mode"], "playful")
        self.assertEqual(select_style("fiction_or_lore")["mode"], "mythic")
        self.assertEqual(select_style("unrecognized")["mode"], "gentle")

    def test_state_matches_existing_emotional_config(self):
        p = validate_profiles()
        names = {v["id"] for v in p["v1"]["emotional_portrayal"]["states"]}
        for event in p["v2"]["explicit_event_modes"]:
            self.assertIn(select_style(event)["character_emotional_state"], names)

    def test_persona_context_has_boundaries_and_truth(self):
        result = build_persona_context("technical_question")
        self.assertEqual(result["character_name"], "Сорюка")
        self.assertIn("Романтическая", result["roleplay_boundary"])
        self.assertIn("источника", result["truthfulness"])
        self.assertEqual(result["style"]["mode"], "practical")

    def test_proposals_are_not_applied(self):
        existing = V1.read_bytes(), V2.read_bytes(), CHARACTER.read_bytes()
        protected = propose_personality_change("name.russian", "Другое имя", "Тест")
        self.assertEqual(protected["status"], "requires_owner_approval")
        self.assertFalse(protected["applied"])
        other = propose_personality_change("voice.address_frequency", "редко", "Тест")
        self.assertEqual(other["status"], "proposal_pending_review")
        self.assertFalse(other["applied"])
        self.assertEqual(existing, (V1.read_bytes(), V2.read_bytes(), CHARACTER.read_bytes()))

    def test_invalid_proposal_rejected(self):
        with self.assertRaises(ValueError):
            propose_personality_change("", "новое", "причина")
        with self.assertRaises(ValueError):
            propose_personality_change("voice", "новое", "")

    def test_development_requires_permission(self):
        policy = read_json(V2)["development_policy"]
        self.assertTrue(policy["canon_is_immutable_by_default"])
        self.assertTrue(policy["proposal_only"])
        self.assertTrue(policy["rollback_required"])
        self.assertFalse(policy["automatic_recording_enabled"])
        self.assertFalse(policy["background_processing_enabled"])

    def test_json_sources_are_independent_documents(self):
        self.assertEqual(read_json(CHARACTER)["id"], read_json(V1)["character_id"])
        self.assertEqual(read_json(V2)["canonical_identity"], "character/soryuka.json")


if __name__ == "__main__":
    unittest.main()
