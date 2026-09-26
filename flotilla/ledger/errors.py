"""Why a ledger move did not happen."""


class MoveRefused(RuntimeError):
    """A move that does not happen; the message names what is missing and what is legal."""


class ActorUnknown(MoveRefused):
    """The caller could not be identified; nothing is recorded under a guessed name."""


class NotYet(MoveRefused):
    """The move cannot be proved yet (a PR still open, CI still running, origin not updated); ask again later."""
