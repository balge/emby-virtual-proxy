import re
from pathlib import Path
from typing import Optional


_NON_SAFE_USER_ID = re.compile(r"[^a-zA-Z0-9._-]+")
_USER_SEGMENT = "__user__"
_AUTH_USER_ID_RE = re.compile(r'UserId="([^"]+)"')


def extract_user_id_from_request(request, full_path: Optional[str] = None) -> Optional[str]:
    if request is not None:
        try:
            user_id = str(request.query_params.get("UserId") or "").strip()
            if user_id:
                return user_id
        except Exception:
            pass
        try:
            user_id = str(request.headers.get("X-Emby-User-Id") or "").strip()
            if user_id:
                return user_id
            auth = str(request.headers.get("X-Emby-Authorization") or "").strip()
            if auth:
                match = _AUTH_USER_ID_RE.search(auth)
                if match:
                    user_id = str(match.group(1) or "").strip()
                    if user_id:
                        return user_id
        except Exception:
            pass
    path = str(full_path or "")
    parts = [p for p in path.split("/") if p]
    if "Users" in parts:
        idx = parts.index("Users") + 1
        if idx < len(parts):
            user_id = str(parts[idx] or "").strip()
            if user_id:
                return user_id
    return None


def sanitize_cover_user_id(user_id: str) -> str:
    cleaned = _NON_SAFE_USER_ID.sub("_", str(user_id or "").strip())
    return cleaned or "unknown"


def cover_file_stem(library_id: str, resource_type: str, user_id: Optional[str] = None) -> str:
    lid = str(library_id)
    if resource_type == "random" and user_id:
        return f"{lid}{_USER_SEGMENT}{sanitize_cover_user_id(user_id)}"
    return lid


def cover_path_for(covers_dir: Path, library_id: str, resource_type: str, user_id: Optional[str], ext: str) -> Path:
    return covers_dir / f"{cover_file_stem(library_id, resource_type, user_id)}.{ext}"


def resolve_cover_tag(vlib, user_id: Optional[str] = None, fallback_to_default: bool = True) -> Optional[str]:
    if getattr(vlib, "resource_type", None) == "random" and user_id:
        tags = getattr(vlib, "cover_image_tags", None) or {}
        tag = tags.get(str(user_id))
        if tag:
            return tag
        if not fallback_to_default:
            return None
    return getattr(vlib, "image_tag", None)
