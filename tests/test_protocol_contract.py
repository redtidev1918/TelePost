"""Workflow Protocol v1 contract tests.

The machine-checkable half of the PixivFlow <-> TelePost protocol is vendored under
``protocol/v1/`` (schema + fixtures + SOURCES.sha256) by
``pixivflow-telepost-deploy/scripts/sync-protocol.sh``. The normative prose lives in
``pixivflow-telepost-deploy/docs/architecture/workflow-protocol.md``.

These tests keep the vendored copy honest: it must be a valid JSON Schema, every
fixture must validate against its entry point, the vendored files must match the
recorded hashes, and the schema itself must not encode either side's business.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest

jsonschema = pytest.importorskip("jsonschema")
from jsonschema import Draft202012Validator  # noqa: E402

PROTOCOL_DIR = Path(__file__).resolve().parents[1] / "protocol" / "v1"
SCHEMA_PATH = PROTOCOL_DIR / "protocol.schema.json"
FIXTURE_DIR = PROTOCOL_DIR / "fixtures"

# filename prefix -> $defs entry point
ENTRY_POINTS = {
    "task": "Task",
    "job": "Job",
    "event": "Event",
    "result": "Result_CandidateSearch",
    "capabilities": "Capabilities",
    "jobpage": "JobPage",
    "eventpage": "EventPage",
    "asset": "Asset",
    "candidate": "Candidate",
}

# The protocol is transport between two independent products: it may name a platform
# ("pixiv"), never a product's business concepts ("review", "refetch", "slot", ...).
BUSINESS_TERMS = (
    "refetch",
    "review",
    "slot",
    "telegram",
    "message_id",
    "disposition",
    "submission",
    "moderation",
)


def _schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def _fixtures() -> list[Path]:
    if not FIXTURE_DIR.is_dir():
        return []
    return sorted(FIXTURE_DIR.glob("*.json"))


def _entry_point(path: Path) -> str:
    name = ENTRY_POINTS.get(path.name.split(".")[0])
    if name is None:
        pytest.fail(f"{path.name} has no protocol entry point (see ENTRY_POINTS)")
    return name


def _validator(entry: str) -> Draft202012Validator:
    schema = _schema()
    assert entry in schema["$defs"], f"{entry} is not a $defs member"
    return Draft202012Validator(
        {"$schema": schema["$schema"], "$ref": f"#/$defs/{entry}", "$defs": schema["$defs"]}
    )


def test_protocol_assets_are_vendored():
    assert SCHEMA_PATH.is_file(), "run scripts/sync-protocol.sh from the deploy repo"
    assert (PROTOCOL_DIR / "SOURCES.sha256").is_file()
    assert (PROTOCOL_DIR / "error-mapping.json").is_file()
    assert len(_fixtures()) >= 5


def test_schema_is_a_valid_json_schema():
    Draft202012Validator.check_schema(_schema())


def test_every_fixture_validates_against_its_entry_point():
    failures: list[str] = []
    for path in _fixtures():
        entry = _entry_point(path)
        doc = json.loads(path.read_text(encoding="utf-8"))
        errors = sorted(_validator(entry).iter_errors(doc), key=lambda e: list(e.path))
        for error in errors:
            loc = "/".join(str(part) for part in error.path) or "<root>"
            failures.append(f"{path.name} -> {entry}: {loc}: {error.message}")
    assert not failures, "fixture/schema mismatches:\n" + "\n".join(failures)


def test_every_internal_ref_resolves():
    schema = _schema()
    refs: set[str] = set()

    def walk(node) -> None:
        if isinstance(node, dict):
            if "$ref" in node:
                refs.add(node["$ref"])
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(schema)
    broken = [
        ref
        for ref in refs
        if not ref.startswith("#/$defs/") or ref.rsplit("/", 1)[-1] not in schema["$defs"]
    ]
    assert not broken, f"broken $refs: {broken}"


def test_vendored_files_match_recorded_hashes():
    manifest = PROTOCOL_DIR / "SOURCES.sha256"
    recorded: dict[str, str] = {}
    for line in manifest.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        digest, _, rel = line.partition("  ")
        recorded[rel] = digest
    assert recorded, "SOURCES.sha256 is empty"

    mismatches: list[str] = []
    for rel, digest in recorded.items():
        path = PROTOCOL_DIR / rel
        if not path.is_file():
            mismatches.append(f"{rel}: missing")
            continue
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != digest:
            mismatches.append(f"{rel}: {actual} != recorded {digest}")
    assert not mismatches, "vendored copy drifted; re-run scripts/sync-protocol.sh:\n" + "\n".join(
        mismatches
    )


def test_error_vocabulary_is_closed_and_mappable():
    """The consumer must branch on protocol codes, never on a producer's private vocabulary.

    So the error enum stays closed and the producer maps its internal reason codes
    onto it; every protocol code carries the retryable default the consumer falls
    back to when a failure payload omits the flag.
    """
    schema = _schema()
    enum = schema["$defs"]["Error"]["properties"]["code"]["enum"]
    mapping = json.loads((PROTOCOL_DIR / "error-mapping.json").read_text(encoding="utf-8"))

    protocol_codes = mapping["protocol_codes"]
    assert sorted(protocol_codes) == sorted(enum), (
        "error-mapping.json and the schema enum disagree: "
        f"only in mapping={sorted(set(protocol_codes) - set(enum))}, "
        f"only in enum={sorted(set(enum) - set(protocol_codes))}"
    )
    for code, spec in protocol_codes.items():
        assert isinstance(spec.get("retryable"), bool), f"{code} has no retryable default"

    internal = {
        code: target
        for code, target in mapping["producer_internal"].items()
        if not code.startswith("$")  # the table may carry $comment documentation
    }
    dangling = sorted({target for target in internal.values() if target not in enum})
    assert not dangling, f"producer_internal points at undefined protocol codes: {dangling}"


def test_unknown_fields_stay_acceptable():
    """Additive-only: a consumer/producer must tolerate fields it does not know."""
    schema = _schema()
    for entry in ("Task", "Job", "Event", "Capabilities"):
        doc = {
            "Task": {
                "protocol_version": "1",
                "job_type": "candidate_search",
                "idempotency_key": "k",
                "params": {},
            },
            "Job": {
                "protocol_version": "1",
                "job_id": "j",
                "job_type": "candidate_search",
                "status": "queued",
                "created_at": 1,
                "updated_at": 1,
            },
            "Event": {
                "protocol_version": "1",
                "event_id": "e",
                "job_id": "j",
                "type": "job.accepted",
                "at": 1,
            },
            "Capabilities": {"protocol_versions": ["1"], "job_types": []},
        }[entry]
        doc["field_from_a_future_minor_version"] = {"nested": [1, 2, 3]}
        validator = Draft202012Validator(
            {"$schema": schema["$schema"], "$ref": f"#/$defs/{entry}", "$defs": schema["$defs"]}
        )
        errors = list(validator.iter_errors(doc))
        assert not errors, f"{entry} rejected an unknown field: {[e.message for e in errors]}"


def test_schema_does_not_encode_either_side_business():
    """Field names, enum values and $defs names must stay product-neutral."""
    schema = _schema()
    names: list[tuple[str, str]] = []

    def collect(node, where: str) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "properties" and isinstance(value, dict):
                    for prop, sub in value.items():
                        names.append((f"{where}.properties.{prop}", prop))
                        collect(sub, f"{where}.{prop}")
                elif key == "enum" and isinstance(value, list):
                    for item in value:
                        if isinstance(item, str):
                            names.append((f"{where}.enum", item))
                elif key in ("$defs", "additionalProperties") and isinstance(value, dict):
                    for name, sub in value.items():
                        names.append((f"$defs.{name}", name))
                        collect(sub, f"$defs.{name}")
                else:
                    collect(value, where)
        elif isinstance(node, list):
            for value in node:
                collect(value, where)

    collect(schema, "")

    def tokens(text: str) -> list[str]:
        return [part for part in re.split(r"[^a-z0-9]+", text.lower()) if part]

    def has_term(name: str, term: str) -> bool:
        haystack, needle = tokens(name), tokens(term)
        if not needle:
            return False
        return any(
            haystack[i : i + len(needle)] == needle for i in range(len(haystack) - len(needle) + 1)
        )

    offenders = [
        f"{where} = {name!r}"
        for where, name in names
        for term in BUSINESS_TERMS
        if has_term(name, term)
    ]
    assert not offenders, "protocol assets must stay product-neutral:\n" + "\n".join(offenders)
