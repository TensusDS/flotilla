"""Why a ledger move did not happen."""


class MoveRefused(RuntimeError):
    """A move that does not happen; the message names what is missing and what is legal."""


class ActorUnknown(MoveRefused):
    """The caller could not be identified; nothing is recorded under a guessed name."""


class ActorMismatch(ActorUnknown):
    """The census names the caller, and it is not the session named with --as: a refusal, never a fallback."""


class NotYet(MoveRefused):
    """The move cannot be proved yet (a PR still open, CI still running, origin not updated); ask again later."""


class OrderWait(NotYet):
    """A wait the fleet's rules intend - work waiting on other work - not a fault anywhere: the session is told so,
    so it does not report it as a flotilla defect (the person, 2026-10-27, on twosuns field report A5)."""
