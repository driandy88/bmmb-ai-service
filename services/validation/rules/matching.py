"""
Cross-document matching tools for BMMB document bundle checks.

Same contract as the other tools modules: every function returns

    {
        "passed": bool,
        "message": str,
        "details": {...}
    }

Docstrings are written Google-style (with an Args: section) because the
Gemini function-calling binding sends the whole docstring as the tool's
description verbatim; per-argument text lives here, not in a separate
schema field.
"""

import difflib
from typing import Dict, List, Optional, Tuple

from ._utils import normalize_id

# Common Malay name-particle spelling variants, normalized to one canonical
# form so "MOHD" / "MUHAMMAD" / "MD" don't get penalized as a mismatch.
_NAME_ALIASES = {
    "MOHD": "MOHAMMAD",
    "MOHAMAD": "MOHAMMAD",
    "MUHAMMAD": "MOHAMMAD",
    "MUHD": "MOHAMMAD",
    "MD": "MOHAMMAD",
    "BT": "BINTI",
    "ABD": "ABDUL",
}


def _normalize_text(value: str) -> str:
    return " ".join(value.strip().upper().split())


def _normalize_name(value: str) -> str:
    tokens = _normalize_text(value).split(" ")
    return " ".join(_NAME_ALIASES.get(token, token) for token in tokens)


def _normalize_entity_name(value: str) -> str:
    # Entity names don't carry the name-particle spelling variants that
    # person names do, but OCR/extraction commonly drops or adds
    # punctuation (e.g. "SDN. BHD." vs "SDN BHD"), so strip it here.
    stripped = "".join(ch for ch in value if ch.isalnum() or ch.isspace())
    return _normalize_text(stripped)


def entity_similarity(a: str, b: str) -> float:
    """Raw similarity score (0.0-1.0) between two entity names, ignoring punctuation/case."""
    return difflib.SequenceMatcher(None, _normalize_entity_name(a), _normalize_entity_name(b)).ratio()


def person_similarity(a: str, b: str) -> float:
    """Raw similarity score (0.0-1.0) between two person names, after Malay name-particle aliasing."""
    return difflib.SequenceMatcher(None, _normalize_name(a), _normalize_name(b)).ratio()


def strict_match_entity_names(ssm_entity_name: str, target_entity_name: str) -> Dict:
    """Check that an entity name on a target document exactly matches the SSM entity name.

    Use this to confirm the entity named on a bank statement, financial
    statement, or consent form is the same entity as on the SSM corporate
    form. Try this first; if it fails only due to formatting/punctuation
    noise, fall back to fuzzy_match_entity_names before concluding it's a
    real mismatch.

    Args:
        ssm_entity_name: The entity_name from the SSM corporate form
            (source of truth).
        target_entity_name: The entity_name from the document being
            checked (bank statement, financial statement, or consent form).
    """
    normalized_ssm = _normalize_text(ssm_entity_name)
    normalized_target = _normalize_text(target_entity_name)
    passed = normalized_ssm == normalized_target

    return {
        "passed": passed,
        "message": (
            "Entity names match."
            if passed
            else f"Entity name mismatch: SSM has '{ssm_entity_name}', document has '{target_entity_name}'."
        ),
        "details": {
            "ssm_entity_name": ssm_entity_name,
            "target_entity_name": target_entity_name,
            "normalized_ssm_entity_name": normalized_ssm,
            "normalized_target_entity_name": normalized_target,
        },
    }


def fuzzy_match_entity_names(
    ssm_entity_name: str, target_entity_name: str, threshold: float = 0.95
) -> Dict:
    """Check whether two entity names are highly likely to be the same entity.

    Intended as a fallback for the agent when strict_match_entity_names fails
    due to punctuation/formatting noise (e.g. "SDN. BHD." vs "SDN BHD"). Uses
    a higher default threshold than fuzzy_match_person_names since entity
    names should stay close to an exact match.
    """
    normalized_ssm = _normalize_entity_name(ssm_entity_name)
    normalized_target = _normalize_entity_name(target_entity_name)

    similarity_score = difflib.SequenceMatcher(None, normalized_ssm, normalized_target).ratio()
    passed = normalized_ssm == normalized_target or similarity_score >= threshold

    return {
        "passed": passed,
        "message": (
            f"Entity names are highly likely to match (similarity {similarity_score:.2f})."
            if passed
            else f"Entity names do not appear to match (similarity {similarity_score:.2f})."
        ),
        "details": {
            "ssm_entity_name": ssm_entity_name,
            "target_entity_name": target_entity_name,
            "normalized_ssm_entity_name": normalized_ssm,
            "normalized_target_entity_name": normalized_target,
            "similarity_score": round(similarity_score, 4),
            "threshold": threshold,
        },
    }


def strict_match_ic_numbers(ssm_nric: str, target_nric: str) -> Dict:
    """Check that an NRIC/passport number on a target document exactly matches the SSM record.

    Use this to confirm the NRIC/passport on an identity_document or
    consent_form matches the one recorded for that person on the SSM
    corporate form. This is a numeric identifier, so there is no fuzzy
    fallback for it — any difference is a real mismatch worth flagging.

    Args:
        ssm_nric: The nric_passport from the SSM corporate form (source of
            truth) for this person.
        target_nric: The nric_passport from the document being checked
            (identity_document or consent_form).
    """
    normalized_ssm = normalize_id(ssm_nric)
    normalized_target = normalize_id(target_nric)
    passed = normalized_ssm == normalized_target

    return {
        "passed": passed,
        "message": (
            "NRIC/passport numbers match."
            if passed
            else f"NRIC/passport mismatch: SSM has '{ssm_nric}', document has '{target_nric}'."
        ),
        "details": {
            "ssm_nric": ssm_nric,
            "target_nric": target_nric,
            "normalized_ssm_nric": normalized_ssm,
            "normalized_target_nric": normalized_target,
        },
    }


def match_people_by_name(
    ssm_people: List[Tuple[str, str]], candidates: List[Tuple[str, str]], threshold: float = 0.85
) -> Dict[str, Optional[str]]:
    """Greedily pair each SSM person to their best-matching candidate document by name.

    Used to join an SSM-declared person to their identity_document (or other
    per-person document) *before* comparing a field like NRIC/passport
    between the two. Joining on that field directly doesn't work: it's the
    very value the caller wants to compare, so a changed/mismatched number
    would simply fail to key at all and the person would silently vanish
    from the comparison instead of producing a mismatch result.

    Scores every (person, candidate) pair by name similarity and assigns the
    highest-scoring pairs first, so one candidate can't be claimed by two
    people and a person can't be matched to two candidates.

    Args:
        ssm_people: (person_key, name) pairs -- person_key is an opaque
            identifier the caller uses to look up the result (e.g. the
            person's NRIC), name is their SSM-declared name.
        candidates: (candidate_key, name) pairs -- candidate_key is an opaque
            identifier for the document (e.g. its document_id), name is the
            individual name on that document.
        threshold: minimum name-similarity score to accept a pairing as a
            confident match; same default as fuzzy_match_person_names.

    Returns:
        Dict keyed by person_key, mapping to the matched candidate_key, or
        None if no candidate scored above threshold.
    """
    scored = []
    for person_key, person_name in ssm_people:
        for candidate_key, candidate_name in candidates:
            score = person_similarity(person_name, candidate_name)
            if score >= threshold:
                scored.append((score, person_key, candidate_key))
    scored.sort(key=lambda t: t[0], reverse=True)

    assignment: Dict[str, Optional[str]] = {person_key: None for person_key, _ in ssm_people}
    claimed_people = set()
    claimed_candidates = set()
    for score, person_key, candidate_key in scored:
        if person_key in claimed_people or candidate_key in claimed_candidates:
            continue
        assignment[person_key] = candidate_key
        claimed_people.add(person_key)
        claimed_candidates.add(candidate_key)
    return assignment


def fuzzy_match_person_names(ssm_name: str, target_name: str, threshold: float = 0.85) -> Dict:
    """Check whether two person names are highly likely to refer to the same individual.

    Intended as a fallback for the agent when strict matching fails but the
    names look like spelling/ordering variants (e.g. "MOHD AIMAN" vs
    "MOHAMAD AIMAN"), not as a replacement for strict_match_ic_numbers.
    """
    normalized_ssm = _normalize_name(ssm_name)
    normalized_target = _normalize_name(target_name)

    similarity_score = difflib.SequenceMatcher(None, normalized_ssm, normalized_target).ratio()
    passed = normalized_ssm == normalized_target or similarity_score >= threshold

    return {
        "passed": passed,
        "message": (
            f"Names are highly likely to match (similarity {similarity_score:.2f})."
            if passed
            else f"Names do not appear to match (similarity {similarity_score:.2f})."
        ),
        "details": {
            "ssm_name": ssm_name,
            "target_name": target_name,
            "normalized_ssm_name": normalized_ssm,
            "normalized_target_name": normalized_target,
            "similarity_score": round(similarity_score, 4),
            "threshold": threshold,
        },
    }
