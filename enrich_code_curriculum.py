#!/usr/bin/env python3
"""
Enrich the code (Software Engineering) curriculum with learning objectives,
prerequisites, and exercises.

This is a pure data utility script -- no AI API calls. The agent (or human)
runs it to mechanically add structured metadata to structure.json and
generate exercises.json.
"""

import json
import re
import copy
from pathlib import Path

BASE = Path(__file__).parent / "output" / "code"
STRUCTURE_PATH = BASE / "structure.json"
EXERCISES_PATH = BASE / "exercises.json"

# ---------------------------------------------------------------------------
# Bloom's taxonomy action verbs by level
# ---------------------------------------------------------------------------
BLOOM_VERBS = {
    "remember": ["list", "identify", "name", "define", "recall", "recognize", "state"],
    "understand": ["explain", "describe", "summarize", "interpret", "classify", "compare", "distinguish"],
    "apply": ["implement", "use", "demonstrate", "apply", "execute", "solve", "construct"],
    "analyze": ["analyze", "differentiate", "examine", "contrast", "categorize", "deconstruct"],
    "evaluate": ["evaluate", "justify", "assess", "critique", "judge", "defend", "argue"],
    "create": ["design", "create", "propose", "formulate", "develop", "compose", "plan"],
}

# ---------------------------------------------------------------------------
# Keyword-based topic classification for objective generation
# ---------------------------------------------------------------------------

def classify_topic(title: str) -> str:
    """Classify a topic by its title to determine primary Bloom level."""
    t = title.lower()
    if any(w in t for w in ["what is", "what are", "what does", "what do", "definition"]):
        return "understand"
    if any(w in t for w in ["how do you", "how does", "how can", "how should", "implement", "build", "write", "test-drive", "refactor"]):
        return "apply"
    if any(w in t for w in ["why ", "why is", "why are", "why should", "why do", "why must", "why can"]):
        return "evaluate"
    if any(w in t for w in ["when should", "when is", "when do"]):
        return "analyze"
    if any(w in t for w in ["design", "create", "propose", "plan"]):
        return "create"
    if any(w in t for w in ["compare", "differ", "contrast", "trade-off", "vs"]):
        return "analyze"
    return "understand"


def extract_concept(title: str) -> str:
    """Extract the core concept from a topic title for use in objectives."""
    # Strip common question prefixes
    prefixes = [
        r"^What [Ii]s (the |an? )?",
        r"^What [Aa]re (the )?",
        r"^What [Dd]oes ",
        r"^What [Dd]o ",
        r"^How [Dd]o [Yy]ou ",
        r"^How [Dd]oes ",
        r"^How [Cc]an ",
        r"^How [Ss]hould ",
        r"^How [Dd]o ",
        r"^Why [Ii]s ",
        r"^Why [Aa]re ",
        r"^Why [Ss]hould ",
        r"^Why [Dd]o ",
        r"^Why [Dd]oes ",
        r"^Why [Mm]ust ",
        r"^Why [Cc]an't ",
        r"^Why [Cc]an ",
        r"^When [Ss]hould ",
        r"^When [Ii]s ",
        r"^When [Dd]o ",
        r"^Did ",
        r"^Can ",
        r"^Should ",
        r"^Is ",
        r"^Are ",
        r"^Does ",
    ]
    concept = title.rstrip("?")
    for p in prefixes:
        concept = re.sub(p, "", concept)
    return concept.strip()


def generate_objectives_for_topic(title: str, depth: int, has_children: bool) -> list:
    """Generate 1-5 learning objectives for a topic based on its title and depth."""
    bloom = classify_topic(title)
    concept = extract_concept(title)
    objectives = []

    if has_children:
        # Parent topics: 1-3 broader objectives
        objectives.append({
            "text": f"Explain the key concepts within {concept}",
            "bloom_level": "understand"
        })
        if bloom in ("apply", "create"):
            objectives.append({
                "text": f"Apply the principles of {concept} to software design problems",
                "bloom_level": "apply"
            })
        if bloom in ("evaluate", "analyze"):
            objectives.append({
                "text": f"Evaluate the trade-offs involved in {concept}",
                "bloom_level": "evaluate"
            })
        return objectives

    # Leaf topics: 2-5 objectives with progressive Bloom levels
    # Always start with a remember/understand level
    if bloom == "remember":
        objectives.append({
            "text": f"Define {concept}",
            "bloom_level": "remember"
        })
        objectives.append({
            "text": f"Explain the purpose and significance of {concept}",
            "bloom_level": "understand"
        })
    elif bloom == "understand":
        objectives.append({
            "text": f"Describe {concept} and its role in software engineering",
            "bloom_level": "understand"
        })
        objectives.append({
            "text": f"Identify examples of {concept} in real codebases",
            "bloom_level": "apply"
        })
    elif bloom == "apply":
        objectives.append({
            "text": f"Explain the purpose of {concept}",
            "bloom_level": "understand"
        })
        objectives.append({
            "text": f"Implement {concept} in a software project",
            "bloom_level": "apply"
        })
        objectives.append({
            "text": f"Evaluate when to use {concept} versus alternative approaches",
            "bloom_level": "evaluate"
        })
    elif bloom == "analyze":
        objectives.append({
            "text": f"Describe the factors involved in {concept}",
            "bloom_level": "understand"
        })
        objectives.append({
            "text": f"Analyze {concept} in the context of software design decisions",
            "bloom_level": "analyze"
        })
        objectives.append({
            "text": f"Justify decisions regarding {concept} with evidence",
            "bloom_level": "evaluate"
        })
    elif bloom == "evaluate":
        objectives.append({
            "text": f"Explain {concept}",
            "bloom_level": "understand"
        })
        objectives.append({
            "text": f"Evaluate the reasoning behind {concept}",
            "bloom_level": "evaluate"
        })
        objectives.append({
            "text": f"Argue for or against {concept} in specific scenarios",
            "bloom_level": "evaluate"
        })
    elif bloom == "create":
        objectives.append({
            "text": f"Explain the goals of {concept}",
            "bloom_level": "understand"
        })
        objectives.append({
            "text": f"Design a solution using {concept}",
            "bloom_level": "create"
        })
        objectives.append({
            "text": f"Evaluate the quality of a {concept} implementation",
            "bloom_level": "evaluate"
        })

    return objectives


# ---------------------------------------------------------------------------
# Prerequisite generation
# ---------------------------------------------------------------------------

def collect_all_titles(nodes: list, path: list = None) -> dict:
    """Build a map of title -> path for all topics."""
    if path is None:
        path = []
    result = {}
    for node in nodes:
        current_path = path + [node["title"]]
        result[node["title"]] = current_path
        if node.get("children"):
            result.update(collect_all_titles(node["children"], current_path))
    return result


# Define explicit prerequisite relationships based on curriculum structure
# Format: {topic_title: [(prerequisite_title, strength), ...]}
EXPLICIT_PREREQUISITES = {
    # SOLID depends on foundational concepts
    "What Are the SOLID Principles?": [
        ("What Is the Difference Between Design and Architecture?", "recommended"),
    ],
    "What Is the Single Responsibility Principle?": [
        ("What Are the SOLID Principles?", "required"),
    ],
    "How Does Accidental Duplication Violate the SRP?": [
        ("What Is the Single Responsibility Principle?", "required"),
    ],
    "How Do You Fix SRP Violations?": [
        ("What Is the Single Responsibility Principle?", "required"),
    ],
    "What Is the Open-Closed Principle?": [
        ("What Are the SOLID Principles?", "required"),
    ],
    "How Does the OCP Create a Protection Hierarchy?": [
        ("What Is the Open-Closed Principle?", "required"),
    ],
    "What Is the Liskov Substitution Principle?": [
        ("What Are the SOLID Principles?", "required"),
    ],
    "What Is the Interface Segregation Principle?": [
        ("What Are the SOLID Principles?", "required"),
    ],
    "What Is the Dependency Inversion Principle?": [
        ("What Are the SOLID Principles?", "required"),
    ],
    "How Do Abstract Factories Support the DIP?": [
        ("What Is the Dependency Inversion Principle?", "required"),
    ],

    # OO Design depends on paradigms
    "What Is Object-Oriented Programming Really?": [
        ("What Are the Three Programming Paradigms?", "required"),
    ],
    "Did OO Improve Encapsulation?": [
        ("What Is Object-Oriented Programming Really?", "required"),
    ],
    "Did OO Invent Inheritance?": [
        ("What Is Object-Oriented Programming Really?", "required"),
    ],
    "How Does OO Polymorphism Enable Plugin Architecture?": [
        ("What Is Object-Oriented Programming Really?", "required"),
    ],
    "What Is Dependency Inversion?": [
        ("How Does OO Polymorphism Enable Plugin Architecture?", "required"),
    ],
    "What Is Data Abstraction?": [
        ("What Are Encapsulation and Abstractions?", "required"),
    ],
    "What Is the Data/Object Anti-Symmetry?": [
        ("What Is Data Abstraction?", "required"),
    ],
    "What Is the Law of Demeter?": [
        ("What Are Encapsulation and Abstractions?", "recommended"),
    ],

    # Domain-Driven Design
    "What Is the Domain Model Pattern?": [
        ("What Are Encapsulation and Abstractions?", "recommended"),
    ],
    "What Is a Value Object?": [
        ("What Is the Domain Model Pattern?", "required"),
    ],
    "How Do Entities Differ from Value Objects?": [
        ("What Is a Value Object?", "required"),
    ],
    "What Is a Domain Service Function?": [
        ("What Is the Domain Model Pattern?", "required"),
    ],
    "What Are Aggregates and Consistency Boundaries?": [
        ("What Is the Domain Model Pattern?", "required"),
        ("How Do Entities Differ from Value Objects?", "recommended"),
    ],
    "What Is the Aggregate Pattern?": [
        ("What Are Aggregates and Consistency Boundaries?", "required"),
    ],
    "How Do You Choose the Right Aggregate?": [
        ("What Is the Aggregate Pattern?", "required"),
    ],
    "Why Should Each Repository Return Only Aggregates?": [
        ("What Is the Aggregate Pattern?", "required"),
        ("What Is the Repository Pattern?", "required"),
    ],
    "How Do Version Numbers Enforce Aggregate Consistency?": [
        ("What Is the Aggregate Pattern?", "required"),
    ],
    "When Should You Choose Optimistic vs Pessimistic Locking?": [
        ("How Do Version Numbers Enforce Aggregate Consistency?", "required"),
    ],

    # Architecture Patterns
    "What Is the Repository Pattern?": [
        ("What Is the Dependency Inversion Principle?", "recommended"),
    ],
    "How Does Dependency Inversion Apply to Data Access?": [
        ("What Is the Dependency Inversion Principle?", "required"),
        ("What Is the Repository Pattern?", "required"),
    ],
    "How Do You Build a Fake Repository for Tests?": [
        ("What Is the Repository Pattern?", "required"),
    ],
    "What Is the Service Layer Pattern?": [
        ("What Is the Repository Pattern?", "required"),
    ],
    "What Is the Difference Between Application Services and Domain Services?": [
        ("What Is the Service Layer Pattern?", "required"),
        ("What Is a Domain Service Function?", "recommended"),
    ],
    "What Is the Unit of Work Pattern?": [
        ("What Is the Repository Pattern?", "required"),
        ("What Is the Service Layer Pattern?", "recommended"),
    ],
    "How Do You Build a Fake Unit of Work for Tests?": [
        ("What Is the Unit of Work Pattern?", "required"),
    ],
    "How Does UoW Handle Commit and Rollback?": [
        ("What Is the Unit of Work Pattern?", "required"),
    ],

    # Dependency Injection
    "Dependency Injection and Bootstrapping": [
        ("What Is the Unit of Work Pattern?", "recommended"),
        ("What Is the Service Layer Pattern?", "recommended"),
    ],
    "The Bootstrap Script Pattern": [
        ("Dependency Injection and Bootstrapping", "required"),
    ],
    "Class-Based Message Bus with DI": [
        ("The Bootstrap Script Pattern", "required"),
    ],

    # Testing
    "What Is the Core Rhythm of Test-Driven Development?": [
        ("Why Is Software Testing Like Science?", "recommended"),
    ],
    "How Does TDD Manage Fear in Programming?": [
        ("What Is the Core Rhythm of Test-Driven Development?", "required"),
    ],
    "How Does a Complete TDD Cycle Work in Practice?": [
        ("What Is the Core Rhythm of Test-Driven Development?", "required"),
    ],
    "The Three Laws of TDD": [
        ("What Is the Core Rhythm of Test-Driven Development?", "required"),
    ],
    "Keeping Tests Clean": [
        ("The Three Laws of TDD", "required"),
    ],
    "Writing Clean Tests": [
        ("Keeping Tests Clean", "required"),
    ],
    "F.I.R.S.T. -- Five Rules for Clean Tests": [
        ("Writing Clean Tests", "recommended"),
    ],
    "What Are High Gear and Low Gear in TDD?": [
        ("What Is the Core Rhythm of Test-Driven Development?", "required"),
    ],
    "What Does a Healthy Test Pyramid Look Like?": [
        ("What Are High Gear and Low Gear in TDD?", "recommended"),
    ],

    # Event-Driven Architecture
    "Why Do Side Effects Like Email Notifications Create Architectural Problems?": [
        ("What Is the Service Layer Pattern?", "required"),
    ],
    "How Do You Define and Raise Domain Events?": [
        ("Why Do Side Effects Like Email Notifications Create Architectural Problems?", "required"),
    ],
    "What Is a Message Bus and How Does It Route Events?": [
        ("How Do You Define and Raise Domain Events?", "required"),
    ],
    "Commands vs Events": [
        ("What Is a Message Bus and How Does It Route Events?", "required"),
    ],
    "CQRS: Command-Query Responsibility Segregation": [
        ("Commands vs Events", "required"),
    ],
    "Denormalized Read Models": [
        ("CQRS: Command-Query Responsibility Segregation", "required"),
    ],
    "What Is CQRS and Where Did It Come From?": [
        ("CQRS: Command-Query Responsibility Segregation", "recommended"),
    ],
    "What Is Event Sourcing and Why Store Events Instead of State?": [
        ("What Is CQRS and Where Did It Come From?", "required"),
    ],

    # Clean Architecture
    "What is the Clean Architecture?": [
        ("What Is the Dependency Inversion Principle?", "required"),
        ("What Is Software Architecture?", "recommended"),
    ],
    "What is the Dependency Rule and what are the Clean Architecture layers?": [
        ("What is the Clean Architecture?", "required"),
    ],
    "What is Screaming Architecture?": [
        ("What is the Clean Architecture?", "recommended"),
    ],
    "What is an Entity in Clean Architecture?": [
        ("What is the Clean Architecture?", "required"),
    ],

    # Clean Code
    "Why Should Names Reveal Intent?": [
        ("What Is Clean Code?", "recommended"),
    ],
    "How Small Should Functions Be?": [
        ("What Is Clean Code?", "recommended"),
    ],
    "What Is the DRY Principle?": [
        ("How Small Should Functions Be?", "recommended"),
    ],
    "Why Prefer Exceptions Over Error Codes?": [
        ("What Are Side Effects in Functions?", "recommended"),
    ],

    # Component principles depend on SOLID
    "What Are Software Components?": [
        ("What Are the SOLID Principles?", "required"),
    ],
    "What Is the Reuse/Release Equivalence Principle?": [
        ("What Are Software Components?", "required"),
    ],
    "What Is the Common Closure Principle?": [
        ("What Are Software Components?", "required"),
        ("What Is the Single Responsibility Principle?", "recommended"),
    ],
    "What Is the Common Reuse Principle?": [
        ("What Are Software Components?", "required"),
    ],
    "What Is the Acyclic Dependencies Principle?": [
        ("What Are Software Components?", "required"),
    ],
    "What Is the Stable Abstractions Principle?": [
        ("What Are Software Components?", "required"),
        ("What Is the Dependency Inversion Principle?", "recommended"),
    ],

    # Functional programming
    "What Is Immutability and Why Does It Matter for Architecture?": [
        ("What Are the Three Programming Paradigms?", "required"),
    ],
    "How Should You Segregate Mutable and Immutable Components?": [
        ("What Is Immutability and Why Does It Matter for Architecture?", "required"),
    ],
    "What Is Event Sourcing?": [
        ("How Should You Segregate Mutable and Immutable Components?", "recommended"),
    ],

    # Concurrency
    "Why Is a Simple Increment Not Thread-Safe?": [
        ("Why Should You Care About Concurrency?", "required"),
    ],
    "What Are the Three Classic Concurrency Problems?": [
        ("Why Is a Simple Increment Not Thread-Safe?", "required"),
    ],
    "What Causes Deadlock and How Do You Prevent It?": [
        ("What Are the Three Classic Concurrency Problems?", "required"),
    ],
    "How Do You Break Each Deadlock Condition?": [
        ("What Causes Deadlock and How Do You Prevent It?", "required"),
    ],

    # SRE foundations
    "How does SRE relate to DevOps?": [
        ("Why does operations need reinvention?", "recommended"),
    ],
    "Why does SRE reject 100% availability?": [
        ("How does SRE relate to DevOps?", "recommended"),
    ],
    "What is an error budget and why is 100% the wrong target?": [
        ("Why does SRE reject 100% availability?", "required"),
    ],
    "Why are SLOs the foundation of SRE practice?": [
        ("What is an error budget and why is 100% the wrong target?", "required"),
    ],
    "How do you define and measure Service Level Indicators?": [
        ("Why are SLOs the foundation of SRE practice?", "required"),
    ],
    "What is an error budget policy and why do you need one?": [
        ("What is an error budget and why is 100% the wrong target?", "required"),
    ],
    "How do error budgets drive engineering decisions?": [
        ("What is an error budget policy and why do you need one?", "required"),
    ],
    "What is burn rate alerting and how does it improve SLO-based alerts?": [
        ("How do you turn SLOs into actionable alerts?", "required"),
    ],
    "What is toil and why must SREs minimize it?": [
        ("How does SRE relate to DevOps?", "required"),
    ],
    "What is incident response and how should it be structured?": [
        ("Why are SLOs the foundation of SRE practice?", "recommended"),
    ],
    "What is a blameless postmortem culture and why does it matter?": [
        ("What is incident response and how should it be structured?", "required"),
    ],

    # Microservices
    "Events for Microservice Integration": [
        ("What Is a Message Bus and How Does It Route Events?", "recommended"),
    ],
    "Distributed Ball of Mud": [
        ("Events for Microservice Integration", "recommended"),
    ],
    "Temporal Coupling in Distributed Systems": [
        ("Events for Microservice Integration", "required"),
    ],

    # Legacy code
    "What is the Strangler Fig pattern for migrating to microservices?": [
        ("How do you start separating responsibilities in a legacy codebase?", "required"),
    ],
}


def generate_prerequisites(title: str, all_titles: dict) -> list:
    """Generate prerequisites for a topic based on explicit mappings."""
    if title not in EXPLICIT_PREREQUISITES:
        return []
    prereqs = []
    for prereq_title, strength in EXPLICIT_PREREQUISITES[title]:
        if prereq_title in all_titles:
            prereqs.append({
                "topic": prereq_title,
                "strength": strength
            })
    return prereqs


# ---------------------------------------------------------------------------
# Enrich structure
# ---------------------------------------------------------------------------

def enrich_node(node: dict, all_titles: dict) -> dict:
    """Add learning_objectives and prerequisites to a single node."""
    title = node["title"]
    has_children = bool(node.get("children"))
    depth = node["depth"]

    # Generate learning objectives
    node["learning_objectives"] = generate_objectives_for_topic(title, depth, has_children)

    # Generate prerequisites
    prereqs = generate_prerequisites(title, all_titles)
    if prereqs:
        node["prerequisites"] = prereqs

    # Recurse into children
    if has_children:
        for child in node["children"]:
            enrich_node(child, all_titles)

    return node


# ---------------------------------------------------------------------------
# Exercise generation
# ---------------------------------------------------------------------------

def build_topic_path(nodes: list, path: list = None) -> list:
    """Flatten hierarchy into (path, title, depth, bloom) tuples."""
    if path is None:
        path = []
    result = []
    for node in nodes:
        current_path = path + [node["title"]]
        bloom = classify_topic(node["title"])
        result.append((current_path, node["title"], node["depth"], bloom, bool(node.get("children"))))
        if node.get("children"):
            result.extend(build_topic_path(node["children"], current_path))
    return result


# Define exercises for key topics across all depth-0 groups
EXERCISE_DATA = [
    # ---- Why Software Design Matters ----
    {
        "topic_path": ["Why Software Design Matters", "What Is the Difference Between Design and Architecture?"],
        "exercises": [{
            "title": "Design vs Architecture Classification",
            "problem_statement": "You are given descriptions of five software decisions: (1) choosing between microservices and a monolith, (2) naming a variable, (3) deciding which cloud provider to use, (4) extracting a method from a long function, (5) defining API boundaries between teams. Classify each as primarily an 'architecture' decision or a 'design' decision, and explain your reasoning.",
            "hints": ["Architecture decisions are high-level and hard to reverse", "Design decisions are low-level and local in scope", "Consider the blast radius of changing each decision"],
            "expected_solution": "(1) Architecture - affects overall system structure and is costly to change. (2) Design - local code-level decision. (3) Architecture - infrastructure choice affecting deployment and operations. (4) Design - local refactoring within a module. (5) Architecture - defines team boundaries and communication contracts. The key distinction is that architecture decisions shape the overall system structure and are expensive to change, while design decisions are localized and reversible.",
            "common_mistakes": ["Thinking all technical decisions are architecture", "Confusing the scale of impact with the type of decision"],
            "bloom_level": "analyze",
            "difficulty": 1
        }]
    },
    {
        "topic_path": ["Why Software Design Matters", "What Are the Two Values of Software?"],
        "exercises": [{
            "title": "Behavior vs Structure Priority",
            "problem_statement": "A product manager asks you to add a new payment method to an e-commerce system. The current codebase has hardcoded payment logic scattered across 15 files with no abstraction layer. You estimate: adding the feature with copy-paste takes 2 days; refactoring to a plugin architecture first takes 5 days but makes future payment methods take 1 day each. Six more payment methods are planned for the next year. Which approach do you recommend, and how do you frame the argument in terms of the two values of software?",
            "hints": ["The two values are behavior (what it does now) and structure (how easy it is to change)", "Calculate total effort for both approaches over the year", "Consider which value degrades faster when neglected"],
            "expected_solution": "Recommend the refactoring approach. Copy-paste: 2 + (6 x 2) = 14 days total. Refactoring: 5 + (6 x 1) = 11 days total, plus each addition is less risky. The structure (architecture) value is more important because software that is hard to change eventually becomes impossible to change. The behavior value is immediately visible but temporary; the structure value compounds over time.",
            "common_mistakes": ["Only considering immediate delivery pressure", "Ignoring the compounding cost of structural debt"],
            "bloom_level": "evaluate",
            "difficulty": 2
        }]
    },
    {
        "topic_path": ["Why Software Design Matters", "What Is Clean Code?"],
        "exercises": [{
            "title": "Clean Code Evaluation",
            "problem_statement": "Review the following pseudocode and identify at least three clean code violations. Then rewrite it to be clean.\n\n```\ndef proc(lst):\n    r = []\n    for i in range(len(lst)):\n        if lst[i].s == 1:\n            lst[i].p = lst[i].p * 0.9\n            r.append(lst[i])\n        elif lst[i].s == 2:\n            lst[i].p = lst[i].p * 0.8\n            r.append(lst[i])\n    return r\n```",
            "hints": ["Look at naming: what do proc, lst, r, s, p mean?", "Look at the magic numbers: what do 1, 2, 0.9, 0.8 represent?", "Is the function doing more than one thing?"],
            "expected_solution": "Violations: (1) Non-descriptive names (proc, lst, r, s, p). (2) Magic numbers (1, 2, 0.9, 0.8). (3) Function does two things (filters and transforms). (4) Uses index-based iteration when direct iteration would be clearer. Clean version:\n```\ndef apply_membership_discounts(products):\n    DISCOUNT_BY_TIER = {SILVER: 0.10, GOLD: 0.20}\n    discounted = []\n    for product in products:\n        if product.membership_tier in DISCOUNT_BY_TIER:\n            product.price *= (1 - DISCOUNT_BY_TIER[product.membership_tier])\n            discounted.append(product)\n    return discounted\n```",
            "common_mistakes": ["Only fixing names but keeping magic numbers", "Not separating filtering from transformation"],
            "bloom_level": "apply",
            "difficulty": 1
        }]
    },

    # ---- SOLID Principles ----
    {
        "topic_path": ["Why Software Design Matters", "What Is the Single Responsibility Principle?"],
        "exercises": [{
            "title": "SRP Violation Detection",
            "problem_statement": "A `UserService` class has the following methods: `authenticate(username, password)`, `sendWelcomeEmail(user)`, `generateMonthlyReport(users)`, `saveToDatabase(user)`, `validatePassword(password)`. Identify which groups of methods represent separate responsibilities, name the stakeholders who would request changes to each, and propose a refactored class structure.",
            "hints": ["Ask: who would request a change to each method?", "Group methods by their reason for change", "Each responsibility should map to a different stakeholder or concern"],
            "expected_solution": "Four responsibilities: (1) Authentication (authenticate, validatePassword) - Security team. (2) Notifications (sendWelcomeEmail) - Marketing/UX team. (3) Reporting (generateMonthlyReport) - Business/Analytics team. (4) Persistence (saveToDatabase) - Infrastructure team. Refactored: AuthenticationService, EmailNotifier, UserReportGenerator, UserRepository. Each class has one reason to change and one stakeholder driving changes.",
            "common_mistakes": ["Grouping by 'they all deal with users' instead of by reason for change", "Creating too many classes (one per method) instead of grouping by responsibility"],
            "bloom_level": "analyze",
            "difficulty": 2
        }]
    },
    {
        "topic_path": ["Why Software Design Matters", "What Is the Open-Closed Principle?"],
        "exercises": [{
            "title": "OCP Refactoring",
            "problem_statement": "The following function calculates shipping cost and violates OCP because adding a new shipping method requires modifying it:\n\n```python\ndef calculate_shipping(order, method):\n    if method == 'standard':\n        return order.weight * 0.5\n    elif method == 'express':\n        return order.weight * 1.5 + 10\n    elif method == 'overnight':\n        return order.weight * 3.0 + 25\n```\n\nRefactor this to conform to the Open-Closed Principle so that new shipping methods can be added without modifying existing code.",
            "hints": ["Use polymorphism or a strategy pattern", "Define an abstract interface for shipping calculators", "Each shipping method becomes its own class"],
            "expected_solution": "```python\nfrom abc import ABC, abstractmethod\n\nclass ShippingCalculator(ABC):\n    @abstractmethod\n    def calculate(self, order) -> float: ...\n\nclass StandardShipping(ShippingCalculator):\n    def calculate(self, order): return order.weight * 0.5\n\nclass ExpressShipping(ShippingCalculator):\n    def calculate(self, order): return order.weight * 1.5 + 10\n\nclass OvernightShipping(ShippingCalculator):\n    def calculate(self, order): return order.weight * 3.0 + 25\n\ndef calculate_shipping(order, calculator: ShippingCalculator):\n    return calculator.calculate(order)\n```\nNow adding 'DronDelivery' just means creating a new class -- no existing code changes.",
            "common_mistakes": ["Using a dictionary mapping instead of polymorphism (partial fix, not truly OCP)", "Forgetting to define a clear interface/contract"],
            "bloom_level": "apply",
            "difficulty": 2
        }]
    },
    {
        "topic_path": ["Why Software Design Matters", "What Is the Liskov Substitution Principle?"],
        "exercises": [{
            "title": "LSP Violation Analysis",
            "problem_statement": "A Rectangle class has setWidth() and setHeight() methods that set dimensions independently. A Square subclass overrides both so that setting either dimension sets both (maintaining the square invariant). A function resizeAndCheck(rect) calls rect.setWidth(5) then rect.setHeight(4) and asserts area == 20. Explain why passing a Square violates LSP, and propose a design that avoids this violation.",
            "hints": ["What postcondition does Rectangle.setWidth() establish?", "Does Square.setWidth() honor that postcondition?", "Think about whether Square IS-A Rectangle in terms of behavior"],
            "expected_solution": "Rectangle.setWidth(5) has the postcondition that width==5 AND height is unchanged. Square.setWidth(5) also sets height to 5, violating this postcondition. The assertion fails because area becomes 5*5=25, not 5*4=20. Solution: Do not make Square extend Rectangle. Instead, create a Shape interface with an area() method, and implement Rectangle and Square as independent classes. The behavioral contract (width and height are independent) is what matters for LSP, not the mathematical IS-A relationship.",
            "common_mistakes": ["Thinking the problem is in the test rather than the hierarchy", "Trying to fix it by adding type checks (which violates OCP)"],
            "bloom_level": "analyze",
            "difficulty": 2
        }]
    },
    {
        "topic_path": ["Why Software Design Matters", "What Is the Dependency Inversion Principle?"],
        "exercises": [{
            "title": "DIP Application",
            "problem_statement": "A NotificationService directly instantiates an SmtpEmailSender to send emails. The team wants to add SMS notifications and also test the service without sending real emails. Apply the Dependency Inversion Principle to refactor this design. Show the interface, two implementations, and how NotificationService uses them.",
            "hints": ["High-level modules should not depend on low-level modules", "Both should depend on abstractions", "Think about what NotificationService actually needs from a sender"],
            "expected_solution": "Define an abstract MessageSender interface with a send(recipient, message) method. SmtpEmailSender and SmsSender both implement MessageSender. NotificationService depends on MessageSender (the abstraction), not on any concrete sender. For testing, create a FakeMessageSender that records calls. The dependency flows from concrete implementations toward the abstraction, which lives in the same layer as NotificationService.",
            "common_mistakes": ["Putting the interface in the infrastructure layer instead of the domain/service layer", "Creating the abstraction but still instantiating the concrete class inside NotificationService"],
            "bloom_level": "apply",
            "difficulty": 2
        }]
    },

    # ---- Domain-Driven Design ----
    {
        "topic_path": ["Domain-Driven Design", "Domain Modeling", "What Is a Value Object?"],
        "exercises": [{
            "title": "Value Object Design",
            "problem_statement": "In an e-commerce system, you have a Money concept used for prices and totals. Design a Money value object class. It should: (1) be immutable, (2) support equality by value, (3) support addition of two Money objects in the same currency, (4) raise an error when adding different currencies. Show the class definition and a usage example.",
            "hints": ["Value objects have no identity -- they are defined by their attributes", "Immutability means operations return new objects", "Equality is based on attribute values, not memory reference"],
            "expected_solution": "```python\nfrom dataclasses import dataclass\n\n@dataclass(frozen=True)\nclass Money:\n    amount: float\n    currency: str\n\n    def __add__(self, other):\n        if not isinstance(other, Money):\n            return NotImplemented\n        if self.currency != other.currency:\n            raise ValueError(f'Cannot add {self.currency} and {other.currency}')\n        return Money(self.amount + other.amount, self.currency)\n\nprice = Money(10.00, 'USD')\ntax = Money(0.80, 'USD')\ntotal = price + tax  # Money(10.80, 'USD')\nassert total == Money(10.80, 'USD')  # True, equality by value\n```",
            "common_mistakes": ["Making Money mutable (setters on amount)", "Comparing by identity instead of by value"],
            "bloom_level": "apply",
            "difficulty": 2
        }]
    },
    {
        "topic_path": ["Domain-Driven Design", "Aggregates", "What Is the Aggregate Pattern?"],
        "exercises": [{
            "title": "Aggregate Boundary Design",
            "problem_statement": "An order management system has Orders, OrderLines, and Products. An order can have up to 50 lines. Each line references a product and has a quantity. Business rules: (1) total order value cannot exceed $10,000, (2) each line quantity must be positive, (3) the same product cannot appear twice in an order. Define the aggregate boundary and explain which entity is the aggregate root and why.",
            "hints": ["The aggregate root is the entry point for all modifications", "Invariants that span multiple entities define the boundary", "Consider which entity 'owns' the consistency rules"],
            "expected_solution": "Order is the aggregate root. OrderLines are entities within the aggregate. Products are outside the aggregate (referenced by ID only). Reasoning: (1) The $10,000 limit spans all lines -- only Order can enforce it. (2) Quantity positivity can be checked per-line but is part of Order's responsibility during add/update. (3) The no-duplicate-product rule requires knowledge of all lines. All three invariants require access to the full set of OrderLines, so they must be inside the same aggregate with Order as root. Products are separate aggregates because they have independent lifecycles.",
            "common_mistakes": ["Including Product as part of the Order aggregate (it has its own lifecycle)", "Making OrderLine the root (it cannot enforce cross-line invariants)"],
            "bloom_level": "analyze",
            "difficulty": 3
        }]
    },

    # ---- Architecture Patterns ----
    {
        "topic_path": ["Architecture Patterns", "Repository Pattern", "What Is the Repository Pattern?"],
        "exercises": [{
            "title": "Repository Implementation",
            "problem_statement": "Define an abstract repository interface for a Product aggregate with methods: add(product), get(product_id), and list_by_category(category). Then implement a FakeProductRepository (in-memory, for tests) and explain how this enables testing the service layer without a database.",
            "hints": ["The interface should use domain types, not database types", "The fake stores data in a simple data structure like a list or dictionary", "The service layer depends on the abstract interface, not the concrete implementation"],
            "expected_solution": "```python\nfrom abc import ABC, abstractmethod\n\nclass AbstractProductRepository(ABC):\n    @abstractmethod\n    def add(self, product: Product): ...\n    @abstractmethod\n    def get(self, product_id: str) -> Product: ...\n    @abstractmethod\n    def list_by_category(self, category: str) -> list[Product]: ...\n\nclass FakeProductRepository(AbstractProductRepository):\n    def __init__(self):\n        self._products = []\n    def add(self, product):\n        self._products.append(product)\n    def get(self, product_id):\n        return next(p for p in self._products if p.id == product_id)\n    def list_by_category(self, category):\n        return [p for p in self._products if p.category == category]\n```\nThe service layer injects AbstractProductRepository. In production, it gets a SqlAlchemyProductRepository. In tests, it gets FakeProductRepository. No database setup needed for unit tests.",
            "common_mistakes": ["Leaking SQL concepts into the abstract interface", "Making the fake too smart (adding query logic that mirrors the ORM)"],
            "bloom_level": "apply",
            "difficulty": 2
        }]
    },
    {
        "topic_path": ["Architecture Patterns", "Unit of Work", "What Is the Unit of Work Pattern?"],
        "exercises": [{
            "title": "Unit of Work Transaction Scenario",
            "problem_statement": "A service function must: (1) deduct inventory for product A, (2) create an order record, (3) update the customer's loyalty points. If step 3 fails, steps 1 and 2 must be rolled back. Explain how the Unit of Work pattern ensures this atomicity. Write pseudocode showing the UoW as a context manager.",
            "hints": ["The UoW wraps a database transaction", "All three operations happen within a single UoW context", "The UoW commits only if all operations succeed; otherwise it rolls back"],
            "expected_solution": "```python\ndef place_order(uow: AbstractUnitOfWork, product_id, customer_id, qty):\n    with uow:\n        product = uow.products.get(product_id)\n        product.deduct_inventory(qty)\n        order = Order(customer_id=customer_id, product_id=product_id, qty=qty)\n        uow.orders.add(order)\n        customer = uow.customers.get(customer_id)\n        customer.add_loyalty_points(qty * 10)\n        uow.commit()\n```\nIf customer.add_loyalty_points raises an exception, the context manager's __exit__ calls rollback(), undoing the inventory deduction and order creation. The UoW ensures all-or-nothing semantics by wrapping all repository operations in a single database transaction.",
            "common_mistakes": ["Committing after each operation instead of once at the end", "Not implementing rollback in the context manager exit"],
            "bloom_level": "apply",
            "difficulty": 2
        }]
    },

    # ---- Testing and TDD ----
    {
        "topic_path": ["Testing and TDD", "TDD Foundations", "What Is the Core Rhythm of Test-Driven Development?"],
        "exercises": [{
            "title": "Red-Green-Refactor Practice",
            "problem_statement": "Using TDD, implement a function `fizzbuzz(n)` that returns 'Fizz' for multiples of 3, 'Buzz' for multiples of 5, 'FizzBuzz' for multiples of both, and the number as a string otherwise. Show your test list, then demonstrate at least 3 Red-Green-Refactor cycles with the test you wrote first, the minimal code to pass it, and any refactoring.",
            "hints": ["Start with the simplest case first", "Write only enough code to pass the current failing test", "Refactor only when tests are green"],
            "expected_solution": "Test list: [returns '1' for 1, returns '2' for 2, returns 'Fizz' for 3, returns 'Buzz' for 5, returns 'FizzBuzz' for 15, returns 'Fizz' for 6].\n\nCycle 1 (Red): test fizzbuzz(1) == '1'. Green: def fizzbuzz(n): return str(n). Refactor: none.\nCycle 2 (Red): test fizzbuzz(3) == 'Fizz'. Green: def fizzbuzz(n): if n % 3 == 0: return 'Fizz'; return str(n). Refactor: none.\nCycle 3 (Red): test fizzbuzz(5) == 'Buzz'. Green: add elif n % 5 == 0: return 'Buzz'. Refactor: none.\nCycle 4 (Red): test fizzbuzz(15) == 'FizzBuzz'. Green: add if n % 15 == 0: return 'FizzBuzz' at the top. Refactor: reorder conditions so 15 check comes first.",
            "common_mistakes": ["Writing all tests at once before any implementation", "Implementing the full solution before writing any test", "Skipping the refactor step"],
            "bloom_level": "apply",
            "difficulty": 1
        }]
    },
    {
        "topic_path": ["Testing and TDD", "Clean Testing Practices", "F.I.R.S.T. -- Five Rules for Clean Tests"],
        "exercises": [{
            "title": "FIRST Principle Evaluation",
            "problem_statement": "Evaluate the following test against each of the F.I.R.S.T. principles (Fast, Independent, Repeatable, Self-validating, Timely). Identify which principles it violates and how to fix each violation.\n\n```python\ndef test_user_creation():\n    db = connect_to_production_database()\n    db.execute('DELETE FROM users WHERE email = \"test@test.com\"')\n    user = create_user(db, 'test@test.com')\n    assert user is not None\n    print(f'User created: {user.id}')  # manual check\n    other_user = create_user(db, 'other@test.com')  # depends on this test running\n```",
            "hints": ["Fast: does it use a real database?", "Independent: does the second creation depend on the first?", "Repeatable: would it work on a developer's laptop?", "Self-validating: does 'print' constitute a pass/fail?", "Timely: is the test written before or after the code?"],
            "expected_solution": "Violations: (1) Fast -- connects to a real production database, which is slow. Fix: use an in-memory database or fake repository. (2) Independent -- the second user creation depends on the first test's state. Fix: each test should set up its own state. (3) Repeatable -- uses production database, so results depend on existing data. Fix: use isolated test fixtures. (4) Self-validating -- uses print for manual checking instead of assertions. Fix: replace print with assert user.id is not None. (5) Timely -- cannot assess from the code alone, but the pattern suggests tests were added after implementation.",
            "common_mistakes": ["Thinking 'it has an assert so it is self-validating' while ignoring the print", "Not recognizing the production database as a repeatability problem"],
            "bloom_level": "evaluate",
            "difficulty": 2
        }]
    },
    {
        "topic_path": ["Testing and TDD", "Testing Architecture Patterns", "What Does a Healthy Test Pyramid Look Like?"],
        "exercises": [{
            "title": "Test Pyramid Design",
            "problem_statement": "A team has 5 unit tests, 50 integration tests, and 200 end-to-end tests. Their CI pipeline takes 45 minutes. Diagnose what is wrong with their test distribution using the test pyramid model. Propose a target distribution and explain what tests to add or remove.",
            "hints": ["The test pyramid has many unit tests at the base, fewer integration tests, and very few E2E tests", "E2E tests are slow, brittle, and expensive to maintain", "Most business logic should be testable at the unit level"],
            "expected_solution": "The distribution is inverted (ice cream cone anti-pattern). The healthy pyramid should be approximately: 70% unit tests, 20% integration tests, 10% E2E tests. Target: 200+ unit tests, 30-50 integration tests, 10-20 E2E tests. Steps: (1) Identify business logic currently tested only through E2E and extract it to testable units. (2) Replace E2E tests that verify logic with unit tests. (3) Keep E2E tests only for critical user journeys. (4) Integration tests should verify adapter/boundary code only. This should reduce CI time from 45 minutes to under 10.",
            "common_mistakes": ["Thinking you can just delete E2E tests without adding equivalent unit coverage", "Keeping integration tests that duplicate unit test coverage"],
            "bloom_level": "evaluate",
            "difficulty": 2
        }]
    },

    # ---- Event-Driven Architecture ----
    {
        "topic_path": ["Event-Driven Architecture", "Commands and Events", "Commands vs Events"],
        "exercises": [{
            "title": "Command vs Event Classification",
            "problem_statement": "Classify each of the following as either a Command or an Event, and explain why:\n1. PlaceOrder(customer_id, items)\n2. OrderPlaced(order_id, timestamp)\n3. UpdateInventory(product_id, new_quantity)\n4. PaymentReceived(order_id, amount)\n5. SendShippingNotification(order_id, email)\n6. InventoryDepleted(product_id)",
            "hints": ["Commands express intent -- they ask something to happen", "Events record something that already happened", "Commands are imperative; events are past tense"],
            "expected_solution": "1. Command -- asks the system to create an order (imperative, directed at one handler). 2. Event -- records that an order was placed (past tense, broadcast to many listeners). 3. Command -- asks to update inventory (imperative). 4. Event -- records that payment was received (past tense). 5. Command -- asks to send a notification (imperative). 6. Event -- records that inventory was depleted (past tense). Key differences: Commands have one handler and can be rejected. Events have zero or more handlers and represent facts.",
            "common_mistakes": ["Confusing 'Update' commands with events because they describe state changes", "Thinking events can be rejected or fail"],
            "bloom_level": "understand",
            "difficulty": 1
        }]
    },
    {
        "topic_path": ["Event-Driven Architecture", "CQRS", "CQRS: Command-Query Responsibility Segregation"],
        "exercises": [{
            "title": "CQRS Architecture Design",
            "problem_statement": "An e-commerce dashboard needs to display: (1) a list of recent orders with customer names and total amounts, (2) allow placing new orders with inventory checks. The current system uses the same ORM models for both reads and writes, causing slow dashboard loads due to complex joins. Design a CQRS solution: describe the write side (command model), read side (query model), and how they synchronize.",
            "hints": ["The write side uses the rich domain model with aggregates", "The read side can use denormalized views optimized for the specific query", "Events synchronize the write side to the read side"],
            "expected_solution": "Write side: PlaceOrder command is handled by the Order aggregate which checks inventory via the domain model. Repository persists the aggregate and raises an OrderPlaced event. Read side: A denormalized orders_view table with columns (order_id, customer_name, total_amount, placed_at) -- no joins needed. Sync: When OrderPlaced event fires, an event handler updates the orders_view table by inserting a row with pre-computed data. The dashboard queries orders_view directly with raw SQL, bypassing the ORM entirely. This separates the complex domain logic (writes) from the simple query needs (reads).",
            "common_mistakes": ["Using the same database model for both sides", "Forgetting that the read model needs to be updated asynchronously via events"],
            "bloom_level": "create",
            "difficulty": 3
        }]
    },

    # ---- Clean Code Fundamentals ----
    {
        "topic_path": ["Clean Code Fundamentals", "Naming", "Why Should Names Reveal Intent?"],
        "exercises": [{
            "title": "Intent-Revealing Names",
            "problem_statement": "Rename the variables and function in this code to reveal intent:\n\n```python\ndef f(l):\n    r = []\n    for x in l:\n        if x[0] == 4:\n            r.append(x)\n    return r\n```\n\nAssume this is filtering a list of cells in a minesweeper game, where index 0 is the status and 4 means 'flagged'.",
            "hints": ["What does 'l' represent? What are the elements?", "What does x[0] == 4 mean in the domain?", "What is the function's purpose?"],
            "expected_solution": "```python\ndef get_flagged_cells(game_board):\n    FLAGGED = 4\n    STATUS_INDEX = 0\n    flagged_cells = []\n    for cell in game_board:\n        if cell[STATUS_INDEX] == FLAGGED:\n            flagged_cells.append(cell)\n    return flagged_cells\n```\nEven better with a Cell class: cell.is_flagged instead of cell[0] == 4.",
            "common_mistakes": ["Making names too long without adding clarity", "Keeping magic numbers while renaming variables"],
            "bloom_level": "apply",
            "difficulty": 1
        }]
    },
    {
        "topic_path": ["Clean Code Fundamentals", "Functions", "How Small Should Functions Be?"],
        "exercises": [{
            "title": "Function Extraction",
            "problem_statement": "This function processes an order and does too many things. Extract it into smaller functions, each doing one thing:\n\n```python\ndef process_order(order):\n    # validate\n    if not order.items:\n        raise ValueError('Empty order')\n    if order.total > 10000:\n        raise ValueError('Order exceeds limit')\n    # calculate discount\n    if order.customer.is_premium:\n        order.total *= 0.9\n    # save\n    db.save(order)\n    # notify\n    email.send(order.customer.email, f'Order {order.id} confirmed')\n    return order\n```",
            "hints": ["Each comment block suggests a separate responsibility", "A function should do one thing at one level of abstraction", "The refactored process_order should read like a series of steps"],
            "expected_solution": "```python\ndef process_order(order):\n    validate_order(order)\n    apply_discounts(order)\n    save_order(order)\n    notify_customer(order)\n    return order\n\ndef validate_order(order):\n    if not order.items:\n        raise ValueError('Empty order')\n    if order.total > 10000:\n        raise ValueError('Order exceeds limit')\n\ndef apply_discounts(order):\n    if order.customer.is_premium:\n        order.total *= 0.9\n\ndef save_order(order):\n    db.save(order)\n\ndef notify_customer(order):\n    email.send(order.customer.email, f'Order {order.id} confirmed')\n```\nprocess_order now reads as a high-level description of the workflow.",
            "common_mistakes": ["Extracting too little (one function per line)", "Keeping mixed abstraction levels in the main function"],
            "bloom_level": "apply",
            "difficulty": 1
        }]
    },
    {
        "topic_path": ["Clean Code Fundamentals", "Comments", "Are Comments Good or Bad?"],
        "exercises": [{
            "title": "Comment Necessity Analysis",
            "problem_statement": "For each comment below, classify it as 'necessary' or 'unnecessary' and explain why. If unnecessary, show how to eliminate it by improving the code.\n\n1. `# Check if user is admin`  followed by  `if user.role == 'admin':`\n2. `# Format: YYYY-MM-DD`  above a regex  `r'\\d{4}-\\d{2}-\\d{2}'`\n3. `# TODO: handle edge case when list is empty`\n4. `# increment counter`  followed by  `counter += 1`\n5. `# WARNING: this function is not thread-safe`",
            "hints": ["A comment is unnecessary if the code already says the same thing", "A comment is necessary if it explains WHY or warns about non-obvious behavior", "TODOs can be necessary if they mark genuine incomplete work"],
            "expected_solution": "(1) Unnecessary -- the code already says it (rename to `if user.is_admin:`). (2) Necessary -- the regex alone does not communicate the date format expectation, though better as a named constant like DATE_PATTERN. (3) Necessary -- marks genuine incomplete work. (4) Unnecessary -- the code is self-explanatory. (5) Necessary -- warns about non-obvious behavior that could cause bugs in concurrent code.",
            "common_mistakes": ["Thinking all comments are bad", "Not recognizing that 'why' comments and warnings are valuable"],
            "bloom_level": "evaluate",
            "difficulty": 1
        }]
    },
    {
        "topic_path": ["Clean Code Fundamentals", "Error Handling", "Don't Return Null"],
        "exercises": [{
            "title": "Null Elimination Refactoring",
            "problem_statement": "This code returns null when a user is not found, causing NullPointerExceptions downstream:\n\n```python\ndef get_user(user_id):\n    user = db.query(User).filter_by(id=user_id).first()\n    return user  # returns None if not found\n\n# caller\nuser = get_user(42)\nprint(user.name)  # crashes if user is None\n```\n\nShow three alternative approaches that eliminate the null return, and explain when each is appropriate.",
            "hints": ["Consider: raising an exception, returning a default object, or returning an Optional/Maybe type", "Think about the caller's intent -- do they expect the user to exist?", "The Null Object pattern can provide a safe default"],
            "expected_solution": "Approach 1 -- Raise exception (when user MUST exist): raise UserNotFoundError(user_id) instead of returning None. Approach 2 -- Return empty/default (when absence is normal): return a NullUser object with name='Guest' and safe defaults. Approach 3 -- Return Optional (when caller should decide): return Optional[User] and force the caller to handle the None case explicitly with .or_else() or pattern matching. Use Approach 1 for business logic where a missing user is an error. Use Approach 2 for display logic where a fallback is acceptable. Use Approach 3 for generic utility code where the caller knows best.",
            "common_mistakes": ["Always raising exceptions even when absence is normal", "Returning null but documenting it (the contract is still unsafe)"],
            "bloom_level": "apply",
            "difficulty": 2
        }]
    },

    # ---- Software Architecture ----
    {
        "topic_path": ["Software Architecture", "The Clean Architecture", "What is the Clean Architecture?"],
        "exercises": [{
            "title": "Clean Architecture Layer Assignment",
            "problem_statement": "Assign each of the following code artifacts to the correct Clean Architecture layer (Entities, Use Cases, Interface Adapters, Frameworks & Drivers) and explain the Dependency Rule violation if any exist:\n\n1. A `User` class with business validation (email format, password strength)\n2. A `RegisterUserUseCase` class that orchestrates user creation\n3. A Flask route handler that parses JSON and calls the use case\n4. A `SqlAlchemyUserRepository` that persists users to PostgreSQL\n5. A `UserPresenter` that formats user data for the API response\n6. The `RegisterUserUseCase` directly imports `flask.request`",
            "hints": ["Entities are the innermost circle -- pure business rules", "Use Cases orchestrate entities -- application-specific business rules", "The Dependency Rule: source code dependencies only point inward", "Frameworks are the outermost circle"],
            "expected_solution": "(1) Entities -- pure business logic with no framework dependencies. (2) Use Cases -- application logic orchestrating entities. (3) Frameworks & Drivers -- Flask is a web framework. (4) Frameworks & Drivers (or Interface Adapters) -- SQLAlchemy is infrastructure. (5) Interface Adapters -- transforms data between use case format and external format. (6) VIOLATION: RegisterUserUseCase (inner circle) imports flask.request (outer circle). The Dependency Rule forbids this. Fix: the Flask handler should extract data from the request and pass it as plain data to the use case.",
            "common_mistakes": ["Putting repository interfaces in the Frameworks layer instead of Use Cases", "Confusing Interface Adapters with Frameworks"],
            "bloom_level": "apply",
            "difficulty": 2
        }]
    },

    # ---- Concurrency ----
    {
        "topic_path": ["Concurrency", "What Causes Deadlock and How Do You Prevent It?"],
        "exercises": [{
            "title": "Deadlock Scenario Analysis",
            "problem_statement": "Two threads need to transfer money between accounts. Thread 1 transfers from Account A to Account B. Thread 2 transfers from Account B to Account A. Both acquire locks on accounts in the order of their operation:\n\n```\nThread 1: lock(A), lock(B), transfer, unlock(B), unlock(A)\nThread 2: lock(B), lock(A), transfer, unlock(A), unlock(B)\n```\n\nExplain why this can deadlock. Identify which of the four Coffman conditions are present. Propose a fix that prevents deadlock while still ensuring thread safety.",
            "hints": ["Deadlock requires all four Coffman conditions simultaneously", "The four conditions are: mutual exclusion, hold and wait, no preemption, circular wait", "You only need to break ONE condition to prevent deadlock"],
            "expected_solution": "Deadlock scenario: Thread 1 locks A, Thread 2 locks B, Thread 1 waits for B (held by Thread 2), Thread 2 waits for A (held by Thread 1) -- circular wait. All four conditions: (1) Mutual exclusion -- locks are exclusive. (2) Hold and wait -- each thread holds one lock while waiting for another. (3) No preemption -- locks cannot be forcibly taken. (4) Circular wait -- A waits for B which waits for A. Fix: Break circular wait by always acquiring locks in a consistent global order (e.g., by account ID). Both threads would lock(A) then lock(B), or lock the lower ID first. This eliminates the circular dependency.",
            "common_mistakes": ["Trying to fix deadlock by adding more locks", "Using timeouts as the primary strategy (can cause livelock)"],
            "bloom_level": "analyze",
            "difficulty": 3
        }]
    },

    # ---- Systems Design ----
    {
        "topic_path": ["Systems Design", "Emergent Design", "What Are Kent Beck's Four Rules of Simple Design?"],
        "exercises": [{
            "title": "Simple Design Evaluation",
            "problem_statement": "Kent Beck's four rules of simple design are (in priority order): (1) Runs all the tests, (2) Contains no duplication, (3) Expresses the intent of the programmer, (4) Minimizes the number of classes and methods. Given a codebase with 100% test coverage but 3 nearly identical classes that each handle a different payment type with copy-pasted logic, which rules are satisfied and which are violated? What should you do?",
            "hints": ["Evaluate each rule independently", "The rules are in priority order", "Rule 2 (no duplication) trumps Rule 4 (minimize classes)"],
            "expected_solution": "Rule 1 (tests pass): Satisfied -- 100% coverage. Rule 2 (no duplication): VIOLATED -- three classes with copy-pasted logic. Rule 3 (expresses intent): Partially violated -- the duplication obscures the fact that payment processing follows a common pattern. Rule 4 (minimize classes): Satisfied at first glance, but creating a shared base class or strategy would actually reduce total code. Action: Extract the shared logic into a base PaymentProcessor class or strategy, making the three classes thin subclasses that differ only in their unique behavior. This may increase the class count by 1 but eliminates duplication (Rule 2 > Rule 4).",
            "common_mistakes": ["Thinking Rule 4 means fewer classes is always better", "Not recognizing that duplication elimination may temporarily increase class count"],
            "bloom_level": "evaluate",
            "difficulty": 2
        }]
    },

    # ---- Code Smells and Heuristics ----
    {
        "topic_path": ["Code Smells and Heuristics", "What Is Feature Envy and How Do You Fix It?"],
        "exercises": [{
            "title": "Feature Envy Identification and Fix",
            "problem_statement": "Identify the feature envy in this code and refactor it:\n\n```python\nclass OrderPrinter:\n    def print_order_summary(self, order):\n        total = 0\n        for item in order.items:\n            price = item.product.price\n            qty = item.quantity\n            discount = item.product.discount_percent\n            line_total = price * qty * (1 - discount / 100)\n            total += line_total\n        tax = total * order.customer.tax_rate\n        print(f'Subtotal: {total}, Tax: {tax}, Total: {total + tax}')\n```",
            "hints": ["Feature envy means a method uses another class's data more than its own", "Which class's data does print_order_summary access most?", "The calculation logic belongs where the data lives"],
            "expected_solution": "Feature envy: OrderPrinter reaches deep into Order, Item, Product, and Customer internals. Refactored:\n```python\nclass Order:\n    def subtotal(self):\n        return sum(item.line_total() for item in self.items)\n    def tax(self):\n        return self.subtotal() * self.customer.tax_rate\n    def total(self):\n        return self.subtotal() + self.tax()\n\nclass OrderItem:\n    def line_total(self):\n        return self.product.price * self.quantity * (1 - self.product.discount_percent / 100)\n\nclass OrderPrinter:\n    def print_order_summary(self, order):\n        print(f'Subtotal: {order.subtotal()}, Tax: {order.tax()}, Total: {order.total()}')\n```\nNow each class computes with its own data. OrderPrinter only asks Order for computed values.",
            "common_mistakes": ["Moving everything to OrderPrinter instead of to the data owners", "Only moving the total calculation but leaving line_total in OrderPrinter"],
            "bloom_level": "apply",
            "difficulty": 2
        }]
    },

    # ---- Site Reliability Engineering ----
    {
        "topic_path": ["Site Reliability Engineering", "What is an error budget and why is 100% the wrong target?"],
        "exercises": [{
            "title": "Error Budget Calculation",
            "problem_statement": "A service has an SLO of 99.9% availability measured over a 30-day window. Calculate: (1) the error budget in minutes for the 30-day period, (2) if the service has had 20 minutes of downtime so far this month, what percentage of the error budget is consumed? (3) if the team wants to deploy a risky change that historically causes 5 minutes of downtime, should they proceed?",
            "hints": ["30 days = 30 * 24 * 60 minutes", "Error budget = total minutes * (1 - SLO)", "Compare remaining budget against expected risk"],
            "expected_solution": "Total minutes in 30 days: 30 * 24 * 60 = 43,200 minutes. (1) Error budget = 43,200 * (1 - 0.999) = 43,200 * 0.001 = 43.2 minutes. (2) 20 minutes consumed out of 43.2 = 46.3% consumed, leaving 23.2 minutes. (3) Expected downtime of 5 minutes would consume 23.2 - 5 = 18.2 minutes remaining (leaving 42% of original budget). Since 23.2 > 5, they can proceed, but should monitor closely. If the risky change were expected to cause 25 minutes of downtime, they should NOT deploy as it would exceed the remaining budget.",
            "common_mistakes": ["Using 365 days instead of 30 for the window", "Forgetting to convert the SLO to a fraction before calculating"],
            "bloom_level": "apply",
            "difficulty": 2
        }]
    },
    {
        "topic_path": ["Site Reliability Engineering", "What is toil and why must SREs minimize it?"],
        "exercises": [{
            "title": "Toil Classification",
            "problem_statement": "Classify each task as 'toil' or 'engineering work' and justify your answer using the six characteristics of toil (manual, repetitive, automatable, tactical, no enduring value, scales with service growth):\n\n1. Writing a runbook for incident response\n2. Manually restarting a service every Monday due to a memory leak\n3. Designing a self-healing system that auto-restarts on OOM\n4. Running a script to onboard each new customer to the platform\n5. Investigating a novel production issue that has never occurred before\n6. Manually rotating SSL certificates every 90 days",
            "hints": ["Toil is operational work that could be automated", "Engineering work produces lasting improvements", "Not all operational work is toil -- novel investigation is not repetitive"],
            "expected_solution": "(1) Engineering -- produces enduring value (documentation) and is not repetitive. (2) Toil -- manual, repetitive, automatable, tactical (treats symptom not cause), scales with service count. (3) Engineering -- designs a lasting solution that eliminates future toil. (4) Toil -- manual, repetitive, automatable, scales linearly with customer growth. (5) Engineering -- novel investigation produces understanding and is not repetitive. (6) Toil -- manual, repetitive, automatable, and if forgotten causes outages. The fix for #2 is to fix the memory leak. The fix for #4 is a self-service onboarding API. The fix for #6 is auto-rotation with cert-manager.",
            "common_mistakes": ["Classifying all operational work as toil", "Thinking toil is only 'boring work' rather than using the formal characteristics"],
            "bloom_level": "analyze",
            "difficulty": 2
        }]
    },
    {
        "topic_path": ["Site Reliability Engineering", "Why are SLOs the foundation of SRE practice?"],
        "exercises": [{
            "title": "SLI/SLO Definition",
            "problem_statement": "Design SLIs and SLOs for a web API that serves product search results. Define: (1) an availability SLI and SLO, (2) a latency SLI and SLO, (3) a correctness SLI and SLO. For each, specify what you measure, where you measure it, and the target threshold.",
            "hints": ["SLIs should be measurable ratios (good events / total events)", "Measure as close to the user as possible", "SLOs should be realistic but ambitious"],
            "expected_solution": "(1) Availability: SLI = proportion of requests returning non-5xx responses, measured at the load balancer. SLO = 99.9% of requests succeed over a 30-day window. (2) Latency: SLI = proportion of requests completing in under 200ms, measured at the API gateway. SLO = 95% of requests under 200ms, 99% under 1000ms. (3) Correctness: SLI = proportion of search responses that return relevant results (measured by periodic probe queries with known expected results). SLO = 99.5% of probe queries return expected results. All measured over a 30-day rolling window.",
            "common_mistakes": ["Defining SLIs as absolute numbers instead of ratios", "Setting SLOs at 100% (leaves no room for error budget)", "Measuring at the server instead of at the user-facing boundary"],
            "bloom_level": "create",
            "difficulty": 3
        }]
    },

    # ---- Object-Oriented Design ----
    {
        "topic_path": ["Object-Oriented Design", "OO Fundamentals", "What Is the Law of Demeter?"],
        "exercises": [{
            "title": "Law of Demeter Violation Fix",
            "problem_statement": "This code violates the Law of Demeter (don't talk to strangers):\n\n```python\ndef get_delivery_city(order):\n    return order.get_customer().get_address().get_city()\n```\n\nExplain why this is problematic and show two different ways to fix it.",
            "hints": ["The method is reaching through three objects to get data", "A change to Address structure would break this code", "Consider: what does the caller actually need?"],
            "expected_solution": "Problem: get_delivery_city knows about the internal structure of Customer and Address. If Address changes (e.g., city becomes part of a Location object), this code breaks even though Order itself did not change. Fix 1 -- Delegate through Order: order.get_delivery_city() which internally calls self.customer.address.city. Only Order knows about Customer's structure. Fix 2 -- Pass the data directly: the caller of get_delivery_city should receive the city from wherever constructed the order context, avoiding the chain entirely. Fix 1 is generally preferred because it follows 'Tell, Don't Ask' -- ask Order for the city rather than navigating its internals.",
            "common_mistakes": ["Thinking the fix is just to inline the chain", "Creating a wrapper that still exposes the chain through a different API"],
            "bloom_level": "apply",
            "difficulty": 2
        }]
    },
]


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    # Load structure
    with open(STRUCTURE_PATH) as f:
        structure = json.load(f)

    # Build title index
    all_titles = collect_all_titles(structure)

    # Enrich each node
    for group in structure:
        enrich_node(group, all_titles)

    # Write enriched structure
    with open(STRUCTURE_PATH, "w") as f:
        json.dump(structure, f, indent=2, ensure_ascii=False)

    print(f"Enriched structure.json written to {STRUCTURE_PATH}")
    print(f"  Total topics with learning_objectives: {len(all_titles)}")

    # Count prerequisites
    prereq_count = 0
    for title in all_titles:
        if title in EXPLICIT_PREREQUISITES:
            prereq_count += 1
    print(f"  Topics with prerequisites: {prereq_count}")

    # Write exercises
    with open(EXERCISES_PATH, "w") as f:
        json.dump(EXERCISE_DATA, f, indent=2, ensure_ascii=False)

    total_exercises = sum(len(entry["exercises"]) for entry in EXERCISE_DATA)
    print(f"Exercises written to {EXERCISES_PATH}")
    print(f"  Total exercise entries: {len(EXERCISE_DATA)}")
    print(f"  Total individual exercises: {total_exercises}")


if __name__ == "__main__":
    main()
