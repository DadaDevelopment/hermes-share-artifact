"""
share-artifact plugin for Hermes Agent.

Registers three tools in the ``share-artifact`` toolset:

  - publish_artifact(path, title?, entrypoint?)  -> publish a file or directory
  - publish_html(html, title?)                    -> publish inline HTML
  - delete_artifact(artifact_id)                  -> remove a published artifact

Storage: a GitHub repo's gh-pages branch, one directory per artifact under
``a/<id>/``, served by GitHub Pages. No server to run, no S3 bucket to own -
git push is the publish step and GitHub Pages is the CDN. Public by
construction: anyone with the URL can view it, no auth.

Config (optional, all have defaults suitable for DadaDevelopment/hermes-artifacts)::

    plugins:
      entries:
        share-artifact:
          settings:
            repo: "DadaDevelopment/hermes-artifacts"
            branch: "gh-pages"
            base_url: "https://dadadevelopment.github.io/hermes-artifacts/"

Zero core edits - everything goes through the public PluginContext surface
(``ctx.register_tool``, ``ctx.get_config``, ``ctx.state``).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

__all__ = ["register"]

_DEFAULT_REPO = "DadaDevelopment/hermes-artifacts"
_DEFAULT_BRANCH = "gh-pages"
_DEFAULT_BASE_URL = "https://dadadevelopment.github.io/hermes-artifacts/"


def _settings(ctx) -> dict:
    return {
        "repo": ctx.get_config("repo", _DEFAULT_REPO),
        "branch": ctx.get_config("branch", _DEFAULT_BRANCH),
        "base_url": ctx.get_config("base_url", _DEFAULT_BASE_URL),
    }


def _clone_dir(ctx) -> Path:
    return ctx.state.data_dir / "repo"


def _record_artifact(ctx, artifact_id: str, url: str, title: str) -> None:
    try:
        history = ctx.state.get("published", [])
        history.append({"id": artifact_id, "url": url, "title": title})
        ctx.state.set("published", history[-200:])
    except Exception:
        logger.debug("share-artifact: failed to record history", exc_info=True)


def _make_publish_artifact(ctx):
    def _handler(args: dict, **_: Any) -> str:
        from .publisher import publish, PublishError

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

        cfg = _settings(ctx)
        try:
            result = publish(
                repo_dir=_clone_dir(ctx),
                repo=cfg["repo"],
                branch=cfg["branch"],
                base_url=cfg["base_url"],
                path=path,
                title=title,
                entrypoint=entrypoint,
            )
        except PublishError as e:
            return f"Error: publish failed - {e}"
        except Exception as e:
            logger.warning("share-artifact: publish_artifact failed", exc_info=True)
            return f"Error: publish failed - {e}"

        _record_artifact(ctx, result.id, result.url, result.title)
        return (
            f"Published: {result.title}\n"
            f"URL: {result.url}\n"
            f"Artifact ID: {result.id}\n"
            "Anyone with this URL can view it (public, no login)."
        )

    return _handler


def _make_publish_html(ctx):
    def _handler(args: dict, **_: Any) -> str:
        from .publisher import publish, PublishError

        html = args.get("html")
        if not isinstance(html, str) or not html.strip():
            return "Error: 'html' is required (a non-empty HTML string)."
        title = str(args.get("title") or "").strip()

        cfg = _settings(ctx)
        try:
            result = publish(
                repo_dir=_clone_dir(ctx),
                repo=cfg["repo"],
                branch=cfg["branch"],
                base_url=cfg["base_url"],
                html=html,
                title=title,
            )
        except PublishError as e:
            return f"Error: publish failed - {e}"
        except Exception as e:
            logger.warning("share-artifact: publish_html failed", exc_info=True)
            return f"Error: publish failed - {e}"

        _record_artifact(ctx, result.id, result.url, result.title)
        return (
            f"Published: {result.title}\n"
            f"URL: {result.url}\n"
            f"Artifact ID: {result.id}\n"
            "Anyone with this URL can view it (public, no login)."
        )

    return _handler


def _make_publish_markdown(ctx):
    def _handler(args: dict, **_: Any) -> str:
        from .publisher import publish, render_markdown_html, PublishError

        markdown = args.get("markdown")
        if not isinstance(markdown, str) or not markdown.strip():
            return "Error: 'markdown' is required (a non-empty Markdown string)."
        title = str(args.get("title") or "").strip()
        html = render_markdown_html(markdown, title)

        cfg = _settings(ctx)
        try:
            result = publish(
                repo_dir=_clone_dir(ctx),
                repo=cfg["repo"],
                branch=cfg["branch"],
                base_url=cfg["base_url"],
                html=html,
                title=title,
            )
        except PublishError as e:
            return f"Error: publish failed - {e}"
        except Exception as e:
            logger.warning("share-artifact: publish_markdown failed", exc_info=True)
            return f"Error: publish failed - {e}"

        _record_artifact(ctx, result.id, result.url, result.title)
        return (
            f"Published: {result.title}\n"
            f"URL: {result.url}\n"
            f"Artifact ID: {result.id}\n"
            "Anyone with this URL can view it (public, no login)."
        )

    return _handler


def _make_delete_artifact(ctx):
    def _handler(args: dict, **_: Any) -> str:
        from .publisher import delete, PublishError

        artifact_id = str(args.get("artifact_id") or "").strip()
        if not artifact_id:
            return "Error: 'artifact_id' is required."

        cfg = _settings(ctx)
        try:
            found = delete(
                repo_dir=_clone_dir(ctx),
                repo=cfg["repo"],
                branch=cfg["branch"],
                artifact_id=artifact_id,
            )
        except PublishError as e:
            return f"Error: delete failed - {e}"
        except Exception as e:
            logger.warning("share-artifact: delete_artifact failed", exc_info=True)
            return f"Error: delete failed - {e}"

        if not found:
            return f"No artifact found with id '{artifact_id}'."
        return f"Deleted artifact '{artifact_id}'."

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
                    "path": {
                        "type": "string",
                        "description": "Absolute or cwd-relative path to the file or directory to publish.",
                    },
                    "title": {
                        "type": "string",
                        "description": "Human title for the artifact (used in the returned summary).",
                    },
                    "entrypoint": {
                        "type": "string",
                        "description": "For directories: which file to serve as the page (default index.html).",
                    },
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
    except Exception:
        logger.warning("share-artifact: failed to register tools", exc_info=True)
