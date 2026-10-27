"""Where a project's code lies: the paths a person's yes is asked for, when they authorize code only (twosuns field
report B1; the person, 2026-10-27: asked in onboarding from the project and where its code lies, so the questions
put to the person stay few).

A code file is one by its suffix. Its top directory becomes `<dir>/**`, a code file at the root is named on its own;
tests, docs and examples change no product and ask no yes; data, assets and content under a code directory are
excluded with `!`, as credits and tables ship on the sender's word. Read from what git tracks, never from the disk.
"""

from __future__ import annotations

import subprocess
from pathlib import Path, PurePosixPath

CODE = frozenset({"py", "ts", "tsx", "js", "jsx", "mjs", "cjs", "mts", "cts", "rs", "go", "java", "kt", "kts", "swift",
                  "c", "h", "cc", "cpp", "hpp", "cs", "rb", "php", "lua", "scala", "dart", "vue", "svelte", "zig",
                  "glsl", "wgsl", "vert", "frag", "hlsl", "sh", "bash", "sql", "ex", "exs", "erl", "clj", "hs", "ml"})
#: Directories whose changes do not change the product: no yes is asked for them.
NOT_PRODUCT = frozenset({"tests", "test", "__tests__", "spec", "specs", "e2e", "docs", "doc", "examples", "example",
                         "fixtures", "benchmarks", "bench"})
#: What runs whatever its suffix - CI, hooks, the tools' settings, manifests and build files - asked of the person
#: wherever it lies (security review of the code-paths change, 4). Named when the project tracks it.
RUNS_DIRS = (".github", ".gitlab", ".claude", ".husky", ".circleci", ".devcontainer")
RUNS_NAMES = ("package.json", "package-lock.json", "pnpm-lock.yaml", "yarn.lock", "pyproject.toml", "uv.lock",
              "setup.py", "setup.cfg", "Cargo.toml", "Cargo.lock", "go.mod", "go.sum", "Gemfile", "Gemfile.lock",
              "Makefile", "Dockerfile", "docker-compose.yml", "docker-compose.yaml", ".gitlab-ci.yml", ".gitattributes",
              ".gitmodules", ".pre-commit-config.yaml", ".envrc", ".npmrc")
#: Directories under code that hold data rather than code: excluded with `!`.
DATA = frozenset({"data", "assets", "content", "locales", "locale", "i18n", "public", "static", "media", "fonts"})


def _tracked(root: Path, run) -> list[str]:
    done = run(["git", "-C", str(root), "ls-files", "-z"], capture_output=True, check=False)
    if done.returncode != 0:
        return []
    return [name.decode("utf-8", "surrogateescape") for name in done.stdout.split(b"\0") if name]


def code_paths(root: Path, run=subprocess.run) -> list[str]:
    """The approve_paths patterns for this project's code, includes first, each followed by its excludes."""
    tops: set[str] = set()
    singles: set[str] = set()
    data: dict[str, set[str]] = {}
    runs: set[str] = set()
    for name in _tracked(root, run):
        path = PurePosixPath(name)
        dirs = path.parts[:-1]
        if dirs and dirs[0] in RUNS_DIRS:
            runs.add(f"{dirs[0]}/**")
            continue
        if path.name in RUNS_NAMES or path.name.startswith("requirements") and path.suffix == ".txt":
            runs.add(path.name)   # by name, anywhere
            continue
        if any(part.startswith(".") for part in path.parts):
            continue   # the fleet's and the tools' own (.github, .flotilla, .claude) are not the product
        if any(part.lower() in NOT_PRODUCT for part in dirs):
            continue
        held = next((at for at, part in enumerate(dirs) if part.lower() in DATA), None)
        if held is not None:
            if held > 0:
                data.setdefault(dirs[0], set()).add("/".join(dirs[:held + 1]))
            continue
        if path.suffix.lower().lstrip(".") not in CODE:
            continue
        if dirs:
            tops.add(dirs[0])
        else:
            singles.add(name)
    found = sorted(runs) + [f"/{name}" for name in sorted(singles)]
    for top in sorted(tops):
        found.append(f"{top}/**")
        found += [f"!{inner}/**" for inner in sorted(data.get(top, ()))]
    return found
