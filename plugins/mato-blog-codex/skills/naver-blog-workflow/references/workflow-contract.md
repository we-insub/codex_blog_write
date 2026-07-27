# Workflow contract

## Script location

Resolve the plugin root two levels above this skill directory. Use the local venv Python printed by `scripts/bootstrap.py` after setup.

## Commands

These are internal contracts for Codex. Do not present them as the normal user interface.

```powershell
python scripts/profiles.py discover
python scripts/profiles.py list
python scripts/profiles.py add --slot 1 --alias "업무용" --blog-url "https://blog.naver.com/example"
python scripts/profiles.py check --slot 1 --login
python scripts/parse_request.py --command-file <local-staging/request.txt>
python scripts/ingest_browser_sources.py --prepare --keyword "서울 맛집" --surface blog --versions 1 --command-file <local-staging/request.txt>
python scripts/ingest_browser_sources.py --input <local-staging/browser-sources.json> --keyword "서울 맛집" --surface blog --versions 1 --run-dir <prepared-run-dir>
python scripts/mato_helper_bridge.py url-download --run-dir <prepared-run-dir> --max-images-per-page 80
python scripts/sanitize_images.py <owned-image-or-folder> --recursive --manifest <local-manifest.json>
python scripts/prepare_image_datasets.py --run-dir <run-dir> --source-dir <run-dir>/sources/items/<owned-post-folder>
python scripts/collect.py --keyword "서울 맛집" --surface blog --versions 10 --command "서울 맛집 / 블로그탭 / 10개"
python scripts/write_posts.py --run-dir <run-dir> --input <generated-posts.json> --purge-raw
python scripts/validate_posts.py --run-dir <run-dir> --expected 10
python scripts/upload.py --run-dir <run-dir> --profiles 1,2,3 --mode draft
python scripts/upload.py --run-dir <run-dir> --profiles 1,2,3 --mode draft --execute --confirm <run-id>
python scripts/upload.py --run-dir <run-dir> --resolve-uncertain 2 --entry-key <exact-entry-key> --resolution not-uploaded --confirm <run-id>
python scripts/history.py show --run-dir <run-dir>
```

Put request, Browser-source, and generated-post input files under `~/.googleblog/mato-blog-codex/staging/<id>/`, never in the Git worktree. Delete them after the corresponding handoff.

When no explicit `--run-dir` is supplied, durable research, history, and generated posts go under `Desktop/YYYY-MM-DD/YYYYMMDD_HHMMSS_<keyword>` using KST. Keep temporary handoff JSON in staging; keep the final `sources`, `posts`, `run.json`, and `HISTORY.md` in the Desktop run directory.

`parse_request.py` defaults an omitted surface to `integrated`, an omitted version count to `10`, and an omitted mode to `draft`. A profile number means upload is requested unless the user explicitly says not to upload, save, or publish. `publish` is valid when the original command explicitly says `발행`, `바로발행`, `자동발행`, or another unambiguous public-publish phrase. That phrase authorizes execution in the same task; the saved run ID remains an internal anti-tamper input and is not a second user-confirmation prompt.

`profiles.py check --login` and `upload.py --execute` each use the same numbered persistent-profile runtime. Close the visible login window before executing an upload so the profile can be opened by the upload task. The runtime never copies or prints cookies, credentials, or browser storage values.

Each Browser source object should contain `title`, `url`, `text`, `headings`, paraphrased `notes`, and `image_count`. After ingestion, the bridge calls the plugin-bundled `naver_url_download.py` with the retained rank/title folder names; no Mato Helper source checkout is required for this step. Each source folder contains its local original TXT, `<title>_함축.txt`, real `image_N.jpg` files referenced by matching `[image_N.jpg]` tags, and `image-processing.json`. Image cleanup is enabled by default and removes GPS/EXIF/XMP/ICC payloads after applying EXIF orientation. Use `sanitize_images.py` directly for an owned local image or folder that is unrelated to Naver.

For a user-owned post image workflow, run `prepare_image_datasets.py` only after `write_posts.py` completes. It expects one selected folder below `sources/items/`, cleans that source dataset once, appends the selected image tags to every generated post when tags are absent, and creates a separate copied dataset for every post. Each copied post dataset receives its own cleanup pass and its own `image-processing.json`. Run `validate_posts.py` after this step because the post text now contains image tags.

`ingest_browser_sources.py --prepare` must run before Codex opens the in-app Browser so history includes actual research time. The second call supplies up to five results in displayed order to that prepared run. `collect.py` is a diagnostic fallback and must not replace an explicit in-app Browser request.

## Generated post input

Write UTF-8 JSON with this shape:

```json
{
  "analysis": {
    "search_intent": "string",
    "common_topics": ["string"],
    "source_notes": [
      {"rank": 1, "url": "https://blog.naver.com/example/123", "notes": ["string"]}
    ]
  },
  "posts": [
    {
      "title": "string",
      "intro": ["paragraph"],
      "sections": [
        {"heading": "string", "paragraphs": ["paragraph"]}
      ]
    }
  ]
}
```

The number of posts must equal `request.versions` in `run.json`. `write_posts.py` converts each post to Mato `_함축.txt` format and records the files in the run manifest. `validate_posts.py` binds a passing validation to the exact safe post paths and SHA-256 hashes; any later change requires validation again.

## Run states

Expected stages are `collecting`, `collected`, `generating`, `generated`, `validated`, `upload_planned`, `uploading`, `completed`, and `failed`. `run.json` is the machine-readable source of truth; `HISTORY.md` is the human-readable audit trail.

An upload entry is uniquely identified by post file, profile slot, and mode. Resume only entries without a verified success status.
