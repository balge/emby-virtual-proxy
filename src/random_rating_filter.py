from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional


def _normalize_rating(value: Any) -> str:
    if value is None:
        return ""
    s = str(value).strip().upper()
    if not s:
        return ""
    return "".join(ch for ch in s if ch.isalnum())


def filter_items_by_official_rating_threshold(
    items: List[Dict[str, Any]],
    hide_from_rating: Optional[str],
    ordered_ratings: Iterable[str],
) -> List[Dict[str, Any]]:
    """
    Hide items whose OfficialRating is at or above `hide_from_rating`.

    `ordered_ratings` should be in ascending maturity order from Emby /OfficialRatings.
    Items with unknown or missing rating are kept (fail-open).
    """
    threshold_key = _normalize_rating(hide_from_rating)
    if not threshold_key:
        return list(items)

    order_map: Dict[str, int] = {}
    for idx, raw in enumerate(ordered_ratings):
        key = _normalize_rating(raw)
        if key and key not in order_map:
            order_map[key] = idx

    threshold_idx = order_map.get(threshold_key)
    if threshold_idx is None:
        return list(items)

    out: List[Dict[str, Any]] = []
    for item in items:
        raw_rating = item.get("OfficialRating")
        rating_key = _normalize_rating(raw_rating)
        if not rating_key:
            out.append(item)
            continue

        item_idx = order_map.get(rating_key)
        if item_idx is None or item_idx < threshold_idx:
            out.append(item)

    return out
