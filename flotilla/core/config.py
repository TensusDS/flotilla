"""Project configuration and the activation rule.

A project is active when `.flotilla/project.toml` exists at or above the working directory.
Finding it never parses it: the hook path that decides "not onboarded, stay silent" must cost
one stat per directory level and nothing more.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

SUPPORTED_SCHEMA = 1
PROJECT_DIR = ".flotilla"
PROJECT_FILE = "project.toml"


class ConfigError(RuntimeError):
    """The project configuration cannot be used; the message names the file."""


@dataclass(frozen=True)
class Project:
    root: Path
    path: Path
    schema: int
    data: dict


def find_project(start: Path) -> Path | None:
    current = Path(start).resolve()
    for directory in (current, *current.parents):
        if (directory / PROJECT_DIR / PROJECT_FILE).is_file():
            return directory
    return None


def load_project(root: Path) -> Project:
    path = Path(root) / PROJECT_DIR / PROJECT_FILE
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as err:
        raise ConfigError(f"{path}: {err}") from err
    except OSError as err:
        raise ConfigError(f"{path}: cannot be read: {err}") from err
    schema = data.get("schema")
    if schema is None:
        raise ConfigError(f"{path}: missing `schema`; expected `schema = {SUPPORTED_SCHEMA}`")
    if isinstance(schema, bool) or not isinstance(schema, int) or schema < 1:
        raise ConfigError(f"{path}: `schema` must be a positive integer, found {schema!r}")
    if schema > SUPPORTED_SCHEMA:
        raise ConfigError(f"{path}: schema {schema} is newer than this flotilla understands "
                          f"({SUPPORTED_SCHEMA}); update the plugin")
    return Project(root=Path(root), path=path, schema=schema, data=data)
