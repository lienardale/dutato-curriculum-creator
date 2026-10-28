"""Tests for lessons.py — prepare work files, check outlines and full lessons, and build
structure.json + chunks.json on a synthetic lesson-mode curriculum (2 lessons, one with a
code unit). Every gate is exercised twice: a planted defect must be caught and the clean
fixture must pass without errors."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

import _compat  # noqa: F401

from chunk import count_tokens
from contracts import (
    CONTRACT_VERSION,
    LESSON_METRICS_FILE,
    NEW_TERMS_PER_PAGE_MAX,
    PAGE_WORDS,
    budget_for,
    dump_json,
    item_id_to_filename,
    word_count,
)
import lessons
import validate
from lessons import build, check, check_item, main, prepare

write_json = dump_json  # creates parent directories

# ---------------------------------------------------------------------------
# Synthetic lesson-mode curriculum
# ---------------------------------------------------------------------------

SL = "g:service-layer"
DM = "g:domain-model"
IMAGE_ID = "p142_img0"

UNITS = {
    "peaa": [
        ("u:peaa:12:0", 12, "Service Layer", "prose",
         "A Service Layer defines an application's boundary with a layer of services that establishes a set "
         "of available operations and coordinates the application's response in each operation. Clients call "
         "the service layer rather than reaching into the domain model directly."),
        ("u:peaa:12:1", 12, "Service Layer", "prose",
         "Business logic splits into two kinds: domain logic, which concerns the problem domain, and "
         "application logic, which concerns the responsibilities of the application itself, such as "
         "coordinating transactions and notifying other systems."),
        ("u:peaa:12:2", 12, "Service Layer", "code",
         "```java\npublic class RecognitionService {\n  public void calculateRevenueRecognitions(long contractNumber) { }\n}\n```"),
        ("u:peaa:12:3", 12, "Service Layer", "prose",
         "A common mistake is to put domain logic into the service layer, producing an anemic domain model "
         "where entities are mere data holders and the services hold all the behaviour."),
        ("u:peaa:10:0", 10, "Domain Model", "prose",
         "A Domain Model is an object model of the domain that incorporates both behaviour and data. Each "
         "object represents something meaningful in the business and carries the rules that apply to it."),
        ("u:peaa:10:1", 10, "Domain Model", "prose",
         "Domain models are worth the effort when the business rules are complex and change frequently; for "
         "simple validation and calculation a Transaction Script is enough."),
    ],
    "clean-arch": [
        ("u:clean-arch:4:0", 4, "Use Cases", "prose",
         "Use cases sit between the delivery mechanism and the entities. They orchestrate the flow of data to "
         "and from the entities and direct them to apply their enterprise business rules to achieve the goals "
         "of the use case."),
        ("u:clean-arch:4:1", 4, "Use Cases", "prose",
         "Use cases do not know how the data is presented. They are indifferent to whether the request "
         "arrived from a web page or a batch job."),
    ],
}

QUALIFICATION = {
    "u:peaa:12:0": ("definition", "concept", "c:service-layer"),
    "u:peaa:12:1": ("mechanism", "concept", "c:application-logic"),
    "u:peaa:12:2": ("code", "procedure", "c:service-layer"),
    "u:peaa:12:3": ("pitfall", "principle", "c:service-layer"),
    "u:peaa:10:0": ("definition", "concept", "c:domain-model"),
    "u:peaa:10:1": ("trade_off", "principle", "c:transaction-script"),
    "u:clean-arch:4:0": ("definition", "concept", "c:service-layer"),
    "u:clean-arch:4:1": ("context", "concept", "c:service-layer"),
}

GENERALIZATION = {
    DM: "A Domain Model binds behaviour to data so rules live with the objects they govern.",
    SL: "A Service Layer coordinates application logic while the Domain Model keeps the rules.",
}


def build_curriculum(root: Path) -> Path:
    """units/, qualification/, concepts.json, groups.json, syntheses/ for two groups."""
    cur = root / "arch"
    cur.mkdir(parents=True)
    write_json(cur / "manifest.json", {"name": "Arch", "domain": "arch", "pipeline": "lesson", "sources": []})

    for source, units in UNITS.items():
        write_json(cur / "units" / f"{source}.json", {
            "contract_version": CONTRACT_VERSION, "source": source, "source_file": f"{source}.json",
            "source_type": "pdf", "title": source.upper(), "scope_applied": False,
            "units": [
                {"id": uid, "section_index": sec, "section_title": title, "heading_path": [title],
                 "block_type": block, "text": text, "token_count": count_tokens(text),
                 "raw_span": [0, len(text)], "page": 1, "cleaning": {},
                 "images": ([{"id": IMAGE_ID, "local_path": f"images/{IMAGE_ID}.png",
                              "alt_text": "Service layer boundary diagram", "mime_type": "image/png"}]
                            if uid == "u:peaa:12:0" else [])}
                for uid, sec, title, block, text in units
            ],
        })
        write_json(cur / "qualification" / f"{source}.json", {
            "contract_version": CONTRACT_VERSION, "source": source,
            "units": {
                uid: {"concepts": [{"concept_id": cid, "quote": ""}], "unit_type": utype, "knowledge_level": klevel,
                      "learning_step": "building_block", "bloom_ceiling": "analyze", "bloom_ceiling_reason": None,
                      "quality": 4, "keep": True, "drop_reason": None, "depends_on": [], "notes": ""}
                for uid, (utype, klevel, cid) in QUALIFICATION.items() if uid.startswith(f"u:{source}:")
            },
            "gleaning": {},
        })
    write_json(cur / "units" / "index.json", {"contract_version": CONTRACT_VERSION, "sources": {}, "total_units": 8})

    write_json(cur / "concepts.json", {
        "contract_version": CONTRACT_VERSION,
        "concepts": [
            {"id": "c:service-layer", "label": "Service Layer", "aliases": ["application service layer"],
             "description": "Application boundary coordinating operations.", "learning_step": "building_block",
             "depends_on": ["c:domain-model"], "coverage": "strong", "sources": ["peaa", "clean-arch"]},
            {"id": "c:application-logic", "label": "Application Logic", "aliases": [], "description": "",
             "learning_step": "building_block", "depends_on": [], "coverage": "moderate", "sources": ["peaa"]},
            {"id": "c:domain-model", "label": "Domain Model", "aliases": ["rich domain model"], "description": "",
             "learning_step": "foundation", "depends_on": [], "coverage": "strong", "sources": ["peaa"]},
            {"id": "c:transaction-script", "label": "Transaction Script", "aliases": [], "description": "",
             "learning_step": "foundation", "depends_on": [], "coverage": "thin", "sources": ["peaa"]},
        ],
        "gaps": [], "ordering": ["c:domain-model", "c:transaction-script", "c:service-layer", "c:application-logic"],
        "merges": [],
    })

    def kps(gid, titles):
        return [{"id": f"{gid}#kp{i + 1}", "title": t, "unit_ids": []} for i, t in enumerate(titles)]

    def los(gid, texts):
        return [{"id": f"{gid}#lo{i + 1}", "text": t, "bloom_level": "understand", "concept_ids": [],
                 "unit_ids": [], "knowledge_point_ids": []} for i, t in enumerate(texts)]

    def watch(gid, texts):
        return [{"id": f"{gid}#w{i + 1}", "kind": "misconception", "text": t, "unit_ids": []}
                for i, t in enumerate(texts)]

    write_json(cur / "groups.json", {
        "contract_version": CONTRACT_VERSION,
        "areas": [{"id": "a:domain-logic", "title": "Domain Logic", "sort_order": 0, "suggested_level": 1,
                   "modules": [{"id": "m:patterns", "title": "Patterns", "sort_order": 0}]}],
        "groups": [
            {"id": DM, "title": "Domain Model", "area_id": "a:domain-logic", "module_id": "m:patterns",
             "sort_order": 0, "concept_ids": ["c:domain-model", "c:transaction-script"],
             "unit_ids": ["u:peaa:10:0", "u:peaa:10:1"], "related_unit_ids": [],
             "learning_step": "foundation", "suggested_level": 1,
             "generalization": GENERALIZATION[DM],
             "knowledge_points": kps(DM, ["Behaviour with data", "Objects mirror the business", "When it pays off"]),
             "objectives": los(DM, ["Explain how a domain model binds rules to objects",
                                    "Decide when a domain model beats a transaction script"]),
             "watch": watch(DM, ["Domain models are just data classes", "Always use a domain model"]),
             "duplicates": [], "conflicts": [], "prerequisites": [], "consequential": False, "rationale": "",
             "budget": budget_for(3, 2, "foundation", False)},
            {"id": SL, "title": "Service Layer", "area_id": "a:domain-logic", "module_id": "m:patterns",
             "sort_order": 1, "concept_ids": ["c:service-layer", "c:application-logic"],
             "unit_ids": ["u:peaa:12:0", "u:peaa:12:1", "u:peaa:12:2", "u:peaa:12:3"],
             "related_unit_ids": ["u:clean-arch:4:0", "u:clean-arch:4:1"],
             "learning_step": "building_block", "suggested_level": 2,
             "generalization": GENERALIZATION[SL],
             "knowledge_points": kps(SL, ["The boundary", "Application vs domain logic", "Delivery independence"]),
             "objectives": los(SL, ["Describe what a service layer coordinates",
                                    "Distinguish application logic from domain logic"]),
             "watch": watch(SL, ["Services should hold the business rules", "Use cases know the UI"]),
             "duplicates": [], "conflicts": [],
             "prerequisites": [{"group_id": DM, "strength": "required", "evidence_unit_ids": ["u:peaa:12:0"],
                                "reason": "assumes the domain model", "status": "committed"}],
             "consequential": False, "rationale": "",
             "budget": budget_for(3, 2, "building_block", True)},
        ],
        "dropped": [],
        "prerequisite_edges_reduced": [{"from": DM, "to": SL}],
    })

    def item(gid, n, kind, text, unit_ids, quote, lo, kp):
        return {"id": f"{gid}#b{n}", "kind": kind, "text": text, "unit_ids": unit_ids, "quote": quote,
                "confidence": "attributed", "concept_ids": [], "lo_ids": [f"{gid}#lo{lo}"],
                "knowledge_point_id": f"{gid}#kp{kp}"}

    write_json(cur / "syntheses" / item_id_to_filename(DM), {
        "contract_version": CONTRACT_VERSION, "group_id": DM, "revision": 0,
        "overview": "Why a domain model binds behaviour to data and when that pays off.",
        "items": [
            item(DM, 1, "map", "A domain model couples the rules of the business to the objects that hold its data.",
                 ["u:peaa:10:0"], "incorporates both behaviour and data", 1, 1),
            item(DM, 2, "map", "Each object stands for something the business recognises and owns its rules.",
                 ["u:peaa:10:0"], "carries the rules that apply to it", 1, 2),
            item(DM, 3, "map", "Rich models repay their cost only when rules are complex and shift often.",
                 ["u:peaa:10:1"], "worth the effort when the business rules are complex", 2, 3),
            item(DM, 4, "watch", "For simple validation a transaction script suffices.",
                 ["u:peaa:10:1"], "a Transaction Script is enough", 2, 3),
        ],
        "examples": [],
        "terminology": [{"term": "transaction script", "definition": "A procedure that handles one request end to end.",
                         "unit_id": "u:peaa:10:1"}],
        "lessons_learned": [],
    })
    write_json(cur / "syntheses" / item_id_to_filename(SL), {
        "contract_version": CONTRACT_VERSION, "group_id": SL, "revision": 0,
        "overview": "The service layer as the entry point that coordinates while the domain model keeps the rules.",
        "items": [
            item(SL, 1, "map", "The service layer is the entry point and lists the operations a client may invoke.",
                 ["u:peaa:12:0"], "defines an application's boundary", 1, 1),
            item(SL, 2, "map", "Application logic covers coordination duties such as transactions and notifications.",
                 ["u:peaa:12:1"], "coordinating transactions and notifying other systems", 1, 2),
            item(SL, 3, "map", "Use cases stay unaware of the channel that delivered a request.",
                 ["u:clean-arch:4:1"], "do not know how the data is presented", 2, 3),
            item(SL, 4, "watch", "Moving domain rules into services hollows the entities into an anemic domain model.",
                 ["u:peaa:12:3"], "anemic domain model", 2, 2),
        ],
        "examples": [{"id": f"{SL}#ex1", "unit_id": "u:peaa:12:2", "language": "java", "note": "Revenue recognition service"}],
        "terminology": [{"term": "anemic domain model", "definition": "Objects that carry data but no behaviour.",
                         "unit_id": "u:peaa:12:3"}],
        "lessons_learned": [],
    })
    return cur


# ---------------------------------------------------------------------------
# Page text generator — original prose (never copied from the units) that lands
# inside every metric band of the full check.
# ---------------------------------------------------------------------------


def _paragraphs(sentences: list[str], per_paragraph: int = 3) -> str:
    groups = [sentences[i:i + per_paragraph] for i in range(0, len(sentences), per_paragraph)]
    return "\n\n".join(" ".join(g) for g in groups)


def _fill(intro: list[str], fillers: list[str], target_words: int) -> list[str]:
    """intro sentences, then fillers cycled until the text reaches target_words."""
    out = list(intro)
    i = 0
    while word_count(" ".join(out)) < target_words:
        out.append(fillers[i % len(fillers)])
        i += 1
    return out


def _page(title: str, sentences: list[str], prefix: str = "", suffix: str = "") -> str:
    return f"## {title}\n\n{prefix}{_paragraphs(sentences)}{suffix}"


def domain_model_pages() -> list[str]:
    orientation = _page("Why rules belong with data", _fill(
        ["Suppose a billing team keeps every rule about invoices inside one long procedure that runs once per request.",
         "The procedure grows with each new case until nobody can say which rule applies to which invoice.",
         "This lesson shows why a domain model keeps each rule next to the data it governs and when that structure pays for itself."],
        ["Picture an invoice object that knows how to compute its own late fee.",
         "The team reads the rule where the data lives instead of hunting through a script.",
         "A model built this way answers questions about the business in the vocabulary of the business.",
         "You will also see where a simpler script is the honest choice."], 185))
    core1 = _page("Behaviour with data", _fill(
        ["A **domain model** is a set of business objects where each object owns its data together with the behaviour that changes it.",
         "**Behaviour** means the operations an object performs on its own state, such as approving an order or computing a fee.",
         "For example, an order object in the domain model decides for itself whether a discount applies."],
        ["Because behaviour lives next to the data, a rule in the domain model changes in exactly one place.",
         "The model therefore mirrors how the business talks about its own work.",
         "A reader finds the behaviour by opening the object, not by searching a procedure.",
         "In practice the domain model grows one object at a time as new behaviour appears.",
         "Each piece of behaviour stays small because it touches only the data of its own object."], 330))
    core2 = _page("Objects mirror the business", _fill(
        ["A **transaction script** is a single procedure that handles one request from start to finish and keeps all of its rules in one body.",
         "A **data holder** is an object that stores fields but makes no decisions about them.",
         "Consider a refund request: the transaction script checks the order, computes the amount and writes the result in sequence."],
        ["In a transaction script every rule sits in the procedure, so the objects it touches stay plain data holders.",
         "A data holder cannot refuse an invalid change because it has no behaviour of its own.",
         "The transaction script is honest about its shape: one request, one procedure, one path through the rules.",
         "When two requests share a rule, the transaction script copies it or calls a helper, and the copies drift apart.",
         "A business object replaces the data holder by taking the rule into itself."], 330))
    pitfalls = _page("When it pays off", _fill(
        ["Three misconceptions follow the domain model around, and each one has a direct remedy."],
        [], 0),
        suffix="\n\n### Misconception: a domain model is a set of data classes\n\n"
               "Teams sometimes generate classes from a database and call the result a model. Remediation: ask of every "
               "class which decision it makes; a class that makes none is a data holder, not part of the model.\n\n"
               "### Misconception: every application needs a domain model\n\n"
               "For example, a form that validates three fields and stores them gains nothing from an object model. "
               "Remediation: when the rules are few and stable, write a transaction script and keep the structure light.\n\n"
               "### Misconception: the model pays off immediately\n\n"
               "The first version of a domain model costs more than a script because the objects and their behaviour "
               "must be designed. Remediation: expect the return when the rules start to change, because each change "
               "then lands in one object instead of across many procedures. Suppose a pricing rule changes monthly: the "
               "model absorbs each change in one place while the script needs a hunt through every path that prices "
               "an order. That recurring saving is the payoff, and it only appears when change is frequent.")
    recap = _page("Recap", [GENERALIZATION[DM],
                            "Keep these takeaways in mind as you move on."],
                  suffix="\n\n- A domain model gives each business object its data and the behaviour that changes it.\n"
                         "- A data holder stores fields but decides nothing, so rules about it live elsewhere.\n"
                         "- A transaction script handles one request in one procedure and suits simple, stable rules.\n"
                         "- The model repays its design cost only when rules are complex and change often.\n\n"
                         "Key terms: domain model, behaviour, data holder, transaction script.\n\n"
                         "Next, the service layer lesson shows how an application coordinates requests without pulling "
                         "the rules out of these objects. For example, a billing screen will ask a service to close a month "
                         "while the invoice objects keep deciding their own fees, and that split is the subject of the next "
                         "lesson. Picture the objects you met here as the inner circle that the next lesson wraps.")
    return [orientation, core1, core2, pitfalls, recap]


SL_CODE = "```java\npublic class RecognitionService {\n  public void calculateRevenueRecognitions(long contractNumber) { }\n}\n```"


def service_layer_pages() -> list[str]:
    orientation = _page("The front door of an application", _fill(
        ["Imagine a billing screen, a nightly batch job and a partner API that all need to close a month of accounts.",
         "If each of them reaches into the domain model directly, three copies of the closing steps appear and drift apart.",
         "A service layer gives the application one front door through which every caller enters."],
        ["The domain model you met in the previous lesson keeps the rules; the service layer decides which rules run for a request.",
         "This lesson explains the boundary a service layer draws and what belongs on each side of it.",
         "You will see a small revenue recognition service and the mistakes that hollow a model out."], 185),
        prefix=f"![Service layer boundary diagram](image:{IMAGE_ID})\n\n")
    core1 = _page("The boundary", _fill(
        ["A **service layer** is the set of operations an application offers to its callers, each one coordinating the domain objects that answer a request.",
         "The **boundary** is the line between the callers and the domain model, and the service layer sits exactly on that line.",
         "For example, a billing screen asks the service layer to close a month instead of updating invoice totals itself."],
        ["A client that respects the boundary never reaches past the service layer into the objects behind it.",
         "Because the boundary is explicit, the team can list every operation the service layer offers.",
         "The service layer answers each request by choosing which domain operations run and in what order.",
         "In practice the service layer stays thin while the objects behind the boundary carry the rules.",
         "Each operation on the boundary names a goal of the caller, not a step inside the service layer."], 330))
    worked = _page("A revenue recognition service", _fill(
        ["**Application logic** is the coordination work an application does around the rules: opening a transaction, calling the right objects, and telling other systems what happened.",
         "Domain logic, by contrast, is the rules themselves, and it stays inside the domain model.",
         "Consider the small service below, which recognises revenue for one contract."],
        [], 0),
        suffix=f"\n\n{SL_CODE}\n\n"
               "Step one: the service receives a contract number, which is a request from a caller, not a rule.\n\n"
               "Pause and explain: why does the service take an identifier rather than a contract object?\n\n"
               "Step two: the service loads the contract and asks it to calculate its own recognitions, so the "
               "application logic in the service only coordinates while the contract applies the rule. "
               "Step three: the service commits the transaction and notifies the ledger, which is application logic again.\n\n"
               "Pause and explain: what would move into the service if the contract became a plain data holder?\n\n"
               "The pattern repeats for every operation on the boundary: application logic in the service, rules in "
               "the objects. For example, a monthly close operation loads the invoices, lets each one compute its "
               "fees, and then records the result. The application logic decides the order of those calls and "
               "handles the transaction around them. The rules about fees never leave the invoice objects.\n\n"
               "Suppose a new notification is needed when a close completes: that is application logic, so it joins "
               "the service rather than the invoice. Suppose instead the fee formula changes: that is domain logic, "
               "so the invoice object changes and the service stays untouched. Keeping the two kinds apart is what "
               "lets each change land in one place, and the service above shows the split in a dozen lines.\n\n"
               "In practice you can test the application logic with a fake ledger while the rule tests need no "
               "service at all. The boundary makes that separation visible to every reader of the code.")
    pitfalls = _page("Delivery independence", _fill(
        ["Three misconceptions pull rules out of the domain model or tie the service layer to a channel."],
        [], 0),
        suffix="\n\n### Misconception: services should hold the business rules\n\n"
               "When rules migrate into the service layer, the objects become empty shells. An **anemic domain model** is "
               "exactly that: objects that carry data but no behaviour. Remediation: when a service grows an if-statement "
               "about the business, move that decision into the object it concerns.\n\n"
               "### Misconception: a service knows which screen called it\n\n"
               "A service that formats output for a web page cannot serve a batch job. Remediation: for example, return "
               "plain results and let each caller present them; the service should not know whether the request came "
               "from a page or a job.\n\n"
               "### Misconception: the boundary is a pass-through\n\n"
               "Some teams write one service method per domain method and gain nothing. Remediation: name each operation "
               "after a caller's goal, such as closing a month, so that the service coordinates several objects and the "
               "boundary earns its place. Picture the caller reading the list of operations as a menu of goals.")
    recap = _page("Recap", [GENERALIZATION[SL],
                            "Keep these takeaways in mind."],
                  suffix="\n\n- The service layer is the boundary every caller crosses to reach the domain model.\n"
                         "- Application logic coordinates transactions, objects and notifications; domain logic is the rules.\n"
                         "- A service that absorbs the rules leaves an anemic domain model behind.\n"
                         "- A service stays independent of the channel that delivered the request.\n\n"
                         "Key terms: service layer, boundary, application logic, anemic domain model.\n\n"
                         "Next, the lessons on delivery mechanisms show how a web page or a batch job calls into this "
                         "boundary. For example, both will invoke the same close operation, and the service will not "
                         "know which one is calling. Picture the boundary as a wall with a short list of doors.\n\nEach door is an "
                         "operation named after a goal, and the rules stay behind the wall where the domain model keeps "
                         "them. The next lesson also returns to the anemic domain model as the failure to avoid.")
    return [orientation, core1, worked, pitfalls, recap]


def _lesson(gid: str, title: str, description: str, area: str, module: str, level: int, key_terms: list[str],
            objectives: list[dict], prerequisites: list[str], pages: list[dict], estimated: int) -> dict:
    return {
        "contract_version": CONTRACT_VERSION, "lesson_id": gid, "group_id": gid, "revision": 0,
        "title": title, "description": description, "area_id": area, "module_id": module,
        "suggested_level": level, "estimated_minutes": estimated, "key_terms": key_terms,
        "objectives": [{"id": o["id"], "text": o["text"], "bloom_level": o["bloom_level"]} for o in objectives],
        "prerequisites": prerequisites, "pages": pages, "outline_approved": True, "lessons_learned": [],
    }


def _pg(index, role, title, lo, kp, units, briefs, markdown):
    return {"index": index, "role": role, "title": title, "lo_ids": lo, "knowledge_point_id": kp,
            "unit_ids": units, "brief_item_ids": briefs, "markdown": markdown}


def domain_model_lesson(groups: dict) -> dict:
    md = domain_model_pages()
    pages = [
        _pg(0, "orientation", "Why rules belong with data", [f"{DM}#lo1"], None, ["u:peaa:10:0"], [f"{DM}#b1"], md[0]),
        _pg(1, "core", "Behaviour with data", [f"{DM}#lo1"], f"{DM}#kp1", ["u:peaa:10:0"], [f"{DM}#b1"], md[1]),
        _pg(2, "core", "Objects mirror the business", [f"{DM}#lo1", f"{DM}#lo2"], f"{DM}#kp2", ["u:peaa:10:0", "u:peaa:10:1"], [f"{DM}#b2", f"{DM}#b4"], md[2]),
        _pg(3, "pitfalls", "When it pays off", [f"{DM}#lo2"], f"{DM}#kp3", ["u:peaa:10:1"], [f"{DM}#b3", f"{DM}#b4"], md[3]),
        _pg(4, "recap", "Recap", [f"{DM}#lo1", f"{DM}#lo2"], None, ["u:peaa:10:0", "u:peaa:10:1"], [f"{DM}#b1", f"{DM}#b3"], md[4]),
    ]
    words = sum(word_count(lessons._prose(m)) for m in md)
    return _lesson(DM, "Domain Model", "Rules live with the objects they govern.", "a:domain-logic", "m:patterns", 1,
                   ["behaviour", "data holder"], groups[DM]["objectives"], [], pages, round(words / 238))


def service_layer_lesson(groups: dict) -> dict:
    md = service_layer_pages()
    pages = [
        _pg(0, "orientation", "The front door of an application", [f"{SL}#lo1"], None, ["u:peaa:12:0"], [f"{SL}#b1"], md[0]),
        _pg(1, "core", "The boundary", [f"{SL}#lo1"], f"{SL}#kp1", ["u:peaa:12:0"], [f"{SL}#b1"], md[1]),
        _pg(2, "worked_example", "A revenue recognition service", [f"{SL}#lo2"], f"{SL}#kp2", ["u:peaa:12:1", "u:peaa:12:2"], [f"{SL}#b2"], md[2]),
        _pg(3, "pitfalls", "Delivery independence", [f"{SL}#lo2"], f"{SL}#kp3", ["u:peaa:12:3", "u:clean-arch:4:1"], [f"{SL}#b3", f"{SL}#b4"], md[3]),
        _pg(4, "recap", "Recap", [f"{SL}#lo1", f"{SL}#lo2"], None, ["u:peaa:12:0"], [f"{SL}#b1", f"{SL}#b2"], md[4]),
    ]
    words = sum(word_count(lessons._prose(m)) for m in md)
    return _lesson(SL, "Service Layer", "One front door that coordinates while the model keeps the rules.",
                   "a:domain-logic", "m:patterns", 2, ["boundary", "coordination"], groups[SL]["objectives"], [DM],
                   pages, round(words / 238))


def lesson_path(cur: Path, gid: str) -> Path:
    return cur / "lessons" / item_id_to_filename(gid)


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_lessons(cur: Path, outline: bool = False) -> None:
    groups = {g["id"]: g for g in read_json(cur / "groups.json")["groups"]}
    for lesson in (domain_model_lesson(groups), service_layer_lesson(groups)):
        if outline:
            lesson = copy.deepcopy(lesson)
            for page in lesson["pages"]:
                page["markdown"] = None
            lesson["outline_approved"] = False
        write_json(lesson_path(cur, lesson["lesson_id"]), lesson)


def mutate(cur: Path, gid: str, fn) -> dict:
    lesson = read_json(lesson_path(cur, gid))
    fn(lesson)
    write_json(lesson_path(cur, gid), lesson)
    return lesson


@pytest.fixture
def cur(tmp_path: Path) -> Path:
    """Complete lesson-mode curriculum with two full lessons that pass every gate."""
    cur = build_curriculum(tmp_path)
    write_lessons(cur)
    return cur


@pytest.fixture
def outline_cur(tmp_path: Path) -> Path:
    cur = build_curriculum(tmp_path)
    write_lessons(cur, outline=True)
    return cur


def ids(findings, severity=None):
    return [f.check_id for f in findings if severity is None or f.severity == severity]


def errors(findings):
    return ids(findings, "error")


def warnings(findings):
    return ids(findings, "warning")


def _trim(markdown: str, max_words: int) -> str:
    """Drop trailing sentences until the page prose has at most max_words words."""
    head, _, body = markdown.partition("\n\n")
    sentences = body.replace("\n\n", " ").split(". ")
    while sentences and word_count(head + " " + ". ".join(sentences)) > max_words:
        sentences.pop()
    return head + "\n\n" + ". ".join(sentences).rstrip(".") + "."


# ---------------------------------------------------------------------------
# Outline checks
# ---------------------------------------------------------------------------


def test_clean_outline_passes_and_full_check_reports_unwritten_pages(outline_cur):
    assert check(outline_cur, outline=True) == []
    full = check(outline_cur)
    assert errors(full).count("lessons.markdown_missing") == 2
    assert not (outline_cur / LESSON_METRICS_FILE).exists() or read_json(outline_cur / LESSON_METRICS_FILE)["lessons"] == {}


def test_outline_role_order_is_enforced(outline_cur):
    def swap(lesson):
        pages = lesson["pages"]
        pages[1], pages[3] = pages[3], pages[1]
        for i, p in enumerate(pages):
            p["index"] = i
    mutate(outline_cur, SL, swap)
    assert "lessons.outline.order" in errors(check_item(outline_cur, SL, outline=True))


def test_outline_role_counts(outline_cur):
    mutate(outline_cur, SL, lambda l: l["pages"][4].__setitem__("role", "core"))
    found = errors(check_item(outline_cur, SL, outline=True))
    assert "lessons.outline.role_count" in found            # no recap
    mutate(outline_cur, DM, lambda l: l["pages"][1].__setitem__("role", "orientation"))
    assert "lessons.outline.role_count" in errors(check_item(outline_cur, DM, outline=True))  # two orientations
    mutate(outline_cur, DM, lambda l: l["pages"][1].__setitem__("role", "lecture"))
    assert "lessons.outline.role" in errors(check_item(outline_cur, DM, outline=True))


def test_outline_page_count_must_match_budget(outline_cur):
    mutate(outline_cur, SL, lambda l: l["pages"].pop(2))
    found = check_item(outline_cur, SL, outline=True)
    assert "lessons.outline.page_count" in errors(found)
    # the indices of the remaining pages no longer match their positions
    assert "lessons.outline.index" in errors(found)


def test_outline_page_objectives(outline_cur):
    mutate(outline_cur, SL, lambda l: l["pages"][1].__setitem__("lo_ids", []))
    assert "lessons.outline.page_objectives" in errors(check_item(outline_cur, SL, outline=True))
    mutate(outline_cur, SL, lambda l: l["pages"][1].__setitem__("lo_ids", [f"{DM}#lo1"]))
    assert "lessons.outline.page_objectives" in errors(check_item(outline_cur, SL, outline=True))


def test_outline_every_objective_covered(outline_cur):
    def drop_lo2(lesson):
        for p in lesson["pages"]:
            p["lo_ids"] = [lo for lo in p["lo_ids"] if not lo.endswith("#lo2")] or [f"{SL}#lo1"]
    mutate(outline_cur, SL, drop_lo2)
    assert "lessons.outline.objective_uncovered" in errors(check_item(outline_cur, SL, outline=True))


def test_outline_knowledge_points(outline_cur):
    mutate(outline_cur, SL, lambda l: l["pages"][1].__setitem__("knowledge_point_id", None))
    found = errors(check_item(outline_cur, SL, outline=True))
    assert "lessons.outline.knowledge_point" in found and "lessons.outline.kp_uncovered" in found

    mutate(outline_cur, DM, lambda l: l["pages"][2].__setitem__("knowledge_point_id", f"{DM}#kp1"))
    found = errors(check_item(outline_cur, DM, outline=True))
    assert "lessons.outline.kp_duplicate" in found and "lessons.outline.kp_uncovered" in found

    mutate(outline_cur, DM, lambda l: l["pages"][2].__setitem__("knowledge_point_id", f"{DM}#kp2"))
    mutate(outline_cur, DM, lambda l: l["pages"][0].__setitem__("knowledge_point_id", f"{DM}#kp3"))
    assert "lessons.outline.knowledge_point" in errors(check_item(outline_cur, DM, outline=True))
    mutate(outline_cur, DM, lambda l: l["pages"][0].__setitem__("knowledge_point_id", "g:other#kp9"))
    assert "lessons.outline.knowledge_point" in errors(check_item(outline_cur, DM, outline=True))


def test_outline_brief_items_and_inference_only_pages(outline_cur):
    mutate(outline_cur, DM, lambda l: l["pages"][1].__setitem__("brief_item_ids", [f"{DM}#b99"]))
    assert "lessons.brief_item_unknown" in errors(check_item(outline_cur, DM, outline=True))

    brief_path = outline_cur / "syntheses" / item_id_to_filename(DM)
    brief = read_json(brief_path)
    brief["items"].append({"id": f"{DM}#b6", "kind": "inference", "text": "Rules close to data change in one place.",
                           "unit_ids": ["u:peaa:10:0", "u:peaa:10:1"], "quote": "", "confidence": "attributed",
                           "concept_ids": [], "lo_ids": [f"{DM}#lo1"], "knowledge_point_id": None})
    write_json(brief_path, brief)
    mutate(outline_cur, DM, lambda l: l["pages"][1].__setitem__("brief_item_ids", [f"{DM}#b6"]))
    assert "lessons.inference_only_page" in errors(check_item(outline_cur, DM, outline=True))
    mutate(outline_cur, DM, lambda l: l["pages"][1].__setitem__("brief_item_ids", [f"{DM}#b6", f"{DM}#b1"]))
    assert "lessons.inference_only_page" not in errors(check_item(outline_cur, DM, outline=True))


def test_outline_missing_brief_degrades_to_warning(outline_cur):
    (outline_cur / "syntheses" / item_id_to_filename(DM)).unlink()
    found = check_item(outline_cur, DM, outline=True)
    assert errors(found) == [] and "lessons.brief_missing" in warnings(found)


def test_outline_title_must_be_unique(outline_cur):
    mutate(outline_cur, SL, lambda l: l.__setitem__("title", "domain model"))
    found = check(outline_cur, outline=True)
    assert [f.location for f in found if f.check_id == "lessons.title_duplicate"] == [
        f"lessons/{item_id_to_filename(DM)}", f"lessons/{item_id_to_filename(SL)}"]
    mutate(outline_cur, SL, lambda l: l.__setitem__("title", "Domain Logic"))  # the area's title
    assert "lessons.title_duplicate" in errors(check_item(outline_cur, SL, outline=True))


def test_outline_prerequisites_follow_committed_edges(outline_cur):
    mutate(outline_cur, SL, lambda l: l.__setitem__("prerequisites", []))
    found = check_item(outline_cur, SL, outline=True)
    assert errors(found) == [] and "lessons.prerequisite_missing" in warnings(found)
    mutate(outline_cur, DM, lambda l: l.__setitem__("prerequisites", [SL]))
    assert "lessons.prerequisite_not_committed" in errors(check_item(outline_cur, DM, outline=True))


def test_outline_page_units_in_group_scope(outline_cur):
    mutate(outline_cur, DM, lambda l: l["pages"][1].__setitem__("unit_ids", ["u:clean-arch:4:0"]))
    assert "lessons.unit_out_of_group" in errors(check_item(outline_cur, DM, outline=True))
    groups = read_json(outline_cur / "groups.json")
    groups["groups"][0]["unit_ids"].append("u:peaa:99:9")
    write_json(outline_cur / "groups.json", groups)
    mutate(outline_cur, DM, lambda l: l["pages"][1].__setitem__("unit_ids", ["u:peaa:99:9"]))
    assert "lessons.unit_unknown" in errors(check_item(outline_cur, DM, outline=True))


def test_outline_schema_and_identity(outline_cur):
    mutate(outline_cur, DM, lambda l: l["pages"][2].__setitem__("index", 7))
    assert "lessons.outline.index" in errors(check_item(outline_cur, DM, outline=True))
    mutate(outline_cur, DM, lambda l: l["pages"][2].pop("brief_item_ids"))
    assert "lessons.schema" in errors(check_item(outline_cur, DM, outline=True))
    mutate(outline_cur, DM, lambda l: l.__setitem__("lesson_id", SL))
    assert "lessons.lesson_id_mismatch" in errors(check_item(outline_cur, DM, outline=True))
    mutate(outline_cur, DM, lambda l: l["objectives"].append({"id": f"{SL}#lo1", "text": "x", "bloom_level": "fly"}))
    found = errors(check_item(outline_cur, DM, outline=True))
    assert "lessons.objective_unknown" in found and "lessons.objective_bloom" in found


def test_outline_unknown_and_stray_lessons(outline_cur):
    assert check_item(outline_cur, SL, outline=True) == []
    assert "lessons.unknown_lesson" in errors(check_item(outline_cur, "g:nope", outline=True))
    write_json(outline_cur / "lessons" / "g_stray.json", {"lesson_id": "g:stray", "pages": []})
    write_json(outline_cur / "lessons" / "anchor.json", {"lesson_id": DM})
    found = check(outline_cur, outline=True)
    assert errors(found) == ["lessons.unknown_lesson"]
    assert "anchor" not in " ".join(f.location for f in found)


# ---------------------------------------------------------------------------
# Full checks
# ---------------------------------------------------------------------------

PAGE_METRIC_KEYS = {"index", "role", "title", "words", "tokens", "bold_terms", "new_terms", "undefined_terms", "undefined_ratio",
                    "introduced_terms", "term_density", "sentences", "mean_sentence_words", "long_sentences",
                    "max_paragraph_sentences", "has_example", "verbatim", "furniture_matches", "dangling_references",
                    "mid_sentence_start", "image_ids"}


def test_clean_full_lessons_pass_and_metrics_are_written(cur):
    assert check(cur) == []
    metrics = read_json(cur / LESSON_METRICS_FILE)
    assert metrics["contract_version"] == CONTRACT_VERSION and set(metrics["lessons"]) == {DM, SL}
    entry = metrics["lessons"][SL]
    assert entry["page_count"] == 5 and 900 <= entry["words"] <= 2400 and entry["errors"] == 0
    assert len(entry["pages"]) == 5
    for page in entry["pages"]:
        assert PAGE_METRIC_KEYS <= set(page)
        assert page["verbatim"] is not None and page["verbatim"]["max_fragment"] < 8
    core = entry["pages"][1]
    assert core["role"] == "core" and 0.08 <= core["term_density"] <= 0.15
    assert set(core["introduced_terms"]) == {"service layer", "boundary"}
    assert entry["pages"][3]["items"] == 3 and entry["pages"][4]["items"] == 4
    assert entry["pages"][0]["image_ids"] == [IMAGE_ID]


def test_lesson_words_gate(cur):
    def shrink(lesson):
        for page in lesson["pages"]:
            page["markdown"] = _trim(page["markdown"], 120)
    mutate(cur, DM, shrink)
    assert "lessons.lesson_words" in errors(check_item(cur, DM))


def test_page_words_warning_within_tolerance_then_error(cur):
    lo, _ = PAGE_WORDS["core"]
    mutate(cur, SL, lambda l: l["pages"][1].__setitem__("markdown", _trim(l["pages"][1]["markdown"], int(lo * 0.92))))
    found = check_item(cur, SL)
    assert "lessons.page_words" in warnings(found) and "lessons.page_words" not in errors(found)
    mutate(cur, SL, lambda l: l["pages"][1].__setitem__("markdown", _trim(l["pages"][1]["markdown"], int(lo * 0.6))))
    assert "lessons.page_words" in errors(check_item(cur, SL))


def test_new_bold_terms_per_page(cur):
    terms = ["Alpha", "Beta", "Gamma", "Delta", "Epsilon"]
    extra = " ".join(f"**{t}** is a label for case {i}." for i, t in enumerate(terms[:NEW_TERMS_PER_PAGE_MAX]))
    mutate(cur, DM, lambda l: l["pages"][0].__setitem__("markdown", l["pages"][0]["markdown"] + "\n\n" + extra))
    assert "lessons.new_terms" not in errors(check_item(cur, DM))
    extra = " ".join(f"**{t}** is a label for case {i}." for i, t in enumerate(terms))
    mutate(cur, DM, lambda l: l["pages"][0].__setitem__("markdown", domain_model_pages()[0] + "\n\n" + extra))
    assert "lessons.new_terms" in errors(check_item(cur, DM))


def test_term_density_warns_below_and_above(cur):
    # Below: a core page that bolds nothing introduces no registry term (the orientation already names them).
    mutate(cur, DM, lambda l: l["pages"][1].__setitem__("markdown", l["pages"][1]["markdown"].replace("**", "")))
    found = [f for f in check_item(cur, DM) if f.check_id == "lessons.term_density"]
    assert len(found) == 1 and found[0].severity == "warning" and "<" in found[0].message
    # Above: hammer the introduced terms.
    dense = " ".join(["The domain model holds behaviour, and the domain model holds data."] * 12)
    mutate(cur, DM, lambda l: l["pages"][1].__setitem__("markdown", domain_model_pages()[1] + "\n\n" + dense))
    found = [f for f in check_item(cur, DM) if f.check_id == "lessons.term_density"]
    assert found and "add an example/elaboration" in found[0].message


def test_undefined_bold_terms(cur):
    undefined = "**Widget** flies. **Gadget** flies. **Gizmo** flies. **Doohickey** flies."
    mutate(cur, DM, lambda l: l["pages"][0].__setitem__("markdown", l["pages"][0]["markdown"] + "\n\n" + undefined))
    found = [f for f in check_item(cur, DM) if f.check_id == "lessons.undefined_terms"]
    assert found and found[0].severity == "error" and "widget" in found[0].message
    # A term defined in the prerequisite's recap, or bolded on an earlier page, is not undefined.
    mutate(cur, DM, lambda l: l["pages"][0].__setitem__("markdown", domain_model_pages()[0]))
    mutate(cur, SL, lambda l: l["pages"][1].__setitem__("markdown", l["pages"][1]["markdown"] + "\n\nA **data holder** cannot help here. The **service layer** can."))
    assert "lessons.undefined_terms" not in errors(check_item(cur, SL))


def test_sentence_length_gates(cur):
    long_sentence = "The service layer " + "keeps coordinating request after request " * 10 + "until the month closes."
    mutate(cur, SL, lambda l: l["pages"][1].__setitem__("markdown", l["pages"][1]["markdown"] + "\n\n" + long_sentence))
    found = check_item(cur, SL)
    assert "lessons.sentence_long" in ids(found, "info")
    wordy = "\n\n".join([" ".join(["The service layer coordinates every request that crosses the boundary and names each goal"] * 3) + "."] * 5)
    mutate(cur, SL, lambda l: l["pages"][1].__setitem__("markdown", "## The boundary\n\n" + wordy))
    assert "lessons.sentence_mean" in warnings(check_item(cur, SL))


def test_paragraph_sentence_gate(cur):
    para = " ".join(["The boundary holds."] * 6)
    mutate(cur, SL, lambda l: l["pages"][1].__setitem__("markdown", l["pages"][1]["markdown"] + "\n\n" + para))
    assert "lessons.paragraph_long" in warnings(check_item(cur, SL))


def test_example_marker_required_on_long_pages(cur):
    def strip_markers(lesson):
        md = lesson["pages"][1]["markdown"]
        for marker in ("For example", "In practice"):
            md = md.replace(marker, "Usually")
        lesson["pages"][1]["markdown"] = md
    mutate(cur, SL, strip_markers)
    assert "lessons.example_missing" in errors(check_item(cur, SL))
    # a fenced code block counts as an example
    mutate(cur, SL, lambda l: l["pages"][1].__setitem__("markdown", l["pages"][1]["markdown"] + "\n\n```java\nclose();\n```"))
    assert "lessons.example_missing" not in errors(check_item(cur, SL))


def test_verbatim_gate_per_page(cur):
    copied = ("A Domain Model is an object model of the domain that incorporates both behaviour and data. Each "
              "object represents something meaningful in the business and carries the rules that apply to it.")
    mutate(cur, DM, lambda l: l["pages"][1].__setitem__("markdown", l["pages"][1]["markdown"] + "\n\n" + copied))
    found = check_item(cur, DM)
    assert "lessons.verbatim.fragment" in errors(found)
    assert all(f.location.endswith("pages[1]") for f in found if f.check_id.startswith("lessons.verbatim"))


def test_furniture_gate(cur):
    mutate(cur, DM, lambda l: l["pages"][1].__setitem__("markdown", l["pages"][1]["markdown"] + "\n\n142\n\nFrom the Library of Someone."))
    found = [f for f in check_item(cur, DM) if f.check_id == "lessons.furniture"]
    assert found and found[0].severity == "error" and "142" in found[0].message


def test_dangling_reference_gate(cur):
    mutate(cur, DM, lambda l: l["pages"][1].__setitem__("markdown", l["pages"][1]["markdown"] + "\n\nAs we saw earlier, the rule moves."))
    assert "lessons.dangling_reference" in errors(check_item(cur, DM))
    mutate(cur, DM, lambda l: l["pages"][1].__setitem__("markdown", domain_model_pages()[1] + "\n\nFigure 3-2 shows the split."))
    assert "lessons.dangling_reference" in errors(check_item(cur, DM))
    # a Figure mention is fine on a page that places an image
    mutate(cur, SL, lambda l: l["pages"][0].__setitem__("markdown", l["pages"][0]["markdown"] + "\n\nFigure 3-2 shows the boundary."))
    assert "lessons.dangling_reference" not in errors(check_item(cur, SL))


def test_mid_sentence_page_starts(cur):
    mutate(cur, SL, lambda l: l["pages"][1].__setitem__("markdown", "and so " + l["pages"][1]["markdown"]))
    found = [f for f in check_item(cur, SL) if f.check_id == "lessons.mid_sentence_start"]
    assert len(found) == 1 and found[0].location.endswith("pages[1]")
    mutate(cur, SL, lambda l: l["pages"][1].__setitem__("markdown", service_layer_pages()[1]))
    mutate(cur, SL, lambda l: l["pages"][0].__setitem__("markdown", l["pages"][0]["markdown"].rstrip(".") + " and"))
    found = [f for f in check_item(cur, SL) if f.check_id == "lessons.mid_sentence_start"]
    assert len(found) == 1 and found[0].location.endswith("pages[1]") and "previous page" in found[0].message
    # a page that ends with a code fence or an image placeholder is a clean end
    mutate(cur, SL, lambda l: l["pages"][0].__setitem__("markdown", service_layer_pages()[0] + "\n\n```java\nx();\n```"))
    assert "lessons.mid_sentence_start" not in errors(check_item(cur, SL))


def test_unknown_image_placeholder(cur):
    mutate(cur, SL, lambda l: l["pages"][0].__setitem__("markdown", l["pages"][0]["markdown"].replace(f"image:{IMAGE_ID}", "image:nope")))
    assert "lessons.unknown_image" in errors(check_item(cur, SL))


def test_pitfalls_item_count(cur):
    mutate(cur, SL, lambda l: l["pages"][3].__setitem__("markdown", l["pages"][3]["markdown"].replace("### ", "")))
    found = [f for f in check_item(cur, SL) if f.check_id == "lessons.pitfalls_count"]
    assert found and found[0].severity == "error" and found[0].message.startswith("0 misconception")
    # bullets count too: five top-level bullets exceed the range
    bullets = "\n".join(f"- Misconception {i}: a wrong idea. Remediation: the right idea." for i in range(5))
    mutate(cur, SL, lambda l: l["pages"][3].__setitem__("markdown", "## Delivery independence\n\nFive items follow. For example, consider these.\n\n" + bullets))
    assert "lessons.pitfalls_count" in errors(check_item(cur, SL))


def test_recap_restates_generalization_and_lists_takeaways(cur):
    mutate(cur, DM, lambda l: l["pages"][4].__setitem__("markdown", l["pages"][4]["markdown"].replace(GENERALIZATION[DM], "A model binds rules to data.")))
    assert "lessons.recap_generalization" in errors(check_item(cur, DM))
    def one_bullet(lesson):
        md = lesson["pages"][4]["markdown"]
        lines = [l for l in md.splitlines() if not l.startswith("- ")][:]
        lesson["pages"][4]["markdown"] = "\n".join(lines).replace("Keep these takeaways in mind as you move on.",
                                                                  "Keep these takeaways in mind as you move on.\n\n- The model binds behaviour to data.")
    mutate(cur, DM, one_bullet)
    found = check_item(cur, DM)
    assert "lessons.recap_takeaways" in warnings(found)
    # markdown emphasis around the generalization still counts as verbatim
    mutate(cur, DM, lambda l: l["pages"][4].__setitem__("markdown", domain_model_pages()[4].replace(GENERALIZATION[DM], f"**{GENERALIZATION[DM]}**")))
    assert "lessons.recap_generalization" not in errors(check_item(cur, DM))


def test_estimated_minutes_sanity(cur):
    mutate(cur, SL, lambda l: l.__setitem__("estimated_minutes", 20))
    assert "lessons.estimated_minutes" in warnings(check_item(cur, SL))
    mutate(cur, SL, lambda l: l.__setitem__("estimated_minutes", "soon"))
    assert "lessons.estimated_minutes" in warnings(check_item(cur, SL))


def test_full_check_flags_unwritten_pages_and_unapproved_outline(cur):
    mutate(cur, SL, lambda l: l["pages"][2].__setitem__("markdown", None))
    found = check(cur)
    assert errors(found) == ["lessons.markdown_missing"]
    assert set(read_json(cur / LESSON_METRICS_FILE)["lessons"]) == {DM}
    mutate(cur, DM, lambda l: l.__setitem__("outline_approved", False))
    assert "lessons.outline_not_approved" in warnings(check_item(cur, DM))


def test_missing_lesson_and_partial_metrics_merge(cur):
    assert check(cur) == []
    lesson_path(cur, SL).unlink()
    found = check(cur)
    assert errors(found) == ["lessons.missing"]
    assert set(read_json(cur / LESSON_METRICS_FILE)["lessons"]) == {DM}
    write_lessons(cur)
    check_item(cur, SL)  # partial run merges into the existing file
    assert set(read_json(cur / LESSON_METRICS_FILE)["lessons"]) == {DM, SL}


def test_missing_inputs_are_findings_not_tracebacks(cur):
    (cur / "groups.json").unlink()
    assert errors(check(cur)) == ["lessons.groups_missing"]
    assert errors(prepare(cur)) == ["lessons.groups_missing"]
    result, found = build(cur)
    assert result is None and errors(found) == ["lessons.groups_missing"]


def test_invalid_lesson_json_is_a_finding(cur):
    lesson_path(cur, SL).write_text("{not json", encoding="utf-8")
    assert errors(check_item(cur, SL)) == ["lessons.invalid_json"]


# ---------------------------------------------------------------------------
# prepare
# ---------------------------------------------------------------------------

WORK_KEYS = {"contract_version", "stage", "lesson_id", "group_id", "pass", "file", "group", "brief", "concepts", "neighbours",
             "images", "page_template", "unassigned_knowledge_points", "anchor", "outline", "lessons_learned",
             "instructions", "thresholds"}


def test_prepare_outline_pass(tmp_path):
    cur = build_curriculum(tmp_path)
    found = prepare(cur)
    assert errors(found) == [] and ids(found, "info").count("lessons.prepare.written") == 2
    sl = read_json(cur / "lessons" / "work" / item_id_to_filename(SL))
    dm = read_json(cur / "lessons" / "work" / item_id_to_filename(DM))
    assert WORK_KEYS <= set(sl) and sl["pass"] == "outline" and sl["outline"] is None
    assert sl["file"] == f"lessons/{item_id_to_filename(SL)}"
    assert [p["role"] for p in sl["page_template"]] == ["orientation", "core", "worked_example", "pitfalls", "recap"]
    assert [p["knowledge_point_id"] for p in sl["page_template"]] == [None, f"{SL}#kp1", f"{SL}#kp2", f"{SL}#kp3", None]
    assert [p["role"] for p in dm["page_template"]] == ["orientation", "core", "core", "pitfalls", "recap"]
    assert sl["page_template"][1]["words"] == list(PAGE_WORDS["core"]) and sl["page_template"][1]["title_hint"] == "The boundary"
    assert sl["group"]["budget"]["pages"] == 5 and sl["group"]["generalization"] == GENERALIZATION[SL]
    assert sl["group"]["prerequisites"] == [{"group_id": DM, "strength": "required", "status": "committed", "reason": "assumes the domain model"}]
    assert sl["brief"]["examples"][0]["text"].startswith("```java") and sl["brief"]["terminology"][0]["term"] == "anemic domain model"
    assert {c["label"] for c in sl["concepts"]} == {"Service Layer", "Application Logic"}
    assert sl["images"] == [{"id": IMAGE_ID, "unit_id": "u:peaa:12:0", "local_path": f"images/{IMAGE_ID}.png",
                             "alt_text": "Service layer boundary diagram", "mime_type": "image/png",
                             "placeholder": f"![Service layer boundary diagram](image:{IMAGE_ID})"}]
    assert sl["neighbours"] == {"prerequisites": [{"lesson_id": DM, "title": "Domain Model", "strength": "required",
                                                   "status": "committed", "recap_markdown": None}], "next": []}
    assert dm["neighbours"] == {"prerequisites": [], "next": ["Service Layer"]}
    assert "lesson" in sl["instructions"].lower() and sl["thresholds"]["page_words"]["core"] == list(PAGE_WORDS["core"])
    assert sl["anchor"] is None and sl["lessons_learned"] == []


def test_prepare_full_pass_with_outline_recaps_and_anchor(cur):
    mutate(cur, SL, lambda l: l.__setitem__("lessons_learned", ["define boundary before mechanism"]))
    write_json(cur / "lessons" / "anchor.json", {"lesson_id": DM})
    found = prepare(cur)
    assert errors(found) == []
    sl = read_json(cur / "lessons" / "work" / item_id_to_filename(SL))
    assert sl["pass"] == "full" and sl["outline"]["lesson_id"] == SL
    assert sl["neighbours"]["prerequisites"][0]["recap_markdown"].startswith("## Recap")
    assert sl["lessons_learned"] == ["define boundary before mechanism"]
    assert sl["anchor"]["lesson_id"] == DM and sl["anchor"]["pages"][0]["markdown"]
    dm = read_json(cur / "lessons" / "work" / item_id_to_filename(DM))
    assert dm["anchor"] is None  # the anchor is not handed to itself
    assert check(cur) == []        # anchor.json is not a lesson


def test_prepare_skips_existing_unless_pass_changes_or_force(cur):
    prepare(cur)
    found = prepare(cur)
    assert ids(found, "info").count("lessons.prepare.skipped") == 2
    found = prepare(cur, lesson_ids=[SL], pass_="outline")
    assert ids(found, "info") == ["lessons.prepare.written"]
    assert read_json(cur / "lessons" / "work" / item_id_to_filename(SL))["pass"] == "outline"
    found = prepare(cur, lesson_ids=[SL], force=True)
    assert ids(found, "info") == ["lessons.prepare.written"]
    assert "lessons.unknown_lesson" in errors(prepare(cur, lesson_ids=["g:nope"]))


def test_prepare_warns_on_kp_overflow_and_missing_brief(tmp_path):
    cur = build_curriculum(tmp_path)
    groups = read_json(cur / "groups.json")
    sl = groups["groups"][1]
    sl["knowledge_points"] += [{"id": f"{SL}#kp4", "title": "Four", "unit_ids": []}, {"id": f"{SL}#kp5", "title": "Five", "unit_ids": []}]
    sl["budget"] = budget_for(5, 2, "building_block", True)
    write_json(cur / "groups.json", groups)
    (cur / "syntheses" / item_id_to_filename(DM)).unlink()
    found = prepare(cur)
    assert "lessons.prepare.kp_overflow" in warnings(found) and "lessons.prepare.brief_missing" in warnings(found)
    work = read_json(cur / "lessons" / "work" / item_id_to_filename(SL))
    assert work["unassigned_knowledge_points"] == [f"{SL}#kp5"] and len(work["page_template"]) == 6
    assert read_json(cur / "lessons" / "work" / item_id_to_filename(DM))["brief"] is None


# ---------------------------------------------------------------------------
# build
# ---------------------------------------------------------------------------

CHUNK_METADATA_KEYS = {"lesson_id", "page_role", "lo_ids", "source_units", "key_terms", "contract_version"}


def test_build_writes_structure_and_chunks_that_validate(cur, capsys):
    result, found = build(cur)
    assert result is not None and errors(found) == []
    assert not [f for f in validate.check_structure(cur) if f.severity == "error"]
    assert not [f for f in validate.check_chunks(cur) if f.severity == "error"]

    structure = read_json(cur / "structure.json")
    area = structure[0]
    assert area["depth"] == 0 and area["suggested_level"] == 1 and area["title"] == "Domain Logic"
    module = area["children"][0]
    assert module["depth"] == 1 and module["title"] == "Patterns"
    leaves = module["children"]
    assert [l["lesson_id"] for l in leaves] == [DM, SL] and all(l["depth"] == 2 for l in leaves)
    assert [l["sort_order"] for l in leaves] == [0, 1]
    assert leaves[1]["prerequisites"] == [{"topic": "Domain Model", "strength": "required"}] and leaves[0]["prerequisites"] == []
    assert leaves[1]["source_sections"] == [] and leaves[1]["learning_objectives"][0] == {
        "text": "Describe what a service layer coordinates", "bloom_level": "understand"}
    assert leaves[0]["description"] == "Rules live with the objects they govern."

    chunks = read_json(cur / "chunks.json")
    assert len(chunks) == 10
    sl_chunks = [c for c in chunks if c["metadata"]["lesson_id"] == SL]
    assert [c["chunk_index"] for c in sl_chunks] == [0, 1, 2, 3, 4]
    assert all(c["topic_path"] == ["Domain Logic", "Patterns", "Service Layer"] for c in sl_chunks)
    assert all(set(c["metadata"]) == CHUNK_METADATA_KEYS and c["page_number"] is None for c in chunks)
    assert sl_chunks[2]["has_code"] and sl_chunks[2]["metadata"]["page_role"] == "worked_example"
    assert sl_chunks[2]["metadata"]["source_units"] == ["u:peaa:12:1", "u:peaa:12:2"]
    assert sl_chunks[0]["images"] == [{"id": IMAGE_ID, "local_path": f"images/{IMAGE_ID}.png",
                                       "alt_text": "Service layer boundary diagram", "mime_type": "image/png"}]
    assert sl_chunks[1]["images"] == []
    assert all(c["token_count"] == count_tokens(c["content"]) for c in chunks)
    assert sl_chunks[0]["metadata"]["key_terms"] == ["boundary", "coordination"]

    table = lessons.format_summary(result["rows"])
    assert DM in table and SL in table and "2 lesson(s), 10 chunk(s)" in table
    assert main(["build", str(cur)]) == 0
    out = capsys.readouterr().out
    assert "lessons.build.written" in out and "Prereqs" in out


def test_build_is_blocked_by_check_errors_unless_allowed(cur):
    mutate(cur, SL, lambda l: l["pages"][0].__setitem__("markdown", l["pages"][0]["markdown"].replace(f"image:{IMAGE_ID}", "image:nope")))
    result, found = build(cur)
    assert result is None and "lessons.build.blocked" in errors(found) and "lessons.unknown_image" in errors(found)
    assert not (cur / "structure.json").exists()
    assert main(["build", str(cur)]) == 1
    result, found = build(cur, allow_failing=True)
    assert result is not None and "lessons.build.allow_failing" in warnings(found)
    assert (cur / "chunks.json").exists()


def test_build_requires_full_pages(outline_cur):
    result, found = build(outline_cur)
    assert result is None and errors(found).count("lessons.build.outline_only") == 2
    write_lessons(outline_cur)
    lesson_path(outline_cur, DM).unlink()
    result, found = build(outline_cur)
    assert result is None and errors(found) == ["lessons.build.lesson_missing"]


def test_build_without_modules_and_without_reduced_edges(cur):
    groups = read_json(cur / "groups.json")
    for g in groups["groups"]:
        g["module_id"] = None
    groups.pop("prerequisite_edges_reduced")
    write_json(cur / "groups.json", groups)
    result, found = build(cur)
    assert result is not None
    structure = read_json(cur / "structure.json")
    leaves = structure[0]["children"]
    assert [l["depth"] for l in leaves] == [1, 1] and leaves[1]["prerequisites"] == [{"topic": "Domain Model", "strength": "required"}]
    assert read_json(cur / "chunks.json")[0]["topic_path"] == ["Domain Logic", "Domain Model"]
    assert not [f for f in validate.check_structure(cur) if f.severity == "error"]
    # an empty reduction drops every edge from the structure
    groups["prerequisite_edges_reduced"] = []
    write_json(cur / "groups.json", groups)
    build(cur)
    assert read_json(cur / "structure.json")[0]["children"][1]["prerequisites"] == []


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_main_check_formats_findings_and_exit_codes(cur, capsys):
    assert main(["check", str(cur)]) == 0
    assert capsys.readouterr().out.strip().endswith("0 errors, 0 warnings")
    assert main(["check", str(cur), "--outline", "--lesson", SL]) == 0
    capsys.readouterr()
    mutate(cur, SL, lambda l: l["pages"][0].__setitem__("markdown", l["pages"][0]["markdown"].replace(f"image:{IMAGE_ID}", "image:nope")))
    mutate(cur, SL, lambda l: l.__setitem__("estimated_minutes", 40))
    assert main(["check", str(cur), "--lesson", SL]) == 1
    out = capsys.readouterr().out
    assert f"[ERROR] lessons.unknown_image @ lessons/{item_id_to_filename(SL)} pages[0]:" in out
    assert "[WARN] lessons.estimated_minutes @" in out and out.strip().endswith("1 errors, 1 warnings")
    mutate(cur, SL, lambda l: l["pages"][0].__setitem__("markdown", service_layer_pages()[0]))
    assert main(["check", str(cur)]) == 0
    assert main(["check", str(cur), "--strict"]) == 1
    assert main(["check", str(cur / "nope")]) == 2


def test_main_prepare(tmp_path, capsys):
    cur = build_curriculum(tmp_path)
    assert main(["prepare", str(cur), "--lesson", SL, "--pass", "outline"]) == 0
    assert "[INFO] lessons.prepare.written @ lessons/work/" in capsys.readouterr().out
    assert (cur / "lessons" / "work" / item_id_to_filename(SL)).exists()
    assert not (cur / "lessons" / "work" / item_id_to_filename(DM)).exists()
