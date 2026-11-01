"""What counts as media: the only thing the archive lets expire (gc design 7). Video, audio, images and renders, by
content type and by a few suffixes the type table does not know."""

import mimetypes

MEDIA = (".exr", ".hdr", ".blend", ".psd")


def is_media(path: str) -> bool:
    kind = mimetypes.guess_type(path)[0] or ""
    return kind.split("/")[0] in ("video", "audio", "image") or path.lower().endswith(MEDIA)
