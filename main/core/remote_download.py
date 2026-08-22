"""Security-hardened server-side media downloads for URL inputs."""
from __future__ import annotations

import ipaddress
import logging
import mimetypes
import os
import re
import shutil
import socket
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.parse import parse_qs, quote, urlencode, unquote, urljoin, urlsplit, urlunsplit

import httpx

from core.activity import ActivityBusyError, ActivityCoordinator
from core.filenames import sanitize_filename


log = logging.getLogger(__name__)
MAX_REDIRECTS = 8
CHUNK_SIZE = 1024 * 1024
ALLOWED_PORTS = {80, 443}
SAFE_CONTENT_TYPES = (
    "application/octet-stream",
    "application/ogg",
    "application/vnd.apple.mpegurl",
    "application/x-mpegurl",
    "application/zip",
)
CONTENT_TYPE_EXTENSIONS = {
    "audio/flac": ".flac",
    "audio/mp4": ".m4a",
    "audio/mpeg": ".mp3",
    "audio/ogg": ".ogg",
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "video/mp4": ".mp4",
    "video/quicktime": ".mov",
    "video/webm": ".webm",
    "video/x-matroska": ".mkv",
    "video/x-msvideo": ".avi",
}


class RemoteDownloadError(RuntimeError):
    pass


class RemoteDownloadCancelled(RemoteDownloadError):
    pass


@dataclass(frozen=True)
class DownloadResult:
    path: Path
    source_url: str
    size: int


def _rewrite_cloud_share(url: str) -> str:
    """Turn common public share pages into their provider download endpoints."""
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower().rstrip(".")

    if host == "drive.google.com":
        match = re.fullmatch(r"/file/d/([^/]+)(?:/.*)?", parsed.path)
        if match:
            file_id = quote(match.group(1), safe="")
            return f"https://drive.usercontent.google.com/download?id={file_id}&export=download&confirm=t"
        query = parse_qs(parsed.query)
        if parsed.path == "/open" and query.get("id"):
            file_id = quote(query["id"][0], safe="")
            return f"https://drive.usercontent.google.com/download?id={file_id}&export=download&confirm=t"

    if host == "1drv.ms" or host.endswith(".sharepoint.com"):
        query = parse_qs(parsed.query, keep_blank_values=True)
        query["download"] = ["1"]
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path,
                           urlencode(query, doseq=True), ""))

    return url


def _validated_url(raw_url: str) -> tuple[str, str, int]:
    value = (raw_url or "").strip()
    if not value:
        raise RemoteDownloadError("Paste a public HTTP or HTTPS media URL first.")
    if len(value) > 8192:
        raise RemoteDownloadError("The URL is too long.")
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"}:
        raise RemoteDownloadError("Only HTTP and HTTPS URLs are supported.")
    if parsed.username or parsed.password:
        raise RemoteDownloadError("URLs containing embedded credentials are not allowed.")
    host = (parsed.hostname or "").strip().lower().rstrip(".")
    if not host:
        raise RemoteDownloadError("The URL must contain a valid hostname.")
    try:
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError as exc:
        raise RemoteDownloadError("The URL contains an invalid port.") from exc
    if port not in ALLOWED_PORTS:
        raise RemoteDownloadError("Only standard HTTP and HTTPS ports are allowed.")

    try:
        addresses = {
            item[4][0]
            for item in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        }
    except socket.gaierror as exc:
        raise RemoteDownloadError("The URL hostname could not be resolved.") from exc
    if not addresses:
        raise RemoteDownloadError("The URL hostname did not resolve to an address.")
    for address in addresses:
        try:
            ip = ipaddress.ip_address(address.split("%", 1)[0])
        except ValueError as exc:
            raise RemoteDownloadError("The URL resolved to an invalid address.") from exc
        if not ip.is_global:
            raise RemoteDownloadError(
                "Private, local, reserved, and non-public network addresses are blocked."
            )
    return value, host, port


def _verify_connected_peer(response: httpx.Response) -> None:
    """Reject a DNS-rebinding connection if the actual peer is non-public."""
    stream = response.extensions.get("network_stream")
    if stream is None:
        return
    try:
        peer = stream.get_extra_info("server_addr")
        if peer is None:
            sock = stream.get_extra_info("socket")
            peer = sock.getpeername() if sock is not None else None
        address = peer[0] if isinstance(peer, tuple) else str(peer)
        ip = ipaddress.ip_address(address.split("%", 1)[0])
    except Exception:  # noqa: BLE001 - validation already happened before connect
        return
    if not ip.is_global:
        raise RemoteDownloadError("The remote server connected through a blocked network address.")


def _filename_from_response(response: httpx.Response, original_url: str) -> str:
    disposition = response.headers.get("content-disposition", "")
    match = re.search(r"filename\*=UTF-8''([^;]+)", disposition, flags=re.IGNORECASE)
    if match:
        candidate = unquote(match.group(1).strip())
    else:
        match = re.search(r'filename="([^"]+)"|filename=([^;]+)', disposition,
                          flags=re.IGNORECASE)
        candidate = (match.group(1) or match.group(2)).strip() if match else ""
    if not candidate:
        query = parse_qs(urlsplit(original_url).query)
        candidate = (query.get("filename") or [""])[0]
    if not candidate:
        candidate = unquote(Path(urlsplit(str(response.url)).path).name)
    candidate = sanitize_filename(candidate or "remote-media")

    if not Path(candidate).suffix:
        content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
        extension = CONTENT_TYPE_EXTENSIONS.get(content_type) or mimetypes.guess_extension(
            content_type, strict=False
        )
        if extension:
            candidate += extension
    return candidate


def _content_length(response: httpx.Response) -> int | None:
    value = response.headers.get("content-length", "").strip()
    if not value:
        return None
    try:
        parsed = int(value)
    except ValueError:
        return None
    return parsed if parsed >= 0 else None


def _validate_content(response: httpx.Response) -> None:
    content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
    if not content_type:
        return
    if content_type.startswith(("video/", "audio/", "image/")):
        return
    if content_type in SAFE_CONTENT_TYPES:
        return
    if content_type.startswith(("text/", "application/json", "application/xml")):
        raise RemoteDownloadError(
            "The URL returned a web page or text response instead of a media file. "
            "Make sure the link is public and permits downloading."
        )


def _looks_like_html(data: bytes) -> bool:
    beginning = data[:1024].lstrip().lower()
    return beginning.startswith((b"<!doctype html", b"<html", b"<?xml"))


def download_remote_media(
    raw_url: str,
    destination_root: Path,
    max_bytes: int,
    activity: ActivityCoordinator,
    progress: Callable[[int, int | None], None] | None = None,
) -> DownloadResult:
    """Stream a public URL to local disk with size, redirect, and SSRF controls."""
    rewritten = _rewrite_cloud_share((raw_url or "").strip())
    current_url, _, _ = _validated_url(rewritten)
    try:
        lease = activity.begin("download", "remote media download")
    except ActivityBusyError as exc:
        raise RemoteDownloadError(str(exc)) from exc

    target_dir = Path(destination_root) / "remote-inputs" / uuid.uuid4().hex
    target_dir.mkdir(parents=True, exist_ok=False)
    try:
        timeout = httpx.Timeout(connect=20.0, read=120.0, write=30.0, pool=20.0)
        headers = {
            "Accept": "video/*, audio/*, image/*, application/octet-stream;q=0.9, */*;q=0.2",
            "User-Agent": "MediaToolbox/1.0 (+https://www.mediatoolbox.pp.ua/)",
        }
        with httpx.Client(timeout=timeout, follow_redirects=False, trust_env=False,
                          headers=headers) as client:
            response: httpx.Response | None = None
            for _ in range(MAX_REDIRECTS + 1):
                if lease.cancel_event.is_set():
                    raise RemoteDownloadCancelled("Remote download cancelled.")
                _validated_url(current_url)
                request = client.build_request("GET", current_url)
                response = client.send(request, stream=True)
                _verify_connected_peer(response)
                if response.is_redirect:
                    location = response.headers.get("location")
                    response.close()
                    if not location:
                        raise RemoteDownloadError("The remote server returned an invalid redirect.")
                    current_url = urljoin(current_url, location)
                    continue
                break
            else:
                raise RemoteDownloadError("The URL redirected too many times.")

            assert response is not None
            try:
                if response.status_code < 200 or response.status_code >= 300:
                    raise RemoteDownloadError(
                        f"The remote server returned HTTP {response.status_code}."
                    )
                _validate_content(response)
                expected = _content_length(response)
                if expected is not None and expected > max_bytes:
                    raise RemoteDownloadError(
                        f"The remote file is larger than the {max_bytes / (1024 ** 3):g} GB limit."
                    )
                free = shutil.disk_usage(destination_root).free
                required = ((expected + 512 * 1024 * 1024) if expected is not None
                            else 1024 * 1024 * 1024)
                if free < required:
                    raise RemoteDownloadError("There is not enough temporary disk space for this URL.")

                filename = _filename_from_response(response, raw_url)
                destination = target_dir / filename
                partial = target_dir / f"{filename}.part"
                downloaded = 0
                first_chunk = True
                with partial.open("xb") as output:
                    for chunk in response.iter_bytes(CHUNK_SIZE):
                        if lease.cancel_event.is_set():
                            raise RemoteDownloadCancelled("Remote download cancelled.")
                        downloaded += len(chunk)
                        if downloaded > max_bytes:
                            raise RemoteDownloadError(
                                f"The remote file exceeded the {max_bytes / (1024 ** 3):g} GB limit."
                            )
                        if first_chunk and _looks_like_html(chunk):
                            raise RemoteDownloadError(
                                "The URL returned a web page instead of a media file. "
                                "Make sure the share link is public and permits downloading."
                            )
                        first_chunk = False
                        output.write(chunk)
                        if progress:
                            progress(downloaded, expected)
                if downloaded == 0:
                    raise RemoteDownloadError("The remote server returned an empty file.")
                os.replace(partial, destination)
                log.info("remote download complete host=%s bytes=%d", urlsplit(current_url).hostname,
                         downloaded)
                return DownloadResult(destination, current_url, downloaded)
            finally:
                response.close()
    except Exception:
        shutil.rmtree(target_dir, ignore_errors=True)
        raise
    finally:
        activity.finish(lease.token)
