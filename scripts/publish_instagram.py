#!/usr/bin/env python3
"""Validate an approved post and publish it through Meta's Instagram API."""

from __future__ import annotations

import ipaddress
import json
import os
import re
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any


MAX_CAPTION_LENGTH = 2_200
MAX_IMAGE_BYTES = 8 * 1024 * 1024
ALLOWED_ASPECT_MIN = 0.8
ALLOWED_ASPECT_MAX = 1.91
APPROVAL_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{7,79}$")
API_VERSION_RE = re.compile(r"^v\d+\.\d+$")
JPEG_SOF_MARKERS = {
    0xC0,
    0xC1,
    0xC2,
    0xC3,
    0xC5,
    0xC6,
    0xC7,
    0xC9,
    0xCA,
    0xCB,
    0xCD,
    0xCE,
    0xCF,
}


class PublisherError(RuntimeError):
    """A safe, user-readable validation or publishing error."""


@dataclass(frozen=True)
class ApprovedPost:
    approval_id: str
    caption: str
    credit_line: str
    image_url: str
    source_url: str
    dry_run: bool


def _require_https_url(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PublisherError(f"{field} is required")
    value = value.strip()
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme != "https" or not parsed.hostname:
        raise PublisherError(f"{field} must be a public HTTPS URL")
    if parsed.username or parsed.password:
        raise PublisherError(f"{field} must not contain credentials")
    if parsed.port not in (None, 443):
        raise PublisherError(f"{field} must use the standard HTTPS port")
    return value


def _assert_public_host(url: str) -> None:
    hostname = urllib.parse.urlsplit(url).hostname
    if not hostname:
        raise PublisherError("URL hostname is missing")
    try:
        addresses = {item[4][0] for item in socket.getaddrinfo(hostname, 443)}
    except socket.gaierror as exc:
        raise PublisherError(f"Image host could not be resolved: {hostname}") from exc
    for address in addresses:
        ip = ipaddress.ip_address(address)
        if not ip.is_global:
            raise PublisherError("Image URL must resolve only to public internet addresses")


def load_approved_post(raw: str) -> ApprovedPost:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise PublisherError("Approval issue body must contain valid JSON") from exc
    if not isinstance(payload, dict) or payload.get("schema") != 1:
        raise PublisherError("Approval payload must use schema 1")

    approval_id = payload.get("approval_id")
    if not isinstance(approval_id, str) or not APPROVAL_ID_RE.fullmatch(approval_id):
        raise PublisherError("approval_id must be 8-80 safe identifier characters")

    caption = payload.get("caption")
    if not isinstance(caption, str) or not caption.strip():
        raise PublisherError("caption is required")
    caption = caption.strip()

    credit_line = payload.get("credit_line")
    if not isinstance(credit_line, str) or not credit_line.strip():
        raise PublisherError("credit_line is required")
    credit_line = credit_line.strip()
    if len(credit_line) > 300:
        raise PublisherError("credit_line is too long")
    if credit_line.casefold() not in caption.casefold():
        caption = f"{caption}\n\n{credit_line}"
    if len(caption) > MAX_CAPTION_LENGTH:
        raise PublisherError(f"Final caption exceeds {MAX_CAPTION_LENGTH} characters")

    dry_run = payload.get("dry_run", True)
    if not isinstance(dry_run, bool):
        raise PublisherError("dry_run must be true or false")

    return ApprovedPost(
        approval_id=approval_id,
        caption=caption,
        credit_line=credit_line,
        image_url=_require_https_url(payload.get("image_url"), "image_url"),
        source_url=_require_https_url(payload.get("source_url"), "source_url"),
        dry_run=dry_run,
    )


def jpeg_dimensions(data: bytes) -> tuple[int, int]:
    if not data.startswith(b"\xff\xd8"):
        raise PublisherError("Instagram publishing requires a JPEG image")
    index = 2
    while index + 9 < len(data):
        if data[index] != 0xFF:
            index += 1
            continue
        while index < len(data) and data[index] == 0xFF:
            index += 1
        if index >= len(data):
            break
        marker = data[index]
        index += 1
        if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
            continue
        if index + 2 > len(data):
            break
        segment_length = int.from_bytes(data[index : index + 2], "big")
        if segment_length < 2 or index + segment_length > len(data):
            break
        if marker in JPEG_SOF_MARKERS:
            if segment_length < 7:
                break
            height = int.from_bytes(data[index + 3 : index + 5], "big")
            width = int.from_bytes(data[index + 5 : index + 7], "big")
            if width < 1 or height < 1:
                break
            return width, height
        index += segment_length
    raise PublisherError("Could not read JPEG dimensions")


def validate_remote_image(url: str) -> tuple[int, int, int]:
    _assert_public_host(url)
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "PositiveRateInstagramPublisher/1.0"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            final_url = response.geturl()
            _require_https_url(final_url, "redirected image_url")
            _assert_public_host(final_url)
            content_type = response.headers.get_content_type()
            data = response.read(MAX_IMAGE_BYTES + 1)
    except (urllib.error.URLError, TimeoutError) as exc:
        raise PublisherError(f"Image URL could not be downloaded: {exc}") from exc
    if content_type not in {"image/jpeg", "image/jpg", "application/octet-stream"}:
        raise PublisherError(f"Image URL returned unsupported content type: {content_type}")
    if len(data) > MAX_IMAGE_BYTES:
        raise PublisherError("Image exceeds the 8 MB safety limit")
    width, height = jpeg_dimensions(data)
    ratio = width / height
    if ratio < ALLOWED_ASPECT_MIN or ratio > ALLOWED_ASPECT_MAX:
        raise PublisherError("Image aspect ratio must be between 4:5 and 1.91:1")
    if width < 320 or height < 320:
        raise PublisherError("Image dimensions must be at least 320×320")
    return width, height, len(data)


def _decode_meta_error(exc: urllib.error.HTTPError) -> str:
    try:
        body = json.loads(exc.read().decode("utf-8", errors="replace"))
        error = body.get("error", {})
        message = error.get("message", "Meta API request failed")
        code = error.get("code")
        subcode = error.get("error_subcode")
        suffix = ", ".join(
            part for part in (f"code {code}" if code else "", f"subcode {subcode}" if subcode else "") if part
        )
        return f"{message}{f' ({suffix})' if suffix else ''}"
    except Exception:
        return f"Meta API returned HTTP {exc.code}"


def meta_request(url: str, token: str, data: dict[str, str] | None = None) -> dict[str, Any]:
    values = dict(data or {})
    values["access_token"] = token
    encoded = urllib.parse.urlencode(values).encode("utf-8") if data is not None else None
    if data is None:
        separator = "&" if "?" in url else "?"
        url = f"{url}{separator}{urllib.parse.urlencode({'access_token': token})}"
    request = urllib.request.Request(url, data=encoded, method="POST" if data is not None else "GET")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise PublisherError(_decode_meta_error(exc)) from exc
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise PublisherError(f"Meta API request failed: {exc}") from exc


def meta_configuration() -> tuple[str, str, str, str]:
    user_id = os.environ.get("INSTAGRAM_USER_ID", "").strip()
    token = os.environ.get("INSTAGRAM_ACCESS_TOKEN", "").strip()
    version = os.environ.get("META_API_VERSION", "").strip()
    if not user_id or not token:
        raise PublisherError("Instagram repository credentials are not configured")
    if not API_VERSION_RE.fullmatch(version):
        raise PublisherError("META_API_VERSION must look like vXX.X")
    return user_id, token, version, f"https://graph.instagram.com/{version}"


def validate_meta_credentials() -> str:
    user_id, token, _version, base = meta_configuration()
    details = meta_request(f"{base}/me?fields=user_id,username,account_type", token)
    returned_user_id = str(details.get("user_id", ""))
    if not returned_user_id:
        raise PublisherError("Meta token validation did not return an Instagram publishing user ID")
    if returned_user_id != user_id:
        raise PublisherError("Configured Instagram user ID does not match the access token")
    username = details.get("username")
    if not isinstance(username, str) or not username:
        raise PublisherError("Meta token validation did not return an Instagram username")
    return username


def publish(post: ApprovedPost) -> tuple[str, str | None]:
    user_id, token, _version, base = meta_configuration()
    container = meta_request(
        f"{base}/{urllib.parse.quote(user_id, safe='')}/media",
        token,
        {"image_url": post.image_url, "caption": post.caption},
    )
    container_id = container.get("id")
    if not isinstance(container_id, str):
        raise PublisherError("Meta did not return a media container ID")

    for _ in range(12):
        status = meta_request(
            f"{base}/{urllib.parse.quote(container_id, safe='')}?fields=status_code,status",
            token,
        )
        status_code = status.get("status_code")
        if status_code == "FINISHED":
            break
        if status_code in {"ERROR", "EXPIRED"}:
            raise PublisherError(f"Media container failed with status {status_code}: {status.get('status', '')}")
        time.sleep(5)
    else:
        raise PublisherError("Media container was not ready within one minute")

    result = meta_request(
        f"{base}/{urllib.parse.quote(user_id, safe='')}/media_publish",
        token,
        {"creation_id": container_id},
    )
    media_id = result.get("id")
    if not isinstance(media_id, str):
        raise PublisherError("Meta did not return a published media ID")

    permalink: str | None = None
    try:
        details = meta_request(
            f"{base}/{urllib.parse.quote(media_id, safe='')}?fields=permalink",
            token,
        )
        value = details.get("permalink")
        if isinstance(value, str):
            permalink = value
    except PublisherError:
        pass
    return media_id, permalink


def write_output(name: str, value: str) -> None:
    output_path = os.environ.get("GITHUB_OUTPUT")
    if output_path:
        with open(output_path, "a", encoding="utf-8") as handle:
            handle.write(f"{name}={value}\n")


def main() -> int:
    try:
        post = load_approved_post(os.environ.get("APPROVAL_PAYLOAD", ""))
        width, height, byte_count = validate_remote_image(post.image_url)
        print(
            f"Validated approval {post.approval_id}: JPEG {width}×{height}, "
            f"{byte_count:,} bytes, caption {len(post.caption)} characters."
        )
        write_output("approval_id", post.approval_id)
        if post.dry_run:
            username = validate_meta_credentials()
            print(f"Meta credentials verified for @{username}.")
            print("Dry run complete. Nothing was published to Instagram.")
            write_output("published", "false")
            return 0
        media_id, permalink = publish(post)
        print(f"Published Instagram media ID {media_id}.")
        write_output("published", "true")
        write_output("media_id", media_id)
        if permalink:
            write_output("permalink", permalink)
        return 0
    except PublisherError as exc:
        print(f"Publisher error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
