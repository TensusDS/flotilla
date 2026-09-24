"""The onboarding questionnaire: which questions apply next, and whether an answer fits.

The skill asks; this module decides. `next_questions` returns at most four unanswered questions
that apply given what was detected and answered so far, in the order of spec section 4.3. Every
question fits AskUserQuestion: a header of at most 12 characters and 2 to 4 options. With
`free_text`, a typed value (AskUserQuestion's "Other") is accepted as the answer.
"""

from __future__ import annotations

MAX_PER_ROUND = 4


class AnswerError(ValueError):
    """An answer that does not fit its question."""


def _q(qid: str, header: str, question: str, options, *, multi: bool = False, free_text: bool = False) -> dict:
    return {"id": qid, "header": header, "question": question, "multi_select": multi, "free_text": free_text,
            "options": [{"value": v, "label": label, "description": d} for v, label, d in options]}


def all_questions(det: dict, answers: dict) -> list[dict]:
    remote = bool(det.get("remote"))
    ci = det.get("ci") or {}
    signals = det.get("signals") or {}
    release = det.get("release") or {}
    flow = answers.get("flow", None if remote else "local")
    out: list[dict] = []

    if remote:
        out.append(_q("flow", "Trunk path", "How does work reach trunk?", [
            ("pr-sender", "PR, sender merges (Recommended)",
             "The sender opens a pull request and merges it once the required checks are green."),
            ("pr-human", "PR, a human merges", "The sender opens the pull request; a person reviews and merges it."),
            ("direct", "Direct push",
             "For experienced users or simple projects: CI runs only after the code is already in trunk."),
            ("local", "Local only", "Work is merged locally and never pushed."),
        ]))
    if flow in ("pr-sender", "direct"):
        out.append(_q("merge_auth", "Merge auth", "Who authorizes a merge into trunk?", [
            ("human", "A human, per batch", "The sender shows the batch in one block and waits for a yes."),
            ("sender", "The sender", "The sender merges on its own once the push receipt is green."),
        ]))
    out.append(_q("review", "Review", "How much work goes through an independent reviewer?", [
        ("every", "Every branch", "Nothing is queued for merge until a reviewer accepts it over its exact revision."),
        ("main-only", "Main work only", "Large work is reviewed; small fixes from minor sessions go straight on."),
        ("none", "No review", "Branches go to the sender once handed over."),
    ]))
    out.append(_q("permissions", "Permissions", "How do background sessions get permission for their tools?", [
        ("ask", "They ask me", "Sessions stop and wait for you at every permission prompt."),
        ("rules", "Allow rules exist", "Your settings already allow the commands the fleet needs."),
        ("auto", "Auto mode", "Sessions run in auto mode and the classifier decides."),
    ]))
    ci_options = []
    if ci.get("provider") == "github":
        ci_options.append(("github", "GitHub Actions (detected)", "Required jobs are checked by name after a push."))
    ci_options += [
        ("command", "Own gate command", "A command that answers 0 green, 1 red, 2 pending for a revision."),
        ("none", "No CI", "Only the local push receipt stands behind shipped, and the batch says so."),
    ]
    out.append(_q("ci", "CI", "Which CI stands behind shipped?", ci_options))
    if answers.get("ci") in ("github", "command"):
        out.append(_q("ci_where", "CI runs on", "Where does that CI run?", [
            ("cloud", "Hosted runners", "CI runs elsewhere; it does not compete for this machine."),
            ("this-machine", "This machine", "Self-hosted here: the lane also waits for the CI queue."),
        ]))
    if answers.get("ci") == "command":
        out.append(_q("gate_command", "Gate cmd", "Which command reports CI for a revision? Type it as Other.", [
            ("ask-human", "Ask me each time", "No command; the sender asks you whether CI is green."),
            ("later", "Set it later", "Leave it empty for now; edit project.toml when ready."),
        ], free_text=True))
    detected = [(t["name"], t["name"], f"{t['command']}  (from {t['source']})") for t in det.get("tests") or []]
    tier_options = detected[:MAX_PER_ROUND] if detected else [
        ("none", "No tests", "Nothing runs before a handover or a push."),
        ("later", "Add them later", "Type a command as Other, or edit project.toml later."),
    ]
    if len(tier_options) == 1:
        tier_options.append(("none", "None of these", "Do not run this tier before shipping."))
    out.append(_q("tiers", "Test tiers", "Which test commands must be green before shipping? Type more as Other.",
                  tier_options, multi=True, free_text=True))
    out.append(_q("tracker", "Tasks", "Where are tasks tracked?", [
        ("nowhere", "Nowhere", "Closing a row needs no reference."),
        ("github-issues", "GitHub Issues", "Closing a row may name an issue like #42."),
        ("pattern", "Ticket ids", "Closing a row may name a ticket like ABC-123 (Jira, Linear)."),
        ("own-register", "Own register", "Type your id pattern as Other, a regular expression."),
    ], free_text=True))
    if signals.get("multi_repo"):
        out.append(_q("repos", "Push order", f"Sibling repositories found ({', '.join(signals['multi_repo'])}). "
                      "Which goes first?", [
            ("this-first", "This one first", "This repository is pushed before its siblings."),
            ("siblings-first", "Siblings first", "The siblings are pushed first; this one follows."),
        ]))
    if release.get("latest_tag") or release.get("version_files"):
        out.append(_q("release", "Versions", "Who moves the version and tags a release?", [
            ("sender-semver", "Sender, semver", "The sender bumps the version files and writes an annotated tag."),
            ("none", "Nobody", "flotilla leaves versions alone."),
        ]))
    if signals.get("deployment"):
        out.append(_q("deploy", "Deployment", "A deployment was found. What surface does a person use?", [
            ("web", "Web", "An acceptance judge walks the human path in a browser."),
            ("cli", "Command line", "An acceptance judge walks the human path in a terminal."),
            ("api", "API", "An acceptance judge walks the human path with HTTP calls."),
            ("none", "No judge", "No acceptance judge in the fleet."),
        ]))
    if signals.get("shared_files"):
        out.append(_q("shared", "Shared files", f"Files everyone appends to: {', '.join(signals['shared_files'])}. "
                      "Reserve rewrites?", [
            ("reserve", "Reserve rewrites", "A rewrite needs the file's reservation; appends always pass."),
            ("off", "No reservation", "Anyone may rewrite them."),
        ]))
    if signals.get("sequential"):
        out.append(_q("sequential", "Numbering", f"Numbered files live in {', '.join(signals['sequential'])}. "
                      "Claim numbers?", [
            ("claim", "Claim numbers", "Sessions claim the next number, so two branches never take the same one."),
            ("off", "No claims", "Numbers are chosen by hand."),
        ]))
    methods = ci.get("merge_methods") or []
    if flow in ("pr-sender", "pr-human") and len(methods) > 1:
        out.append(_q("merge_method", "Merge method", "Which merge method does the sender use?",
                      [(m, m.capitalize(), f"GitHub's {m} merge.") for m in methods[:MAX_PER_ROUND]]))
    out.append(_q("guards", "Guards", "Which command guards should be on? Each refuses one dangerous command.", [
        ("revert", "Revert guard", "Refuses a checkout, reset or clean that would destroy uncommitted work."),
        ("line_edit", "Line-number edit", "Refuses in-place edits addressed by line number, which go stale silently."),
        ("push_receipt", "Push receipt", "Refuses a push or merge without a green run over that exact revision."),
    ], multi=True))
    out.append(_q("model", "Models", "Which model runs each post?", [
        ("one", "One for all", "Every session uses the model you launch Claude Code with."),
        ("reviewer-strongest", "Strongest reviewer", "Reviewers run on the most capable model; others on yours."),
    ]))
    return out


def next_questions(det: dict, answers: dict, limit: int = MAX_PER_ROUND) -> list[dict]:
    return [q for q in all_questions(det, answers) if q["id"] not in answers][:limit]


def validate_answer(question: dict, values: list[str]):
    allowed = [o["value"] for o in question["options"]]
    values = [v.strip() for v in values if v and v.strip()]
    if not values:
        raise AnswerError(f"{question['id']}: an answer is required")
    for value in values:
        if value not in allowed and not question["free_text"]:
            raise AnswerError(f"{question['id']}: {value!r} is not one of {', '.join(allowed)}")
    if question["multi_select"]:
        return values
    if len(values) != 1:
        raise AnswerError(f"{question['id']}: one answer expected, got {len(values)}")
    return values[0]


def suggest_composition(answers: dict) -> dict[str, int]:
    composition = {"main": 1}
    if answers.get("review", "every") != "none":
        composition["review"] = 1
    if answers.get("deploy") in ("web", "cli", "api"):
        composition["judge"] = 1
    return composition
