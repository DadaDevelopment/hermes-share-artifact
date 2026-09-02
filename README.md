# hermes-share-artifact

Hermes Agent plugin: publish HTML/Markdown/files as a public, shareable web
page - Claude-Code-Artifacts semantics (generate -> public URL -> anyone can
open it), no server to run.

## What it does

Three tools in the `share-artifact` toolset:

- `publish_artifact(path, title?, entrypoint?)` - publish a local file or
  directory (e.g. `index.html` + `assets/`).
- `publish_html(html, title?)` - publish an in-memory HTML string.
- `publish_markdown(markdown, title?)` - publish Markdown, rendered with
  GitHub styling.
- `delete_artifact(artifact_id)` - take an artifact down.

Storage is a GitHub repo's `gh-pages` branch (default:
[`DadaDevelopment/hermes-artifacts`](https://github.com/DadaDevelopment/hermes-artifacts)),
served for free by GitHub Pages, one directory per artifact under `a/<id>/`.
Publishing is `git clone/pull -> copy files -> commit -> push`. No S3, no
database, no daemon.

## Install

```
hermes plugins install DadaDevelopment/hermes-share-artifact
hermes plugins enable share-artifact
```

The machine running Hermes needs `git` and push access (via `gh auth login`
or an SSH deploy key) to whichever repo you configure as the artifact store.

## Configure (optional)

Defaults point at the public `DadaDevelopment/hermes-artifacts` repo. To use
your own:

```yaml
plugins:
  entries:
    share-artifact:
      settings:
        repo: "your-org/your-artifacts-repo"
        branch: "gh-pages"
        base_url: "https://your-org.github.io/your-artifacts-repo/"
```

Your repo's `gh-pages` branch must exist with Pages enabled:

```
gh api -X PUT repos/<owner>/<repo>/pages -f "source[branch]=gh-pages" -f "source[path]=/"
```

## Why git+Pages instead of S3/a custom backend

MVP-simple, zero infra to operate, free hosting, and the repo itself is a
readable audit log of every artifact ever published (git log = publish
history). Trade-off: publish latency is "a git push" (~1-3s), not
instant-CDN; fine for reports/dashboards, not for high-frequency streaming
updates.

## License

MIT
