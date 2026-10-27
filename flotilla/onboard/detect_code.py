"""The paths a person's yes is asked for, when they authorize code only (twosuns field report B1; the person,
2026-10-27: asked in onboarding from the project and where its code lies, so the questions put to the person stay
few).

Everything waits for the person but what is plainly not code: docs, tests and examples, data and assets under the
code, and files whose type is text or media. So a directory or a file the project adds later is the person's until
said otherwise - a snapshot of today's code would let tomorrow's through (security review of the change, 5). What
runs whatever its type - CI, hooks, manifests, the fleet's rules - is the person's in every case (`approval.ALWAYS`).
Read from what git tracks, never from the disk; a name that would read as a pattern is never written as one.
"""

from __future__ import annotations

import subprocess
from pathlib import Path, PurePosixPath

#: Directories whose changes do not change the product.
NOT_PRODUCT = frozenset({"tests", "test", "__tests__", "spec", "specs", "e2e", "docs", "doc", "examples", "example",
                         "fixtures", "benchmarks", "bench"})
#: Directories that hold data rather than code. Not `public`, `static` or `assets`: code is served from them (a service
#: worker, a bundle), so there the type decides, file by file (final review of 0.21.0, I1).
DATA = frozenset({"data", "content", "locales", "locale", "i18n"})
#: Types that are text or media, never run.
NOT_CODE = ("md", "markdown", "rst", "txt", "adoc", "png", "jpg", "jpeg", "gif", "webp", "svg", "ico", "bmp", "tif",
            "tiff", "mp3", "wav", "ogg", "flac", "mp4", "webm", "mov", "pdf", "csv", "tsv", "woff", "woff2", "ttf",
            "otf")
#: A character that would make a name read as a pattern (approval's matcher, or a leading `!`).
_PATTERN = set("*?[]\\!")


def _tracked(root: Path, run) -> list[str] | None:
    done = run(["git", "-C", str(root), "ls-files", "-z"], capture_output=True, check=False)
    if done.returncode != 0:
        return None
    return [name.decode("utf-8", "surrogateescape") for name in done.stdout.split(b"\0") if name]


def _plain(text: str) -> bool:
    return not (_PATTERN & set(text))


def code_paths(root: Path, run=subprocess.run) -> list[str]:
    """approve_paths for this project: `**`, less what is plainly not code - or [] when it holds no code at all."""
    names = _tracked(root, run)
    if not names:
        return []
    dirs: set[str] = set()
    types: set[str] = set()
    code = False
    for name in names:
        path = PurePosixPath(name)
        parts = path.parts[:-1]
        cut = next((at for at, part in enumerate(parts) if part.lower() in NOT_PRODUCT | DATA), None)
        if cut is not None:
            where = "/".join(parts[:cut + 1])
            if _plain(where):   # a name that reads as a pattern is never excluded: it stays the person's
                dirs.add(where)
            continue
        suffix = path.suffix.lower().lstrip(".")
        if suffix in NOT_CODE:
            types.add(suffix)
        else:
            code = True
    if not code:
        return []
    return ["**", *(f"!{where}/**" for where in sorted(dirs)), *(f"!*.{suffix}" for suffix in sorted(types))]
