#!/usr/bin/env python3
"""Enrich structure.json with learning_objectives and prerequisites.

This is a one-shot script -- run once, then delete.
No AI API calls; all content is deterministic based on topic titles.
"""

import json
import re
import sys
from pathlib import Path


# ---------- Prerequisites for depth-0 topics ----------
# Format: {title: [{"topic": "...", "strength": "required|recommended"}]}
PREREQUISITES = {
    "Object-Oriented Design": [
        {"topic": "Why Software Design Matters", "strength": "recommended"},
    ],
    "SOLID Principles": [
        {"topic": "Object-Oriented Design", "strength": "required"},
        {"topic": "Why Software Design Matters", "strength": "recommended"},
    ],
    "Domain-Driven Design": [
        {"topic": "Object-Oriented Design", "strength": "required"},
        {"topic": "SOLID Principles", "strength": "recommended"},
    ],
    "Architecture Patterns": [
        {"topic": "SOLID Principles", "strength": "required"},
        {"topic": "Domain-Driven Design", "strength": "recommended"},
    ],
    "Testing and TDD": [
        {"topic": "Clean Code Fundamentals", "strength": "required"},
        {"topic": "SOLID Principles", "strength": "recommended"},
    ],
    "Event-Driven Architecture": [
        {"topic": "Architecture Patterns", "strength": "required"},
        {"topic": "Domain-Driven Design", "strength": "recommended"},
    ],
    "Software Architecture": [
        {"topic": "SOLID Principles", "strength": "required"},
        {"topic": "Architecture Patterns", "strength": "recommended"},
    ],
    "Programming Paradigms": [
        {"topic": "Why Software Design Matters", "strength": "recommended"},
    ],
    "Clean Code Fundamentals": [
        {"topic": "Why Software Design Matters", "strength": "recommended"},
    ],
    "Systems Design": [
        {"topic": "Software Architecture", "strength": "required"},
        {"topic": "Clean Code Fundamentals", "strength": "recommended"},
    ],
    "Concurrency": [
        {"topic": "Clean Code Fundamentals", "strength": "required"},
        {"topic": "Testing and TDD", "strength": "recommended"},
    ],
    "Code Smells and Heuristics": [
        {"topic": "Clean Code Fundamentals", "strength": "required"},
        {"topic": "Object-Oriented Design", "strength": "recommended"},
    ],
    "Site Reliability Engineering": [
        {"topic": "Systems Design", "strength": "recommended"},
        {"topic": "Testing and TDD", "strength": "recommended"},
    ],
    # "Why Software Design Matters" has no prerequisites (entry point)
}


def _bloom_for_title(title: str) -> str:
    """Infer a bloom level from the topic title wording."""
    t = title.lower()
    if any(w in t for w in ["design", "implement", "build", "create", "construct", "write", "refactor"]):
        return "apply"
    if any(w in t for w in ["why", "evaluate", "compare", "choose", "should", "when should", "trade-off", "better", "prefer"]):
        return "evaluate"
    if any(w in t for w in ["how do", "how does", "how can", "how should", "debug", "diagnose", "identify"]):
        return "analyze"
    if any(w in t for w in ["explain", "describe", "differ", "difference", "relate", "what happens"]):
        return "understand"
    if any(w in t for w in ["what is", "what are", "define", "list", "name"]):
        return "remember"
    return "understand"


def _generate_leaf_objectives(title: str) -> list[dict]:
    """Generate 2-4 learning objectives for a leaf topic based on its title."""
    objectives = []
    t = title.lower().strip("?").strip()

    # 1. Always a remember/understand objective
    if t.startswith("what is") or t.startswith("what are"):
        core_concept = re.sub(r"^what (?:is|are) (?:the |a |an )?", "", t)
        objectives.append({
            "text": f"Define {core_concept} and state its purpose",
            "bloom_level": "remember"
        })
        objectives.append({
            "text": f"Explain the key characteristics of {core_concept}",
            "bloom_level": "understand"
        })
    elif t.startswith("why"):
        reason_topic = re.sub(r"^why (?:should you |do you |does |is |are |must |can )?", "", t)
        objectives.append({
            "text": f"Explain the rationale behind {reason_topic}",
            "bloom_level": "understand"
        })
        objectives.append({
            "text": f"Evaluate the consequences of ignoring {reason_topic}",
            "bloom_level": "evaluate"
        })
    elif t.startswith("how do") or t.startswith("how does") or t.startswith("how can") or t.startswith("how should"):
        mechanism = re.sub(r"^how (?:do you |does |can you |can |should you |should )?", "", t)
        objectives.append({
            "text": f"Describe the process of {mechanism}",
            "bloom_level": "understand"
        })
        objectives.append({
            "text": f"Apply the technique of {mechanism} to a concrete scenario",
            "bloom_level": "apply"
        })
    elif t.startswith("when"):
        decision = re.sub(r"^when (?:should you |should |do you |does )?", "", t)
        objectives.append({
            "text": f"Identify the conditions under which to {decision}",
            "bloom_level": "analyze"
        })
        objectives.append({
            "text": f"Justify the decision to {decision} in a given context",
            "bloom_level": "evaluate"
        })
    elif t.startswith("should"):
        choice = re.sub(r"^should (?:you |the |clients |servers )?", "", t)
        objectives.append({
            "text": f"Compare the trade-offs involved in {choice}",
            "bloom_level": "evaluate"
        })
        objectives.append({
            "text": f"Choose the appropriate approach for {choice} based on context",
            "bloom_level": "evaluate"
        })
    else:
        # Generic fallback
        objectives.append({
            "text": f"Explain the concept of {t}",
            "bloom_level": "understand"
        })
        objectives.append({
            "text": f"Apply the principle of {t} to a real codebase",
            "bloom_level": "apply"
        })

    # 2. Add an apply-level objective if not already present
    bloom_levels = {o["bloom_level"] for o in objectives}
    if "apply" not in bloom_levels:
        objectives.append({
            "text": f"Demonstrate this concept in a practical code example",
            "bloom_level": "apply"
        })

    # 3. Add an analyze objective for depth
    if "analyze" not in bloom_levels and len(objectives) < 4:
        objectives.append({
            "text": f"Analyze a codebase to identify where this concept applies or is violated",
            "bloom_level": "analyze"
        })

    return objectives[:5]  # cap at 5


def _generate_parent_objectives(title: str, depth: int) -> list[dict]:
    """Generate 1-3 learning objectives for a parent topic."""
    t = title.lower().strip("?").strip()
    objectives = []

    if depth == 0:
        objectives.append({
            "text": f"Explain the core concepts and importance of {t}",
            "bloom_level": "understand"
        })
        objectives.append({
            "text": f"Evaluate how {t} applies to real-world software projects",
            "bloom_level": "evaluate"
        })
        objectives.append({
            "text": f"Design solutions that incorporate principles from {t}",
            "bloom_level": "create"
        })
    else:
        objectives.append({
            "text": f"Describe the key ideas within {t}",
            "bloom_level": "understand"
        })
        objectives.append({
            "text": f"Apply the principles of {t} to solve practical problems",
            "bloom_level": "apply"
        })

    return objectives[:3]


def enrich_topic(topic: dict) -> dict:
    """Add learning_objectives (and prerequisites if depth-0) to a topic."""
    has_children = bool(topic.get("children"))
    depth = topic.get("depth", 0)
    title = topic["title"]

    # Generate learning objectives
    if has_children:
        topic["learning_objectives"] = _generate_parent_objectives(title, depth)
    else:
        topic["learning_objectives"] = _generate_leaf_objectives(title)

    # Add prerequisites for depth-0 topics
    if depth == 0:
        prereqs = PREREQUISITES.get(title, [])
        topic["prerequisites"] = prereqs

    # Recurse into children
    if has_children:
        topic["children"] = [enrich_topic(child) for child in topic["children"]]

    return topic


def main():
    input_path = Path(__file__).parent / "output" / "code" / "structure.json"
    if not input_path.exists():
        print(f"ERROR: {input_path} not found", file=sys.stderr)
        sys.exit(1)

    with open(input_path) as f:
        structure = json.load(f)

    enriched = [enrich_topic(topic) for topic in structure]

    # Stats
    total_topics = 0
    topics_with_objectives = 0
    total_objectives = 0

    def count(t):
        nonlocal total_topics, topics_with_objectives, total_objectives
        total_topics += 1
        if t.get("learning_objectives"):
            topics_with_objectives += 1
            total_objectives += len(t["learning_objectives"])
        for c in t.get("children", []):
            count(c)

    for t in enriched:
        count(t)

    print(f"Total topics: {total_topics}")
    print(f"Topics with objectives: {topics_with_objectives}")
    print(f"Total objectives: {total_objectives}")
    print(f"Depth-0 topics with prerequisites: {sum(1 for t in enriched if t.get('prerequisites'))}")

    with open(input_path, "w") as f:
        json.dump(enriched, f, indent=2, ensure_ascii=False)

    print(f"Written enriched structure to {input_path}")


if __name__ == "__main__":
    main()
