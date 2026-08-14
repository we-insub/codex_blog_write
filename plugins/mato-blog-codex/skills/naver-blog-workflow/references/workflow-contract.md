# Workflow contract

## Contents

- Script location and internal commands
- Normal Naver request, Browser, profile, and staging contracts
- MyRealTrip product request, collection-proof, image, writing, and upload contracts
- Generated post input and run states

## Script location

Resolve the plugin root two levels above this skill directory. Bootstrap with `scripts/bootstrap-windows.cmd` on Windows or `sh scripts/bootstrap-macos.sh` on macOS, then use the local venv Python printed by the launcher.

## Commands

These are internal contracts for Codex. Do not present them as the normal user interface.

```powershell
python scripts/profiles.py discover
python scripts/profiles.py list
python scripts/profiles.py add --slot 1 --alias "업무용" --blog-url "https://blog.naver.com/example"
python scripts/profiles.py open --slots 1,2
python scripts/profiles.py check --slot 1 --login
python scripts/profiles.py reset --slot 1 --confirm RESET-1
python scripts/parse_request.py --command-file <local-staging/request.txt>
python scripts/myrealtrip_product.py --input <local-staging/browser-html.json> --classify-output <local-staging/hydration-candidates.json>
python scripts/myrealtrip_product.py --input <local-staging/browser-product.json> --output-dir <run-dir>/sources/myrealtrip-product --facts-output <local-staging/product-facts.json> --purge-input --max-images 80
python scripts/prompt_manager.py init --prompt-key 공통
python scripts/prompt_manager.py export --prompt-key 공통 --output <local-staging/mato-common-prompt.txt>
python scripts/product_writing.py brief --run-dir <run-dir> --facts <local-staging/product-facts.json> --image-manifest <run-dir>/sources/myrealtrip-product/manifest.json --visual-summaries <local-staging/visual-summaries.json> --brief-output <run-dir>/analysis/product-writing-brief.json --overlay-output <local-staging/product-overlay.txt> --purge-facts
python scripts/product_writing.py finalize --run-dir <run-dir> --input <local-staging/raw-product-post.json> --brief <run-dir>/analysis/product-writing-brief.json --output <local-staging/finalized-product-post.json>
python scripts/write_posts.py --run-dir <run-dir> --input <local-staging/finalized-product-post.json>
python scripts/prepare_image_datasets.py --run-dir <run-dir> --source-dir <run-dir>/sources/myrealtrip-product/prepared
python scripts/validate_posts.py --run-dir <run-dir> --expected 1
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

For ordinary Naver-search input, `parse_request.py` defaults an omitted surface to `integrated`, an omitted version count to `10`, and an omitted mode to `draft`. A profile number means upload is requested unless the user explicitly says not to upload, save, or publish. `publish` is valid when the original command explicitly says `발행`, `바로발행`, `자동발행`, or another unambiguous public-publish phrase. That phrase authorizes execution in the same task; the saved run ID remains an internal anti-tamper input and is not a second user-confirmation prompt.

`profiles.py open --slots <N,...>` starts each selected persistent Chrome with a loopback-only connector and leaves its visible window running. `profiles.py check --login` reconnects to that open profile when available. For a visible `upload.py --execute`, the uploader starts the retained profile host when needed or reconnects to the existing host, completes each profile serially, leaves every completed Chrome window open and idle, and then advances to the next profile. The connector never copies or prints cookies, credentials, or browser storage values.

Each Browser source object should contain `title`, `url`, `text`, `headings`, paraphrased `notes`, and `image_count`. After ingestion, the bridge calls the plugin-bundled `naver_url_download.py` with the retained rank/title folder names; no Mato Helper source checkout is required for this step. Each source folder contains its local original TXT, `<title>_함축.txt`, real `image_N.jpg` files referenced by matching `[image_N.jpg]` tags, and `image-processing.json`. Image cleanup is enabled by default and removes GPS/EXIF/XMP/ICC payloads after applying EXIF orientation. Use `sanitize_images.py` directly for an owned local image or folder that is unrelated to Naver.

When a user supplies `https://m.blog.naver.com/<blogId>/<logNo>`, retain that address in the request record and normalize the collection URL to `https://blog.naver.com/<blogId>/<logNo>`. Store the normalized desktop URL as the source object's `url`; it is the canonical input for Browser collection and `url-download`. This normalization does not permit bypassing a login requirement, CAPTCHA, access restriction, or Browser safety block.

For a user-owned post image workflow, run `prepare_image_datasets.py` only after `write_posts.py` completes. It expects one selected folder below `sources/items/`, cleans that source dataset once, appends the selected image tags to every generated post when tags are absent, and creates a separate copied dataset for every post. Each copied post dataset receives its own cleanup pass and its own `image-processing.json`. Run `validate_posts.py` after this step because the post text now contains image tags.

`ingest_browser_sources.py --prepare` must run before Codex opens the in-app Browser so history includes actual research time. The second call supplies up to five results in displayed order to that prepared run. `collect.py` is a diagnostic fallback and must not replace an explicit in-app Browser request.

## MyRealTrip product contract

A canonical `https://experiences.myrealtrip.com/products/<id>`, validated `https://myrealt.rip/<code>`, or MyRealTrip marketing bridge URL selects the product branch. The parsed request has this durable shape and is passed to `history.create_run(..., request_fields=request)`:

```json
{
  "source_type": "myrealtrip_product",
  "channel": "naver",
  "surface": "product",
  "versions": 1,
  "product_url": "the exact URL entered by the user",
  "keyword": "main keyword or an empty string; never the URL",
  "main_keyword": "optional",
  "subkeywords": ["one", "or more"],
  "hook": "optional; defaults later to 솔직후기",
  "companions": ["optional audience facts"],
  "experience_notes": ["only user-supplied experience facts"],
  "image_policy": {
    "mode": "all_unique_seller_product_images",
    "permission_confirmed": false,
    "max_images": 80
  },
  "link_wait_ms": 2000
}
```

Product v1 rejects a non-Naver channel, a version count other than one in the writing stage, `max_images` outside `1..80`, or seller-image use without an explicit permission statement in the current request. Asking to use images is not itself proof of permission. Do not silently change the policy to bypass this gate.

The in-app Browser must render the validated canonical product page and confirm that `INTRODUCTION`, `INCLUDE_EXCLUDE`, `USAGE`, `ESSENTIALS`, `REFUND`, and `REVIEW` exist. `ITINERARIES` is optional. Expand every expandable section that actually exists: `INTRODUCTION`, `ESSENTIALS`, `REVIEW`, and `ITINERARIES` only when present. Open all reviews, visit all gallery slides, and reach the page end. First save the exact product URL plus one of `rendered_html` or `html_file`, run the `--classify-output` command above, and use its exact candidate order and `dom_index` against the unchanged live DOM. Record every allowed DOM candidate only after its image load completes. The final ephemeral `browser-product.json` must contain the same HTML plus the following evidence:

```json
{
  "product_url": "exact request.product_url",
  "rendered_html": "complete rendered DOM",
  "hydrated_rows": [
    {
      "candidate_id": "gallery:1",
      "dom_index": 27,
      "document_image_index": 26,
      "source_role": "gallery|introduction|itinerary",
      "current_src": "https seller image URL",
      "complete": true,
      "natural_width": 1200,
      "natural_height": 800
    }
  ],
  "collection_proof": {
    "page_url": "https://experiences.myrealtrip.com/products/<id>",
    "document_ready_state": "complete",
    "available_sections": ["INTRODUCTION", "ITINERARIES", "INCLUDE_EXCLUDE", "USAGE", "ESSENTIALS", "REFUND", "REVIEW"],
    "expanded_sections": ["INTRODUCTION", "ITINERARIES", "ESSENTIALS", "REVIEW"],
    "review_all_opened": true,
    "page_end_reached": true,
    "scroll_height": 10000,
    "max_scroll_y": 9200,
    "viewport_height": 800,
    "seller_candidate_count": 12,
    "hydrated_candidate_count": 12,
    "gallery_visited_count": 5,
    "stable_candidate_counts": [12, 12]
  }
}
```

Candidate IDs and `available_sections` must come from `hydration-candidates.json`. Candidate IDs match the rendered-DOM classifier's one-based allowed-role order (`gallery:1`, `introduction:1`, `itinerary:1`, and so on); do not invent IDs, reimplement the CSS rules, or include denied rows. Use the emitted zero-based `document_image_index` to read `document.images[document_image_index]`; `dom_index` is the corresponding one-based audit value. The classifier excludes inert `noscript` and `template` image markup so these indexes remain aligned with live `document.images`. Cross-check the emitted role and current URL against the unchanged live DOM. `hydrated_rows` must have exactly the same candidate order and count as the classifier output. The proof's `available_sections` must match the classifier exactly, and `expanded_sections` must contain exactly the expandable members that are present; for a product without `ITINERARIES`, neither list may claim an itinerary section.

`myrealtrip_product.py` compares the proof and hydration order with its rendered-DOM classification. It allows only current-product seller `gallery`, `introduction`, and `itinerary` images. Review/traveler photos, recommendations and other products, UI/profile assets, and placeholders are denied before host allowlisting. It applies the requested 80 limit after URL, byte, and pixel deduplication; a separate larger candidate safety guard rejects a broken/noisy DOM. The fresh bundle directory contains `manifest.json`, response bytes in `originals/`, and contiguous metadata-free `prepared/image_N.jpg` upload copies. `--facts-output` is an external managed-staging JSON containing product facts and sampled review text; consume it with `product_writing.py brief --purge-facts` so raw review sentences do not become durable writing evidence. Product title planning automatically loads compact generated titles from bounded current/dated sibling run histories; `--prior-titles` may override that list but no prior body text is read.

Visually inspect every prepared JPEG and create exactly one summary for every manifest index:

```json
{
  "images": [
    {"index": 1, "visual_summary": "visible scene only, without speculation"}
  ]
}
```

The prior-title staging input is `{"titles": ["title only"]}` and must not contain prior post bodies. The brief command requires that input and the exact durable manifest, then writes exactly `<run>/analysis/product-writing-brief.json` plus an ephemeral overlay. It produces five hard-validated Naver title candidates, selects one deterministically with history-duplicate penalty, blocks a recognized region mismatch, aggregates reviews as non-quoting trends, and binds every seller image. Generate one raw post using the exported `공통` prompt plus this overlay; do not edit `공통.txt`, and do not put URLs or image tags in the raw model output. Companions define the target persona only. First-person experience is allowed solely for facts explicitly present in `experience_notes`; review text never supplies user experience.

Run `product_writing.py finalize`, then `write_posts.py`, `prepare_image_datasets.py --source-dir <run>/sources/myrealtrip-product/prepared`, and `validate_posts.py --expected 1` in that order. The finalizer supplies the exact selected title, fact and experience evidence, sequential image placements, manifest path, and exact original `product_url`. The writer makes that URL the first and last non-empty `본문2:` lines, exactly twice. Product image preparation copies the manifest-bound prepared JPEGs without another re-encode. The uploader waits `2000ms` after each of the two URL lines; any changed URL, missing/extra image, changed hash, incomplete proof, or missing permission invalidates the run.

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
