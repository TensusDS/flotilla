"""Where a new session sits and how it is launched (spec, sections 7.1-7.2).

A seat is a sibling worktree of the main checkout, `<checkout>-<post>-<n>`, on branch `fleet/<post>-<n>`: the
session's home. The session is launched from the main checkout, which belongs to nobody (Claude Code's own
background guard refuses edits there and accepts them in any linked worktree), with its tree added by `--add-dir`.
Its name is given at birth (`-n`), its post text and the absolute path of the flotilla command line ride in the
system prompt, and its first prompt sends it to the `flotilla` skill. It is never asked to infer who it is.
"""

from __future__ import annotations

import json

import subprocess
from dataclasses import dataclass
from pathlib import Path

from flotilla.fleet.names import number_of

PERMISSION = {"ask": "manual", "rules": "dontAsk", "auto": "auto"}
STRONGEST = "opus"
CLI = Path(__file__).resolve().parents[2] / "scripts" / "flotilla"
FIRST_PROMPT = ("Use the flotilla:flotilla skill: take the census, announce yourself to the live peers, report what "
                "you inherited, then wait for a task.")


class LaunchError(RuntimeError):
    """A session cannot be launched as asked; the message says why."""


@dataclass(frozen=True)
class Seat:
    post: str
    name: str
    number: int
    tree: Path
    branch: str


def main_checkout(root: Path, run=subprocess.run) -> Path:
    done = run(["git", "-C", str(root), "rev-parse", "--path-format=absolute", "--git-common-dir"],
               capture_output=True, text=True, check=False)
    if done.returncode != 0 or not done.stdout.strip():
        raise LaunchError(f"{root}: git could not name the main checkout")
    return Path(done.stdout.strip()).parent.resolve()


def seat_for(main: Path, post, name: str) -> Seat:
    number = number_of(post, name)
    if number is None:
        raise LaunchError(f"`{name}` does not match post `{post.name}`'s pattern `{post.name_pattern}`")
    slug = f"{post.name}-{number}"
    return Seat(post.name, name, number, main.parent / f"{main.name}-{slug}", f"fleet/{slug}")


#: How much a mode lets a session do without a person; a post may narrow the profile's mode, never widen it (F6).
WIDTH = {"plan": 0, "default": 1, "manual": 1, "dontAsk": 1, "acceptEdits": 2, "auto": 3}


def permission_mode(profile: dict, post) -> str:
    answer = (profile.get("permissions") or {}).get("mode", "ask")
    if answer not in PERMISSION:
        raise LaunchError(f"permissions.mode is `{answer}`; it must be ask, rules or auto")
    chosen = PERMISSION[answer]
    if not post.permission_mode:
        return chosen
    if post.permission_mode not in WIDTH:
        raise LaunchError(f"post `{post.name}` asks for `{post.permission_mode}`, which flotilla never launches: a "
                          "post's file can be edited by a session, and that mode skips every permission check")
    if WIDTH[post.permission_mode] > WIDTH[chosen]:
        raise LaunchError(f"post `{post.name}` asks for `{post.permission_mode}`, wider than the profile's "
                          f"`{answer}` (`{chosen}`); a post may narrow the mode, never widen it")
    return post.permission_mode


#: `fleet.model` values that name no model: the session inherits the one Claude Code was launched with.
FLEET_MODEL_WORDS = ("one", "reviewer-strongest", "")


def model_for(profile: dict, post) -> str:
    """A post's own model wins; else a model the fleet names runs every post (F5); else inherit."""
    fleet_model = str((profile.get("fleet") or {}).get("model") or "")
    if fleet_model == "reviewer-strongest" and "accept" in post.may:
        return STRONGEST
    if post.model != "inherit":
        return post.model
    return "" if fleet_model in FLEET_MODEL_WORDS else fleet_model


def system_prompt(seat: Seat, post, *, main: Path) -> str:
    return (f"You are the session named \"{seat.name}\", holding the post `{post.name}` in a flotilla fleet on "
            f"this machine. Your home worktree is {seat.tree} (branch {seat.branch}). The main checkout {main} "
            "belongs to nobody: read it, never edit it. Your home tree is the one directory you may edit"
            + (": take each task there with `flotilla tree switch <branch>`. " if "claim" in post.may else
               ", on its own branch. ") +
            f"The flotilla command line is {CLI}; every ledger move goes through it. Your name and your post are "
            "given here: never infer them from the work.\n\n" + post.body.strip())


#: flotilla's command line, as the seats call it.
CLI = Path(__file__).resolve().parents[2] / "scripts" / "flotilla"
#: Every ledger move a seat may make without the person's say - all but `approve`, the person's alone.
OWN_WORK_MOVES = ("accept", "adopt", "assign", "broke", "claim", "close", "fix", "hand", "hold", "inbatch", "land",
                  "moved", "offledger", "queue", "reconcile", "recuse", "release", "reserve", "return", "ship", "show",
                  "take", "unbroke", "unhold", "urgent", "vouch", "wait", "walkable", "walked")
#: Commands a seat runs with any arguments; `fleet` is allowed bare only, since `fleet down` and `fleet clean` are
#: its arguments. Never here: spawn, retire, helper raise, onboard, guard, permit answer, events run.
OWN_COMMANDS = ("tree", "receipt", "lane", "status", "watch", "brief", "metrics", "doctor", "version", "events check",
                "events schema", "permit next", "permit list", "helper done")


def own_allow(seat: Seat, post, profile: dict) -> list[str]:
    """The allow rules a seat is launched with (twosuns field test of 0.6.7, W9 and W11). In auto mode Claude Code's
    classifier judged flotilla's own moves by the conversation around them - `work land` refused as "Merge Without
    Review" - and every sender's push of reviewed work waited on the person. A seat may run flotilla's own commands,
    and the post that lands may push trunk from its own tree. They pass the classifier, not flotilla's hooks: a hook's
    deny wins over an allow rule (measured on Claude Code 2.1.288), so the push guard still asks for a receipt and
    `work approve` stays the person's."""
    cli = str(CLI)
    rules = [rule for move in OWN_WORK_MOVES for rule in (f"Bash({cli} work {move})", f"Bash({cli} work {move} *)")]
    for command in OWN_COMMANDS:
        rules += [f"Bash({cli} {command})", f"Bash({cli} {command} *)"]
    rules.append(f"Bash({cli} fleet)")
    if "land" in post.may:
        trunk = str((profile.get("trunk") or {}).get("branch") or "main")
        rules += [f"Bash(git push origin HEAD:{trunk})", f"Bash(git -C {seat.tree} push origin HEAD:{trunk})"]
    return rules


def seat_settings(seat: Seat, post, profile: dict, plugin_json: str = "") -> str:
    """One `--settings` value: the plugins the post turns off, and the seat's own allow rules."""
    settings = json.loads(plugin_json) if plugin_json else {}
    settings["permissions"] = {"allow": own_allow(seat, post, profile)}
    return json.dumps(settings)


def argv(seat: Seat, post, profile: dict, *, main: Path, settings_json: str = "", first_prompt: str = "") -> list[str]:
    """`settings_json` (from `flotilla.fleet.plugins`) turns off the MCP plugins the post does not keep; the seat's
    own allow rules join it, and it rides as one argv element, never through a shell. `first_prompt` replaces the
    fleet's first prompt (a helper's task)."""
    command = ["claude", "--bg", "-n", seat.name, "--add-dir", str(seat.tree), "--permission-mode",
               permission_mode(profile, post)]
    model = model_for(profile, post)
    if model:
        command += ["--model", model]
    command += ["--settings", seat_settings(seat, post, profile, settings_json)]
    return command + ["--append-system-prompt", system_prompt(seat, post, main=main), first_prompt or FIRST_PROMPT]
