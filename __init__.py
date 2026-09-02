"""
share-artifact plugin for Hermes Agent.

Registers three publish tools + one delete tool in the ``share-artifact``
toolset, and starts a small local HTTP server (127.0.0.1-only) that a
reverse proxy (Caddy/nginx already terminating TLS for this Hermes
instance) forwards ``/share/artifact/*`` to.

Storage: local filesystem, under this plugin's profile-scoped data dir
(``ctx.state.data_dir``). No git, no repo, no external push - publishing
is a local file write plus an HMAC signature, nothing leaves the box
until someone opens the URL.

URL shape: ``https://<host>/share/artifact/<id>/<sign>/`` - the signature
(not a login wall) is the access control: holding the full URL is what
grants viewing, and ids/signs are not guessable or enumerable.

Config (all optional; ``plugins.entries.share-artifact.settings.*`` in
config.yaml)::

    plugins:
      entries:
        share-artifact:
          settings:
            port: 8791          # local bind port for the artifact server
            base_url: "https://harness.dada-tuda.ru"  # public origin
                                 # (defaults to HERMES_DASHBOARD_PUBLIC_URL)

The HMAC secret is generated once on first use and persisted in this
plugin's durable state (``ctx.state``) - never in git, never in config.yaml.

Zero core edits - everything goes through the public PluginContext surface
(``ctx.register_tool``, ``ctx.get_config``, ``ctx.state``).
"""

from __future__ import annotations

import logging
import os
import secrets
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

__all__ = ["register"]

_DEFAULT_PORT = 8791
_SERVER: "Any" = None  # module-level singleton; see _get_server()


def _settings(ctx) -> dict:
    return {
        "port": int(ctx.get_config("port", _DEFAULT_PORT) or _DEFAULT_PORT),
        "base_url": (
            ctx.get_config("base_url", "")
            or os.getenv("HERMES_DASHBOARD_PUBLIC_URL", "")
            or f"http://127.0.0.1:{ctx.get_config('port', _DEFAULT_PORT)}"
        ).rstrip("/"),
    }


def _get_secret(ctx) -> str:
    secret = ctx.state.get("hmac_secret", "")
    if not secret:
        secret = secrets.token_hex(32)
        ctx.state.set("hmac_secret", secret)
    return secret


def _get_store(ctx):
    from .store import ArtifactStore

    data_dir = ctx.state.data_dir / "artifacts"
    return ArtifactStore(data_dir, _get_secret(ctx))


def _get_server(ctx):
    global _SERVER
    from .store import ArtifactServer

    if _SERVER is not None and _SERVER.running:
        return _SERVER
    cfg = _settings(ctx)
    store = _get_store(ctx)
    _SERVER = ArtifactServer(store, host="127.0.0.1", port=cfg["port"])
    _SERVER.start()
    return _SERVER


def _public_url(ctx, artifact_id: str, sign: str) -> str:
    cfg = _settings(ctx)
    return f"{cfg['base_url']}/share/artifact/{artifact_id}/{sign}/"


def _record_artifact(ctx, artifact_id: str, url: str, title: str) -> None:
    try:
        history = ctx.state.get("published", [])
        history.append({"id": artifact_id, "url": url, "title": title})
        ctx.state.set("published", history[-200:])
    except Exception:
        logger.debug("share-artifact: failed to record history", exc_info=True)


def _publish_common(ctx, *, path=None, html=None, title="", entrypoint="index.html") -> str:
    from .store import PublishError

    _get_server(ctx)  # ensure the server is up before handing out a URL
    store = _get_store(ctx)
    try:
        meta = store.publish(path=path, html=html, title=title, entrypoint=entrypoint)
    except PublishError as e:
        return f"Error: publish failed - {e}"
    except Exception as e:
        logger.warning("share-artifact: publish failed", exc_info=True)
        return f"Error: publish failed - {e}"

    url = _public_url(ctx, meta["id"], meta["sign"])
    _record_artifact(ctx, meta["id"], url, meta["title"])
    return (
        f"Published: {meta['title']}\n"
        f"URL: {url}\n"
        f"Artifact ID: {meta['id']}\n"
        "Anyone with this URL can view it (public, no login) - the URL "
        "itself is the access control, so don't paste it anywhere you "
        "wouldn't paste the content."
    )


def _make_publish_artifact(ctx):
    def _handler(args: dict, **_: Any) -> str:
        raw_path = str(args.get("path") or "").strip()
        if not raw_path:
            return "Error: 'path' is required (file or directory to publish)."
        title = str(args.get("title") or "").strip()
        entrypoint = str(args.get("entrypoint") or "index.html").strip()

        path = Path(raw_path).expanduser()
        if not path.is_absolute():
            path = Path.cwd() / path
        if not path.exists():
            return f"Error: path does not exist: {path}"

        return _publish_common(ctx, path=path, title=title, entrypoint=entrypoint)

    return _handler


def _make_publish_html(ctx):
    def _handler(args: dict, **_: Any) -> str:
        html = args.get("html")
        if not isinstance(html, str) or not html.strip():
            return "Error: 'html' is required (a non-empty HTML string)."
        title = str(args.get("title") or "").strip()
        return _publish_common(ctx, html=html, title=title)

    return _handler


def _make_publish_markdown(ctx):
    def _handler(args: dict, **_: Any) -> str:
        from .store import render_markdown_html

        markdown = args.get("markdown")
        if not isinstance(markdown, str) or not markdown.strip():
            return "Error: 'markdown' is required (a non-empty Markdown string)."
        title = str(args.get("title") or "").strip()
        html = render_markdown_html(markdown, title)
        return _publish_common(ctx, html=html, title=title)

    return _handler


def _make_delete_artifact(ctx):
    def _handler(args: dict, **_: Any) -> str:
        from .store import PublishError

        artifact_id = str(args.get("artifact_id") or "").strip()
        if not artifact_id:
            return "Error: 'artifact_id' is required."

        store = _get_store(ctx)
        try:
            found = store.delete(artifact_id)
        except PublishError as e:
            return f"Error: delete failed - {e}"
        except Exception as e:
            logger.warning("share-artifact: delete failed", exc_info=True)
            return f"Error: delete failed - {e}"

        if not found:
            return f"No artifact found with id '{artifact_id}'."
        return f"Deleted artifact '{artifact_id}'."

    return _handler


def _make_list_artifacts(ctx):
    def _handler(args: dict | None = None, **_: Any) -> str:
        history = ctx.state.get("published", [])
        if not history:
            return "No artifacts published yet."
        lines = [f"Published artifacts ({len(history)}):"]
        for item in history[-50:]:
            lines.append(f"  - {item.get('id')}: {item.get('title')} -> {item.get('url')}")
        return "\n".join(lines)

    return _handler


_SCHEMAS: dict[str, dict] = {
    "publish_artifact": {
        "type": "function",
        "function": {
            "name": "publish_artifact",
            "description": (
                "Publish a local HTML file/report or a directory (e.g. an "
                "index.html plus assets/) as a public, standalone web page. "
                "Returns a shareable URL anyone can open - no login needed. "
                "Use this for interactive reports, dashboards, or rendered "
                "documents you want to hand to a human instead of pasting "
                "raw HTML into the chat."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Absolute or cwd-relative path to the file or directory to publish."},
                    "title": {"type": "string", "description": "Human title for the artifact."},
                    "entrypoint": {"type": "string", "description": "For directories: which file to serve as the page (default index.html)."},
                },
                "required": ["path"],
            },
        },
    },
    "publish_html": {
        "type": "function",
        "function": {
            "name": "publish_html",
            "description": (
                "Publish a raw HTML string as a public, standalone web page "
                "and get back a shareable URL. Use this when you generated "
                "HTML in-memory and don't want to write a temp file first."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "html": {"type": "string", "description": "Full HTML document to publish."},
                    "title": {"type": "string", "description": "Human title for the artifact."},
                },
                "required": ["html"],
            },
        },
    },
    "publish_markdown": {
        "type": "function",
        "function": {
            "name": "publish_markdown",
            "description": (
                "Publish a Markdown string as a public, standalone rendered "
                "web page (GitHub-flavored styling) and get back a shareable "
                "URL. Use this for reports/summaries you want a human to "
                "read as a page, not as raw chat text."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "markdown": {"type": "string", "description": "Markdown source to render and publish."},
                    "title": {"type": "string", "description": "Human title for the artifact."},
                },
                "required": ["markdown"],
            },
        },
    },
    "delete_artifact": {
        "type": "function",
        "function": {
            "name": "delete_artifact",
            "description": "Remove a previously published artifact by its id, taking down the public URL.",
            "parameters": {
                "type": "object",
                "properties": {
                    "artifact_id": {"type": "string", "description": "The artifact id returned by publish_*."},
                },
                "required": ["artifact_id"],
            },
        },
    },
    "list_artifacts": {
        "type": "function",
        "function": {
            "name": "list_artifacts",
            "description": "List artifacts published in this Hermes profile (id, title, URL).",
            "parameters": {"type": "object", "properties": {}},
        },
    },
}


def register(ctx) -> None:
    """Plugin entry point - called by the Hermes plugin system."""
    try:
        ctx.register_tool(
            name="publish_artifact",
            toolset="share-artifact",
            schema=_SCHEMAS["publish_artifact"],
            handler=_make_publish_artifact(ctx),
            description=_SCHEMAS["publish_artifact"]["function"]["description"],
            emoji="\U0001f4e4",
        )
        ctx.register_tool(
            name="publish_html",
            toolset="share-artifact",
            schema=_SCHEMAS["publish_html"],
            handler=_make_publish_html(ctx),
            description=_SCHEMAS["publish_html"]["function"]["description"],
            emoji="\U0001f4e4",
        )
        ctx.register_tool(
            name="publish_markdown",
            toolset="share-artifact",
            schema=_SCHEMAS["publish_markdown"],
            handler=_make_publish_markdown(ctx),
            description=_SCHEMAS["publish_markdown"]["function"]["description"],
            emoji="\U0001f4e4",
        )
        ctx.register_tool(
            name="delete_artifact",
            toolset="share-artifact",
            schema=_SCHEMAS["delete_artifact"],
            handler=_make_delete_artifact(ctx),
            description=_SCHEMAS["delete_artifact"]["function"]["description"],
            emoji="\U0001f5d1",
        )
        ctx.register_tool(
            name="list_artifacts",
            toolset="share-artifact",
            schema=_SCHEMAS["list_artifacts"],
            handler=_make_list_artifacts(ctx),
            description=_SCHEMAS["list_artifacts"]["function"]["description"],
            emoji="\U0001f4cb",
        )
    except Exception:
        logger.warning("share-artifact: failed to register tools", exc_info=True)

    # Start the local artifact HTTP server eagerly so the reverse-proxy
    # route is live even before the first publish call.
    try:
        _get_server(ctx)
    except Exception:
        logger.warning("share-artifact: failed to start artifact server", exc_info=True)
