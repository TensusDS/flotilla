"""A seat on a borrowed model (fleet model design 5.3).

The orchestrator - or the person - lends a model other than the fleet's default to one task: one seat of a post,
raised on that model, for a row's branch or a question with no row (`--for`). It owns no work row: like a helper it
works beside the row it is for, in a tree cut from that branch's tip (trunk for a question), and the row stays with
its owner. Its first prompt is the brief it was given (`--task`). Its subagents are held to the fleet's model by the
two settings Claude Code documents for that, carried in its `--settings`. It says it is done with `flotilla helper
done`, as a helper does; then, or once the row it is for closes, `watch` tells the orchestrator to retire it -
flotilla never retires it by itself.
"""

from __future__ import annotations

import time

from flotilla.core import paths
from flotilla.fleet import launch, plugins, spawn
from flotilla.fleet import model as fleet_model
from flotilla.ledger import gitq
from flotilla.ledger.errors import MoveRefused
from flotilla.posts import PostError, post_for_session

#: Claude Code's settings that hold every subagent to one model; the force takes from 2.1.257 on (documented).
SUBAGENT_MODEL, SUBAGENT_FORCE = "CLAUDE_CODE_SUBAGENT_MODEL", "CLAUDE_CODE_SUBAGENT_MODEL_FORCE"
FORCE_SINCE = (2, 1, 257)
FOR_LONGEST = 200


#: Posts never lent another model: the orchestrator routes the fleet and may borrow, so a borrowed one could lend
#: models by its own choice (review of 5ef2dd7, M2); a helper is raised by the seat it helps.
NOT_LENT = ("orchestrator", spawn.HELPER)


def checked(*, model: str, for_task: str, brief: str, own: str, post: str = "",
            may=frozenset({"claim"})) -> tuple[str, str, str]:
    """The model, the task and the brief as given, or the refusal that names what is missing. `own` is the model
    a seat of the post runs on without --model: the post's own, or the fleet's; `may` the post's moves. Only a
    working post is lent - one that may claim, whose claims a borrowed seat is refused: a borrowed reviewer, judge
    or sender would do its post's ordinary work on the costlier model, and a sender is one copy (re-review of
    61483de)."""
    from flotilla.core.text import has_hidden
    model, for_task, brief = model.strip(), for_task.strip(), brief.strip()
    if post in NOT_LENT or "claim" not in may:
        raise MoveRefused(f"a seat of the `{post}` post is never raised on a borrowed model; lend it to a working "
                          "seat, a post that may claim, such as `main`")
    if not fleet_model.valid(model):
        raise MoveRefused(f"`{model}` is not a model's name; name one, such as `fable` or `sonnet`")
    if fleet_model.same(model, own):
        raise MoveRefused(f"`{model}` is what a seat of this post runs on already ({own}); raise it without --model")
    if not for_task:
        raise MoveRefused("a seat on a borrowed model is lent to one task: name it with --for, a row's branch or a "
                          "short text for a question with no row")
    if has_hidden(for_task) or len(for_task) > FOR_LONGEST:   # shown on the person's screen (review, M1)
        raise MoveRefused(f"--for names the task in one line of at most {FOR_LONGEST} characters; the brief goes "
                          "with --task")
    if not brief:
        raise MoveRefused("say what the seat is to do (--task): the person's words about the problem, what you "
                          "know of it, and whether to research, check or build")
    return model, for_task, brief


def caller_refusal(posts: dict, me) -> tuple[str, str]:
    """(who raises it, "") when the calling session holds the orchestrator post or a person calls; else ("", why
    not). Lending a costlier model is never a seat's own choice (fleet model design 4, anti-goals)."""
    try:
        post = post_for_session(posts, me.name) if me is not None and me.name else None
    except PostError:
        post = None
    if post is not None and post.name == "orchestrator":
        return me.name, ""
    from flotilla.core import caller
    refused = caller.person_refusal("raises a seat on a borrowed model")
    if refused:
        return "", f"a seat on another model is the orchestrator's move, or the person's: {refused}"
    return "the person", ""


def base_for(ledger, for_task: str) -> tuple[str, object | None]:
    """Where the seat's tree is cut: the tip of the open row `for_task` names, with that row; else trunk."""
    row = next((item for item in reversed(list(ledger.rows().values()))
                if item.branch == for_task and item.is_open and item.state != "reserved"), None)
    if row is None:
        return gitq.trunk_ref(ledger.root, ledger.trunk, run=ledger.run), None
    tip = gitq.branch_tip(ledger.root, row.branch, run=ledger.run)
    if tip is None:
        raise MoveRefused(f"git could not resolve the tip of `{row.branch}`; the seat starts from a tip someone can "
                          "name")
    return tip, row


def cap_warning(run) -> str:
    """Why the subagent cap may not hold on this Claude Code, or ""."""
    from flotilla.doctor import parse_version
    try:
        done = run(["claude", "--version"], capture_output=True, text=True, check=False, timeout=30)
        version = parse_version(done.stdout or "") if done.returncode == 0 else None
    except Exception:  # noqa: BLE001 - a version that cannot be read is said, not fatal
        version = None
    since = ".".join(map(str, FORCE_SINCE))
    if version is None:
        return (f"Claude Code's version could not be read; the seat's subagents are held to the fleet's model only "
                f"from {since} on")
    if version < FORCE_SINCE:
        return (f"Claude Code {'.'.join(map(str, version))} is older than {since}: the seat's subagents may run on "
                "its borrowed model, not the fleet's")
    return ""


def settings(narrowed: str, default: str) -> str:
    return plugins.with_env(narrowed, {SUBAGENT_MODEL: default, SUBAGENT_FORCE: "1"})


def prompt_for(name: str, post: str, *, model: str, default: str, by: str, for_task: str, row, brief: str,
               tree) -> str:
    if row is not None:
        where = (f"it was cut from the tip of `{row.branch}` (row {row.id}). That row stays with {row.owner or 'its '
                 'owner'}: you own no row, and you do not hand, merge or close it")
        task = f"the work of `{row.branch}`"
    else:
        where = "it was cut from trunk. You own no row of the ledger"
        task = f"\"{for_task}\""
    return (f"You are {name}, a seat of the post `{post}` raised by {by} on {model} - not the fleet's model, "
            f"{default} - for one task: {task}. The brief:\n\n{brief}\n\nWork only in your own tree, {tree}: "
            f"{where}. Report what you find to {by if by != 'the person' else 'the orchestrator'}, and ask it when "
            f"something is unclear. Your subagents run on {default}. When the task is done, or cannot be done, "
            "commit what you keep, run `flotilla helper done --summary \"<what you found or did, or why not>\"`, "
            "send the letter it prints, and stop: the orchestrator closes this seat. The flotilla:flotilla skill "
            "explains the rest.")


def evidence(*, for_task: str, row, by: str) -> dict:
    return {"borrowed": True, "for": for_task, "for_row": row.id if row is not None else "", "by": by}


def raise_borrowed(ledger, post_name: str, *, model: str, for_task: str, brief: str, by: str, census, store,
                   caller: str, wait: float = 30.0, poll: float = 1.0, sleep=time.sleep,
                   anyway: bool = False) -> tuple[spawn.Raised, list[str]]:
    """Raise one seat of `post_name` on `model` for `for_task`, with `brief` as its first prompt."""
    main = launch.main_checkout(ledger.root, run=ledger.run)
    default, _ = fleet_model.default(ledger.profile, state=paths.state_dir(), main=main)
    own = launch.model_for(fleet_model.resolved(ledger.profile, state=paths.state_dir(), main=main),
                           ledger.posts[post_name])
    model, for_task, brief = checked(model=model, for_task=for_task, brief=brief, own=own, post=post_name,
                                     may=ledger.posts[post_name].may)
    if not anyway:
        from flotilla import __version__
        split = spawn.release_split(ledger, running=__version__)
        if split:
            raise spawn.SpawnRefused(split)
    base, row = base_for(ledger, for_task)
    seats, warnings = spawn.plan(ledger, {post_name: 1}, census=census, store=store, reserve=True, anyway=anyway)
    seat = seats[0]
    narrow = plugins.narrowing(ledger.run, main)
    post = ledger.posts[seat.post]
    warnings = warnings + narrow.warnings([post]) + [line for line in (cap_warning(ledger.run),) if line]
    raised = spawn.raise_seat(ledger, seat, caller=caller, census=census, wait=wait, poll=poll, sleep=sleep,
                              base=base, model=model, settings_json=settings(narrow.settings_json(post), default),
                              evidence=evidence(for_task=for_task, row=row, by=by),
                              prompt=prompt_for(seat.name, seat.post, model=model, default=default, by=by,
                                                for_task=for_task, row=row, brief=brief, tree=seat.tree))
    return raised, warnings
