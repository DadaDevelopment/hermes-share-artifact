# hermes-share-artifact

Hermes Agent plugin: publish HTML/Markdown/files as a public, shareable web
page - Claude-Code-Artifacts semantics (generate -> public URL -> anyone can
open it). Local filesystem storage, signed URLs, no git, no external
service, no server to own beyond the Hermes host itself.

## What it does

Tools in the `share-artifact` toolset:

- `publish_artifact(path, title?, entrypoint?)` - publish a local file or
  directory (e.g. `index.html` + `assets/`).
- `publish_html(html, title?)` - publish an in-memory HTML string.
- `publish_markdown(markdown, title?)` - publish Markdown, rendered with
  GitHub styling.
- `delete_artifact(artifact_id)` - take an artifact down.
- `list_artifacts()` - list what's been published.

Storage is the local filesystem under this plugin's profile-scoped data
dir. A small stdlib HTTP server binds `127.0.0.1:<port>` (default 8791)
and serves `/share/artifact/<id>/<sign>/...`. The `<sign>` is an HMAC over
the id with a secret generated on first use and kept in plugin state
(never in git) - it IS the access control: no login wall, but also no
enumeration or guessing.

## Install

```
hermes plugins install DadaDevelopment/hermes-share-artifact
hermes plugins enable share-artifact
```

### One-time reverse-proxy route (operator)

Whatever already terminates public TLS for this Hermes instance needs one
added route, forwarding `/share/artifact/*` to the plugin's local port
(default 8791). Caddy example:

```caddyfile
handle_path /share/artifact/* {
    reverse_proxy 127.0.0.1:8791
}
```

Reload the proxy. That's the entire deploy footprint - no new container,
no database, no external dependency.

## Configure (optional)

```yaml
plugins:
  entries:
    share-artifact:
      settings:
        port: 8791
        base_url: "https://harness.dada-tuda.ru"
```

`base_url` defaults to the `HERMES_DASHBOARD_PUBLIC_URL` env var.

## Why local-disk + signed URL instead of git/S3/a SaaS

Zero external side effects: publishing never leaves the box until a human
opens the link. No git history to accumulate, no repo to own, no push
races, no third-party storage bill. Trade-off: artifacts live only as long
as this host's disk does (no built-in cross-machine replication) - fine
for reports/dashboards handed to one person, not for content meant to
outlive the host.

## License

MIT
