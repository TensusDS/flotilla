"""The project profile's `[rig]` section (rig design, section 7): the image, the GPUs and the disk a project's heavy
runs need. A value that does not look like one falls back to its default, and the reason is kept to be shown. The
image is only a wish here: `rig up` runs it only when the person allowed it (`machine.toml` `rig_images`)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from flotilla.rig.settings import DEFAULT_IMAGE, IMAGE

DEFAULT_DISK = 30
_GPU = re.compile(r"[A-Za-z0-9 _.-]{1,40}")


@dataclass(frozen=True)
class RigProfile:
    image: str
    gpus: tuple
    disk_gb: int
    problems: tuple = field(default=(), compare=False)


def read(data: dict) -> RigProfile:
    section = data.get("rig") if isinstance(data, dict) else None
    section = section if isinstance(section, dict) else {}
    problems = []
    image = section.get("image", DEFAULT_IMAGE)
    if not isinstance(image, str) or not IMAGE.fullmatch(image):
        problems.append(f"[rig] image {image!r} is not an image name; using {DEFAULT_IMAGE}")
        image = DEFAULT_IMAGE
    gpus = section.get("gpu", [])
    gpus = [gpus] if isinstance(gpus, str) else gpus if isinstance(gpus, list) else []
    kept = tuple(name for name in gpus if isinstance(name, str) and _GPU.fullmatch(name))
    if len(kept) != len(gpus):
        problems.append("[rig] gpu holds names that are not GPU names; they are left out")
    disk = section.get("disk_gb", DEFAULT_DISK)
    if isinstance(disk, bool) or not isinstance(disk, int) or not 10 <= disk <= 500:
        problems.append(f"[rig] disk_gb {disk!r} is not 10-500; using {DEFAULT_DISK}")
        disk = DEFAULT_DISK
    return RigProfile(image, kept, disk, tuple(problems))
