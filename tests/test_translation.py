"""Standalone checks for the fixed translation API and frozen outputs."""

from copy import deepcopy
import json
from pathlib import Path
import unittest

from penman.exceptions import DecodeError

from amr_translator import translate, translate_with_audit, validate_frame
from amr_translator.frame import canonical_atom_key, finalize_frame
from amr_translator.templates import expand_macros


class TranslationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixture = Path(__file__).parent / "fixtures" / "frozen.json"
        cls.cases = json.loads(fixture.read_text(encoding="utf-8"))

    def test_frozen_frames(self):
        for case in self.cases:
            with self.subTest(case=case["name"]):
                frame = translate(case["raw_amr"])
                self.assertEqual(frame, case["frame"])
                validate_frame(frame)

    def test_audit_is_json_serializable_and_preserves_frame(self):
        for case in self.cases:
            with self.subTest(case=case["name"]):
                audit = translate_with_audit(case["raw_amr"])
                decoded = json.loads(json.dumps(audit, allow_nan=False))
                self.assertEqual(decoded["frame"], case["frame"])

    def test_intermediate_frame_preserves_case_for_rules(self):
        frame = translate("(b / boy)")
        atom = frame["atoms"][0]
        for raw, expected in (
            (" \tAda read book\r\n", "Ada read book"),
            (" Ada read book. ", "Ada read book."),
            (" Alan live at U.S. ", "Alan live at U.S."),
            (" about 0.5 hours ", "about 0.5 hours"),
            (" Ada  read book ", "Ada  read book"),
        ):
            with self.subTest(raw=raw):
                internal = {
                    "formula_ast": deepcopy(frame["formula_ast"]),
                    "atoms": [{
                        "id": atom["id"],
                        "canonical_key": canonical_atom_key(atom["expression"]),
                        "canonical_payload": {
                            key: deepcopy(value) for key, value in atom["expression"].items()
                            if key != "type"
                        },
                        "base_surface_text": raw,
                    }],
                }
                result = finalize_frame(internal)
                self.assertEqual(result["atoms"][0]["verbalization"], expected)
                self.assertEqual(result["atoms"][0]["expression"], atom["expression"])
                self.assertEqual(result["formula_ast"], frame["formula_ast"])
                self.assertEqual(internal["atoms"][0]["base_surface_text"], raw)

    def test_public_verbalizations_are_ready_for_exact_matching(self):
        for name, expected in (("Ada", "ada"), ("Straße", "strasse"),
                               ("U.S.", "u.s."), ("Ada  Lee", "ada lee")):
            for template, suffix in (
                ('(p / person :name (n / name :op1 "{}"))', ' exists'),
                ('(g / good-02 :ARG1 (p / person :name (n / name :op1 "{}")) '
                 ':degree (v / very))', ' is very good'),
            ):
                with self.subTest(name=name, template=template):
                    raw = template.format(name)
                    audit = translate_with_audit(raw)
                    self.assertEqual(translate(raw), audit['frame'])
                    self.assertEqual(audit['frame']['atoms'][0]['verbalization'], expected + suffix)
                    for merge in audit['merges']:
                        atom = next(a for a in audit['frame']['atoms'] if a['id'] == merge['atom_id'])
                        self.assertEqual(merge['surface'], atom['verbalization'])

    def test_merges_consume_two_dyads_over_three_nodes_once(self):
        merge_count = 0
        for case in self.cases:
            with self.subTest(case=case["name"]):
                audit = translate_with_audit(case["raw_amr"])
                atom_ids, graph_ids = [], []
                for merge in audit["merges"]:
                    merge_count += 1
                    self.assertEqual(len(set(merge["nodes"])), 3)
                    self.assertEqual(len(merge["edges"]), 2)
                    self.assertEqual(len(set(merge["component_atom_ids"])), 2)
                    self.assertEqual(len(set(merge["component_dyad_ids"])), 2)
                    atom_ids.extend(merge["component_atom_ids"])
                    graph_ids.extend(merge["source_graph_record_ids"])
                self.assertEqual(len(atom_ids), len(set(atom_ids)))
                self.assertEqual(len(graph_ids), len(set(graph_ids)))
                self.assertFalse(any(
                    atom["expression"].get("scope_type")
                    for atom in audit["frame"]["atoms"]
                ))
        self.assertGreater(merge_count, 0)

    def test_finalized_frame_does_not_share_mutable_input(self):
        frame = translate('(b / boy :name (n / name :op1 "Ada"))')
        atom = frame["atoms"][0]
        internal = {
            "formula_ast": {"op": "and", "args": [
                {"op": "true"}, deepcopy(frame["formula_ast"]),
            ]},
            "atoms": [{
                "id": atom["id"],
                "canonical_key": canonical_atom_key(atom["expression"]),
                "canonical_payload": {
                    key: deepcopy(value) for key, value in atom["expression"].items()
                    if key != "type"
                },
                "base_surface_text": atom["verbalization"],
            }],
        }
        unused = deepcopy(internal["atoms"][0])
        unused["id"] = "unused"
        internal["atoms"].append(unused)
        before = deepcopy(internal)
        first = finalize_frame(internal)
        second = finalize_frame(internal)
        self.assertEqual(first, frame)
        first["atoms"][0]["expression"]["node"]["metadata"].append(
            {"role": "changed", "value": "changed"}
        )
        first["formula_ast"]["id"] = "changed"
        self.assertEqual(internal, before)
        self.assertEqual(second, frame)

    def test_merge_macro_expansion_preserves_surrounding_negation(self):
        audit = translate_with_audit('(g / good-02 :ARG1 (b / boy) :degree (v / very))')
        self.assertEqual(len(audit["merges"]), 1)
        formula = {"op": "not", "arg": deepcopy(audit["frame"]["formula_ast"])}
        before = deepcopy(formula)
        expanded = expand_macros(formula, audit["merges"])
        self.assertEqual(expanded, {
            "op": "not", "arg": audit["merges"][0]["definition"],
        })
        expanded["arg"]["args"][0]["id"] = "changed"
        self.assertEqual(formula, before)
        self.assertNotEqual(audit["merges"][0]["definition"]["args"][0]["id"], "changed")

    def test_requires_nonempty_amr_string(self):
        for value in (None, 3, b"(b / boy)", {}, [], "", " \n\t"):
            for entry_point in (translate, translate_with_audit):
                with self.subTest(value=value, entry_point=entry_point.__name__):
                    with self.assertRaises(TypeError):
                        entry_point(value)

    def test_malformed_penman_is_not_silently_translated(self):
        for entry_point in (translate, translate_with_audit):
            with self.subTest(entry_point=entry_point.__name__):
                with self.assertRaises(DecodeError):
                    entry_point("(r / run-01 :ARG0 (b / boy)")

    def test_translation_policy_has_no_overrides(self):
        raw_amr = "(r / run-01 :ARG0 (b / boy) :polarity -)"
        for entry_point in (translate, translate_with_audit):
            for options in ({"repairs": False}, {"scope_nonfactive": True},
                            {"text": "unrelated source text"}):
                with self.subTest(entry_point=entry_point.__name__, options=options):
                    with self.assertRaises(TypeError):
                        entry_point(raw_amr, **options)

    def test_frame_validation_rejects_atom_formula_mismatch(self):
        frame = translate("(b / boy)")
        unknown_reference = deepcopy(frame)
        unknown_reference["formula_ast"] = {"op": "atom", "id": "missing"}
        duplicate_atom = deepcopy(frame)
        duplicate_atom["atoms"].append(deepcopy(frame["atoms"][0]))
        for invalid in (unknown_reference, duplicate_atom):
            with self.subTest(frame=invalid):
                with self.assertRaises(RuntimeError):
                    validate_frame(invalid)


if __name__ == "__main__":
    unittest.main()
