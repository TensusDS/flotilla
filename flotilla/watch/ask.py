"""The AskUserQuestion guard: only the orchestrator talks to the person (field test F12, F16, F17).

A background session nobody is attached to that asks the person waits for ever: there is no timeout, and the
permission broker does not cover a question. So a background session holding any post but the orchestrator's is
refused, with the route to follow: the question goes to the orchestrator, and the row it holds up records the wait.
An interactive session has a person in front of it, a session with no post is not the fleet's to govern, and the
orchestrator is the post that talks to the person: all three ask freely. What the guard could not ask lets the
question through, and says so.
"""

from __future__ import annotations

PERSON_FACING = ("orchestrator",)


def ask_verdict(ctx, cli: str = "flotilla") -> tuple[str, str]:
    """("deny", reason), ("note", context), or ("", "") to say nothing."""
    if ctx.sessions is None:
        return "note", (f"flotilla could not ask the census ({ctx.census_error}); if this is a background session "
                        "nobody is attached to, this question waits for ever: send it to the orchestrator instead")
    if ctx.me is None or ctx.me.kind != "background":
        return "", ""
    if ctx.ledger is None:
        return "note", (f"flotilla could not read the ledger ({ctx.ledger_error}), so your post is unknown; if you "
                        "hold a post other than the orchestrator's, send this question to the orchestrator instead")
    post = ctx.post(ctx.me.name)
    if post is None or post.name in PERSON_FACING:
        return "", ""
    orchestrators = sorted(name for name in ctx.live if ctx.post_of(name) in PERSON_FACING)
    lines = [f"flotilla: you are {ctx.me.name} ({post.name} post), a background session nobody is attached to: a "
             "question here waits for ever. Only the orchestrator talks to the person."]
    if orchestrators:
        lines.append(f"Send the question to {', '.join(orchestrators)} with SendMessage; the answer comes back the "
                     "same way.")
    else:
        lines.append("No orchestrator is alive: the wait below carries the question, and `flotilla watch` shows it "
                     "to the person.")
    lines.append(f'Record the wait on the row the question holds up: {cli} work wait <branch> --on "the person" '
                 '--why "<the question>".')
    return "deny", "\n".join(lines)
