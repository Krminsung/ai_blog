"""Deterministic safe rendering for official CMS payloads and manual Naver packages."""

from __future__ import annotations

from dataclasses import dataclass
from html import escape
import re
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from blogops.domain.publishing.references import ReadyMedia
from blogops.domain.publishing.rules import canonical_hash


@dataclass(frozen=True, slots=True)
class RenderedContent:
    html: str
    blocks: list[dict[str, Any]]
    unsupported: list[dict[str, Any]]
    render_hash: str


def render_for_cms(
    document: list[dict[str, Any]],
    media_urls: dict[str, str] | None = None,
    tracking: dict[str, str] | None = None,
    attributions: list[str] | None = None,
) -> RenderedContent:
    urls = media_urls or {}
    rendered: list[str] = []
    blocks: list[dict[str, Any]] = []
    unsupported: list[dict[str, Any]] = []
    for index, raw in enumerate(document):
        block = raw if isinstance(raw, dict) else {"type": "unknown", "value": raw}
        block_type = str(block.get("type") or block.get("block_type") or "paragraph").lower()
        payload = block.get("payload") if isinstance(block.get("payload"), dict) else block
        text = str(
            payload.get("text")
            or payload.get("content")
            or block.get("plain_text")
            or ""
        )
        block_key = str(block.get("id") or block.get("block_key") or f"block-{index + 1}")
        html: str
        copy_text = text
        if block_type in {"title"}:
            html = f"<h1>{escape(text)}</h1>"
        elif block_type in {"paragraph", "text"}:
            html = f"<p>{escape(text)}</p>"
        elif block_type in {"heading", "h2", "h3"}:
            level = int(payload.get("level", 2)) if str(payload.get("level", 2)).isdigit() else 2
            level = min(4, max(2, level))
            html = f"<h{level}>{escape(text)}</h{level}>"
        elif block_type in {"quote", "blockquote"}:
            html = f"<blockquote>{escape(text)}</blockquote>"
        elif block_type in {"list", "bullets"}:
            items = payload.get("items") if isinstance(payload.get("items"), list) else []
            html = "<ul>" + "".join(f"<li>{escape(str(item))}</li>" for item in items) + "</ul>"
            copy_text = "\n".join(str(item) for item in items)
        elif block_type == "table":
            rows = payload.get("rows") if isinstance(payload.get("rows"), list) else []
            normalized_rows = [
                row
                if isinstance(row, list)
                else list(row.values())
                if isinstance(row, dict)
                else [row]
                for row in rows
            ]
            html = "<table><tbody>" + "".join(
                "<tr>"
                + "".join(
                    f"<td>{escape(str(cell))}</td>"
                    for cell in row
                )
                + "</tr>"
                for row in normalized_rows
            ) + "</tbody></table>"
            copy_text = "\n".join(
                "\t".join(str(cell) for cell in row) for row in normalized_rows
            )
        elif block_type == "code":
            html = f"<pre><code>{escape(text)}</code></pre>"
        elif block_type == "faq":
            items = payload.get("items") if isinstance(payload.get("items"), list) else []
            html = "<dl>" + "".join(
                f"<dt>{escape(str(item.get('question', '')))}</dt>"
                f"<dd>{escape(str(item.get('answer', '')))}</dd>"
                for item in items
                if isinstance(item, dict)
            ) + "</dl>"
            copy_text = "\n\n".join(
                f"Q. {item.get('question', '')}\nA. {item.get('answer', '')}"
                for item in items
                if isinstance(item, dict)
            )
        elif block_type in {"cta", "link"}:
            raw_url = str(payload.get("url") or payload.get("href") or "")
            href = _tracked_http_url(raw_url, tracking or {})
            if href is None:
                html = f"<p>{escape(text)}</p>"
                unsupported.append(
                    {"block_key": block_key, "type": block_type, "replacement": "paragraph"}
                )
            else:
                html = (
                    f'<p><a href="{escape(href, quote=True)}" '
                    'rel="noopener noreferrer">'
                    f"{escape(text or raw_url)}</a></p>"
                )
                copy_text = text or raw_url
        elif block_type in {"image", "media"}:
            placement = str(payload.get("placement_key") or block_key)
            url = urls.get(placement)
            copy_text = str(payload.get("caption") or payload.get("alt") or "")
            if url:
                html = f'<figure><img src="{escape(url, quote=True)}" alt="{escape(str(payload.get("alt", "")), quote=True)}"></figure>'
            else:
                html = f"<!-- media:{escape(placement)} -->"
        else:
            html = f"<p>{escape(text)}</p>"
            unsupported.append(
                {"block_key": block_key, "type": block_type, "replacement": "paragraph"}
            )
        rendered.append(html)
        blocks.append(
            {
                "block_key": block_key,
                "type": block_type,
                "html": html,
                "copy_text": copy_text,
                "original_url": (
                    str(payload.get("url") or payload.get("href"))
                    if block_type in {"cta", "link"}
                    and (payload.get("url") or payload.get("href"))
                    else None
                ),
                "order": index + 1,
            }
        )
    attribution_values = list(
        dict.fromkeys(
            item.strip()
            for item in (attributions or [])
            if isinstance(item, str) and item.strip()
        )
    )
    if attribution_values:
        attribution_html = (
            '<section data-blogops="media-attributions"><h2>Image credits</h2><ul>'
            + "".join(f"<li>{escape(item)}</li>" for item in attribution_values)
            + "</ul></section>"
        )
        rendered.append(attribution_html)
        blocks.append(
            {
                "block_key": "media-attributions",
                "type": "attribution",
                "html": attribution_html,
                "copy_text": "\n".join(attribution_values),
                "original_url": None,
                "order": len(blocks) + 1,
            }
        )
    joined = "\n".join(rendered)
    return RenderedContent(joined, blocks, unsupported, canonical_hash(blocks))


def _tracked_http_url(value: str, tracking: dict[str, str]) -> str | None:
    try:
        parsed = urlsplit(value.strip())
        if (
            parsed.scheme.lower() not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
        ):
            return None
        query = list(parse_qsl(parsed.query, keep_blank_values=True))
    except ValueError:
        return None
    existing = {key for key, _value in query}
    query.extend(
        (key, item)
        for key, item in sorted(tracking.items())
        if key not in existing
    )
    return urlunsplit(
        (parsed.scheme.lower(), parsed.netloc, parsed.path, urlencode(query), parsed.fragment)
    )


def naver_image_manifest(media: tuple[ReadyMedia, ...]) -> list[dict[str, Any]]:
    return [
        {
            "order": index,
            "filename": f"{index:03d}-{_safe_filename(item.filename)}",
            "placement_key": item.placement_key,
            "recommended_after_block": item.placement_key,
            "object_ref": item.object_ref,
            "content_hash": item.content_hash,
            "mime_type": item.mime_type,
            "alt_text": item.alt_text,
            "caption": item.caption,
            "attribution_text": item.attribution_text,
            "rights_snapshot_hash": item.rights_snapshot_hash,
        }
        for index, item in enumerate(media, start=1)
    ]


def _safe_filename(value: str) -> str:
    leaf = value.replace("\\", "/").rsplit("/", 1)[-1]
    normalized = re.sub(r"[^A-Za-z0-9._-]+", "-", leaf).strip(".-")[:180]
    return normalized or "image"


def package_diff(
    previous_blocks: list[dict[str, Any]] | None,
    current_blocks: list[dict[str, Any]],
    previous_images: list[dict[str, Any]] | None,
    current_images: list[dict[str, Any]],
) -> dict[str, Any]:
    old_blocks = {str(item["block_key"]): canonical_hash(item) for item in (previous_blocks or [])}
    new_blocks = {str(item["block_key"]): canonical_hash(item) for item in current_blocks}
    old_images = {str(item["placement_key"]): str(item["content_hash"]) for item in (previous_images or [])}
    new_images = {str(item["placement_key"]): str(item["content_hash"]) for item in current_images}
    return {
        "added_blocks": sorted(set(new_blocks) - set(old_blocks)),
        "removed_blocks": sorted(set(old_blocks) - set(new_blocks)),
        "changed_blocks": sorted(
            key for key in set(old_blocks) & set(new_blocks) if old_blocks[key] != new_blocks[key]
        ),
        "added_images": sorted(set(new_images) - set(old_images)),
        "removed_images": sorted(set(old_images) - set(new_images)),
        "changed_images": sorted(
            key for key in set(old_images) & set(new_images) if old_images[key] != new_images[key]
        ),
    }
