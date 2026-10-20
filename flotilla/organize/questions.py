"""Organize's questions, through onboarding's question door (spec, section 4).

Each item's first question is take in / ignore / ignore the rest (or its kind's own answers); a chain or stack
taken in gets a second, its shape. Every question has 2-4 options and none is recommended - the person takes the
choice on themselves - and every question takes typed words: own words, which the session turns into steps the
person confirms. Rounds hold at most four questions, and a later round holds what the earlier answers opened.
"""

from __future__ import annotations

import hashlib
import json

from flotilla.core.text import visible
from flotilla.onboard.questions import MAX_PER_ROUND, AnswerError, _q, validate_answer
from flotilla.organize.items import Item, seen, shown
from flotilla.organize.walk import Walk

TAKE = [("take", "Take it in", "Bring it into the ledger; the fleet reviews and delivers it."),
        ("ignore", "Ignore it", "Leave it; flotilla does not touch it and does not ask again."),
        ("ignore-rest", "Ignore the rest", "Leave this and every item not answered yet.")]
FIRST = {
    "cache": [("exclude", "Exclude it", "Add it to .git/info/exclude, so it is never committed or asked about."),
              ("leave", "Leave it", "Leave it untracked as it is.")],
    "trunk": [("leave", "Leave as is", "Nothing moves; publish refuses while trunk is ahead with these, and you push "
                                       "them yourself."),
              ("move", "Move to a branch", "A new branch holds them and is taken in; trunk goes back to origin's."),
              ("drop", "Drop them", "Kept under refs/flotilla/dropped first; you run the drop yourself after !.")],
    "onboard": [("land", "Land with the profile", "Trunk takes these commits together with the profile."),
                ("own-branch", "A branch of their own", "They move to their own branch, taken in after publishing.")],
    "edits": [("discretion", "Claude organizes them", "Claude proposes branches by what each edit is about; you see "
                                                      "the plan once and it is carried out exactly."),
              ("leave", "Leave as is", "Nothing moves; the fleet does not see these edits."),
              ("words", "I say what goes where", "Tell Claude in your own words which edits go to which branch.")],
}
SHAPE_TEXT = {
    "separate": ("Separate works", "One row per pull request, each reviewed over its own commits, landing in order; "
                                   "flotilla changes each upper pull request's base to trunk on GitHub once the one "
                                   "below has landed."),
    "parts": ("One work in parts", "As separate works, linked as parts: an upper part is not queued before the one "
                                   "below is delivered, and the judge walks the whole path; bases change to trunk on "
                                   "GitHub as each part below lands."),
    "whole": ("One work whole", "One row on the top branch, one review over everything; flotilla changes its base to "
                                "trunk on GitHub, and closes the lower pull requests there once it lands."),
    "claude": ("Claude works it out", "Claude reads the pull requests and proposes one of the three shapes, with its "
                                      "reasons; you decide."),
}
YES_NO = [("yes", "Yes", "Carry it out exactly as shown."), ("no", "No", "Change nothing; ask me again next time.")]
TAKEABLE = ("pr", "chain", "stack", "branch")


def _first_options(found: Item) -> list[tuple]:
    if found.kind in TAKEABLE:
        if found.kind in ("chain", "stack") and not found.offered:
            return TAKE[1:]
        return TAKE
    return FIRST[found.kind]


def _answer(data: dict, qid: str) -> dict | None:
    return data["answers"].get(qid)


def _value(data: dict, qid: str) -> str:
    return (_answer(data, qid) or {}).get("value", "")


def _tree_state(found: Item, data: dict) -> str:
    """"" for an item that needs no tree yes; "ask", "yes" or "no" otherwise."""
    if not found.facts.get("needs_yes"):
        return ""
    return _value(data, f"tree:{found.facts['tree']}") or "ask"


def _header(found: Item) -> str:
    return {"cache": "Tool cache", "trunk": "Local trunk", "onboard": "With profile", "edits": "Edits"}.get(
        found.kind, "Take in?")


def _first(found: Item) -> dict:
    lines = "".join(f"\n  {shown(line, 200)}" for line in found.lines[:12])
    return _q(found.key, _header(found), shown(found.title, 300) + lines, _first_options(found), free_text=True)


#: The own-words vocabulary: each step's `do`, and the one field it takes with that field's kind. Nothing else is
#: accepted, so what the person is shown is everything a step holds (security review of e876731).
STEPS = {"take": ("shape", str), "ignore": None, "close": ("pr", int), "unstack": ("stack", int),
         "delete-branch": ("branch", str)}


def steps_errors(steps) -> list[str]:
    from flotilla.ledger.core import SAFE_BRANCH
    if not isinstance(steps, list) or not steps:
        return ["steps are a non-empty JSON list"]
    errors = []
    for index, step in enumerate(steps):
        if not isinstance(step, dict) or not isinstance(step.get("do"), str):
            errors.append(f"step {index + 1} is not an object with a `do`")
            continue
        wanted = STEPS.get(step["do"], "unknown")
        if wanted == "unknown":
            errors.append(f"step {index + 1}: `{visible(step['do'])}` is none of {', '.join(STEPS)}")
            continue
        fields = {"do"} | ({wanted[0]} if wanted else set())
        if set(step) != fields:
            errors.append(f"step {index + 1} ({step['do']}) takes exactly: {', '.join(sorted(fields))}")
            continue
        if wanted:
            name, kind = wanted
            value = step[name]
            if kind is int and (not isinstance(value, int) or isinstance(value, bool) or value <= 0):
                errors.append(f"step {index + 1}: `{name}` is a positive whole number")
            elif kind is str and not isinstance(value, str):
                errors.append(f"step {index + 1}: `{name}` is text")
            elif name == "branch" and not SAFE_BRANCH.fullmatch(value):
                errors.append(f"step {index + 1}: `{visible(value)}` is not a branch name flotilla files")
            elif name == "shape" and value not in ("separate", "parts", "whole"):
                errors.append(f"step {index + 1}: shape is separate, parts or whole")
    return errors


def plan_errors(plan) -> list[str]:
    from flotilla.ledger.core import SAFE_BRANCH
    if not isinstance(plan, list) or not plan:
        return ["a plan is a non-empty JSON list"]
    errors = []
    for index, entry in enumerate(plan):
        if not isinstance(entry, dict) or set(entry) != {"branch", "paths"}:
            errors.append(f"entry {index + 1} holds exactly `branch` and `paths`")
            continue
        if not isinstance(entry["branch"], str) or not SAFE_BRANCH.fullmatch(entry["branch"]):
            errors.append(f"entry {index + 1}: `{visible(str(entry['branch']))}` is not a branch name flotilla files")
        paths = entry["paths"]
        if not isinstance(paths, list) or not paths or not all(isinstance(path, str) and path for path in paths):
            errors.append(f"entry {index + 1}: `paths` is a non-empty list of paths")
    return errors


def _proposal_errors(proposal: dict) -> list[str]:
    errors = []
    if "steps" in proposal:
        errors += steps_errors(proposal["steps"])
    if "plan" in proposal:
        errors += plan_errors(proposal["plan"])
    return errors


#: Which part of a proposal each confirming question confirms: a yes holds for that content only.
CONFIRMS = {"steps": "steps", "plan": "plan", "proposal": "shape"}


def _part(proposal: dict, name: str):
    return {"shape": proposal.get("shape"), "why": proposal.get("why")} if name == "shape" else proposal.get(name)


def _digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def _bound(data: dict, key: str, suffix: str) -> bool:
    """Whether the person's answer to `<key>:<suffix>` was given to the proposal as it stands now (security
    review of 0dda1a3..02139b1: a proposal changed after the yes must be asked again)."""
    answer = _answer(data, f"{key}:{suffix}")
    proposal = data["proposals"].get(key) or {}
    return bool(answer) and answer.get("proposal") == _digest(_part(proposal, CONFIRMS[suffix]))


def confirmed(found: Item, data: dict, part: str):
    """The proposal's `steps` or `plan` the person said yes to, exactly as it stands; None otherwise."""
    suffix = part
    if _value(data, f"{found.key}:{suffix}") != "yes" or not _bound(data, found.key, suffix):
        return None
    if _proposal_errors(data["proposals"].get(found.key) or {}):
        return None
    return (data["proposals"].get(found.key) or {}).get(part)


def _quoted(value) -> str:
    """A field shown for consent: whole, quoted, its controls escaped - never cut, so nothing hides past an end."""
    return visible(json.dumps(value, ensure_ascii=False))


def shape_of(found: Item, data: dict) -> str:
    """The shape the person chose for a taken-in chain or stack, or ""."""
    if found.kind not in ("chain", "stack"):
        return ""
    if len(found.offered) == 1:
        return found.offered[0]
    shape = _value(data, f"{found.key}:shape")
    if shape != "claude":
        return shape
    return _value(data, f"{found.key}:proposal") if _bound(data, found.key, "proposal") else ""


def _follow_up(found: Item, data: dict) -> dict | None:
    """The next question this item's answers opened, if any is ready to ask."""
    first = _answer(data, found.key) or {}
    value, key = first.get("value"), found.key
    proposal = data["proposals"].get(key) or {}
    if _proposal_errors(proposal):
        return None   # never put a malformed proposal to the person; `waiting` says so
    if found.kind == "edits":   # the person's words about edits become a plan, never steps (final review, 2)
        if value in ("discretion", "words") and proposal.get("plan") and not _bound(data, key, "plan"):
            return _q(f"{key}:plan", "Plan", _plan_text(proposal), YES_NO, free_text=True)
        return None
    if value == "words" and "words" in first:
        return _q(f"{key}:steps", "Your words", _steps_text(proposal), YES_NO, free_text=True) \
            if proposal.get("steps") and not _bound(data, key, "steps") else None
    if value == "take" and found.kind in ("chain", "stack") and len(found.offered) > 1:
        if not _answer(data, f"{key}:shape"):
            return _q(f"{key}:shape", "Shape", "Are these separate works, one work in parts, one work whole, or should "
                                               "Claude work it out?",
                      [(shape, *SHAPE_TEXT[shape]) for shape in found.offered], free_text=True)
        if _value(data, f"{key}:shape") == "claude" and proposal.get("shape") and \
                not _bound(data, key, "proposal"):
            proposed = proposal["shape"]
            rest = [shape for shape in found.offered if shape not in ("claude", proposed)]
            why = shown(proposal.get("why") or "", 300)
            options = [(proposed, f"{SHAPE_TEXT[proposed][0]} (proposed)", f"Claude's reasons: {why}")] + \
                [(shape, *SHAPE_TEXT[shape]) for shape in rest]
            return _q(f"{key}:proposal", "Proposal", "Claude read the pull requests and proposes a shape. Which one?",
                      options, free_text=True)
    if found.kind == "edits" and value in ("discretion", "words") and proposal.get("plan") and \
            not _bound(data, key, "plan"):
        return _q(f"{key}:plan", "Plan", _plan_text(proposal), YES_NO, free_text=True)
    return None


def _plan_text(proposal: dict) -> str:
    lines = [f"  {_quoted(entry.get('branch', ''))}: {', '.join(_quoted(path) for path in entry.get('paths', []))}"
             for entry in proposal.get("plan") or []]
    return "Carry out this plan? Each branch is committed in its own tree and taken in:\n" + "\n".join(lines)


def _steps_text(proposal: dict) -> str:
    lines = ["  " + ", ".join(f"{name}={_quoted(value)}" for name, value in step.items())
             for step in proposal.get("steps") or []]   # held to STEPS: every key is one of its own names
    return "Claude read your words as these steps. Carry them out?\n" + "\n".join(lines)


def all_questions(walk: Walk, data: dict) -> list[dict]:
    """Every question that applies now, in order: a tree's yes before its items, first questions, follow-ups."""
    out, asked_trees = [], set()
    for found in walk.items:
        tree = _tree_state(found, data)
        if tree == "ask" and found.facts["tree"] not in asked_trees:
            asked_trees.add(found.facts["tree"])
            out.append(_q(f"tree:{found.facts['tree']}", "Other tree",
                          f"May organize look into the tree {shown(found.facts['tree'], 1000)}? Nobody works in it now; "
                          "its edits are then asked about like this tree's.",
                          [("yes", "Yes", "Read its edits and ask about them."),
                           ("no", "No", "Leave that tree alone.")], free_text=False))
    for found in walk.items:
        if _tree_state(found, data) not in ("", "yes") or _answer(data, found.key):
            continue
        out.append(_first(found))
    for found in walk.items:
        if _tree_state(found, data) in ("", "yes") and _answer(data, found.key):
            follow = _follow_up(found, data)
            if follow:
                out.append(follow)
    return out


def next_round(walk: Walk, data: dict) -> list[dict]:
    return all_questions(walk, data)[:MAX_PER_ROUND]


def record(walk: Walk, data: dict, qid: str, values: list[str]) -> str:
    question = next((q for q in all_questions(walk, data) if q["id"] == qid), None)
    if question is None:
        raise AnswerError(f"no question `{qid}` applies now; run `flotilla organize next`")
    allowed = {option["value"] for option in question["options"]}
    words = " ".join(value.strip() for value in values if value and value.strip())
    if len(values) > 1 and question["free_text"] and not set(values) <= allowed:
        values = [words]
    value = validate_answer(question, values)
    if qid.startswith("tree:"):
        tree = qid[len("tree:"):]
        data["answers"][qid] = {"value": value}
        if value == "yes" and tree not in data["trees"]:
            data["trees"].append(tree)
        return f"recorded {qid}: {value}"
    found = max((item for item in walk.items if qid == item.key or qid.startswith(f"{item.key}:")),
                key=lambda item: len(item.key))   # a key may hold a colon itself (a tree's path)
    key = found.key
    if value not in allowed:   # typed words: the session proposes steps from them, the person confirms
        data["answers"][qid] = {"value": "words", "words": value, "seen": seen(found)}
        return f"recorded {qid}: in your own words - Claude turns them into steps for your yes"
    if value == "ignore-rest":
        for other in walk.items:
            if not _answer(data, other.key) and other.key != key:
                data["answers"][other.key] = {"value": "ignore", "seen": seen(other)}
                data["ignored"][other.key] = seen(other)
        value = "ignore"
    data["answers"][qid] = {"value": value, "seen": seen(found)}
    suffix = qid[len(key) + 1:]
    if suffix in CONFIRMS:   # the yes is bound to the proposal it was given to
        data["answers"][qid]["proposal"] = _digest(_part(data["proposals"].get(key) or {}, CONFIRMS[suffix]))
    if qid == key and value == "ignore":
        data["ignored"][key] = seen(found)
    return f"recorded {qid}: {value}"


def waiting(walk: Walk, data: dict) -> list[str]:
    """What waits on the session, not the person: a proposal to write before the person can be asked."""
    out = []
    for found in walk.items:
        key, first = found.key, _answer(data, found.key) or {}
        proposal = data["proposals"].get(key) or {}
        errors = _proposal_errors(proposal)
        if errors:
            out.append(f"{key}: the proposal is malformed ({'; '.join(errors)}); propose it again")
        elif found.kind == "edits" and first.get("value") in ("discretion", "words") and not proposal.get("plan"):
            out.append(f"{key}: read the edits (`flotilla organize show {key}`) and propose a plan (`flotilla organize "
                       f"propose {key} --plan <file>`)" + (f" from the person's words: {_quoted(first['words'])}"
                                                           if first.get("words") else ""))
        elif first.get("value") == "words" and found.kind != "edits" and not proposal.get("steps"):
            out.append(f"{key}: turn the person's words into steps (`flotilla organize propose {key} --steps <file>`)")
        elif _value(data, f"{key}:shape") == "claude" and not proposal.get("shape"):
            out.append(f"{key}: read it (`flotilla organize show {key}`) and propose a shape (`flotilla organize "
                       f"propose {key} --shape <shape> --why \"...\"`)")
        elif found.kind == "edits" and first.get("value") in ("discretion", "words") and not proposal.get("plan"):
            out.append(f"{key}: read the edits (`flotilla organize show {key}`) and propose a plan (`flotilla organize "
                       f"propose {key} --plan <file>`)")
    return out


def done(walk: Walk, data: dict) -> bool:
    return not all_questions(walk, data) and not waiting(walk, data)
