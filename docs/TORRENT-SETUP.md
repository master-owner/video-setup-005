# Torrent and magnet sources — 2026.10.14

Update both the GitHub Actions files and the Vercel portal. This update is for **video-setup**, not the separate Cloudflare subtitle extractor. No changes to your private Hugging Face Space or encrypted mappings service are required.

## Install

1. Copy the contents of the bundle's `github-actions/` directory to your existing Actions repository root, including `.github/workflows/`. Commit and push. Keep your existing repository fonts and settings.
2. Replace the corresponding files in your Vercel project with the bundle's `vercel-ui/` files and redeploy. Preserve the existing environment variables. The new API route is `/api/torrent`; the new browser file is `/torrent.js`.
3. No extra API token is needed. Your existing Vercel GitHub token still needs Contents read/write and Actions read/write. The workflow uses its existing `contents: write` permission for per-job selections. The media-tools step installs Ubuntu's `python3-libtorrent` and invokes the Ubuntu system `python3`.

## Use

1. In **The source → Download source**, choose **Upload .torrent** or **Magnet link**. Direct HTTPS / Drive / Dropbox remains available.
2. Drop/browse a public `.torrent` file (up to 1 MB) or paste a public v1 magnet link.
3. Set your filename, subtitle method and other normal options. Click **Start encoding**. The runner first loads torrent metadata; no video payload is requested while waiting for your choice.
4. If there is one supported video file, it is selected automatically. With several videos, **Track a requested run → Choose torrent video** shows filenames and GiB sizes. Select one and click **Download selected video** within 20 minutes.
5. The **same GitHub run** downloads that video and continues into your chosen Burn / On-off / MP4-transfer pipeline. If multiple audio tracks need selection, that choice appears afterwards using the existing audio UI. Do not click Start encoding to confirm a waiting file or audio choice.

You can close the tab and return to the same browser; tracked job tokens are already persisted. Each run has its own metadata path, selection state and temporary download directory, so parallel requests do not share selections or video files.

## What downloads

All unselected files have priority zero. BitTorrent v1 pieces may overlap file boundaries, so a small amount of adjacent-file data may also be fetched to verify a piece. Only the chosen complete video is passed to FFmpeg. Non-video files are never executed. Video payloads stay on the GitHub runner; the portal only uploads the small torrent metadata file. If the Actions repo is public, the uploaded `.torrent` and its filenames are public too.

The selected video must be at most 12 GB and fit the runner's available disk space with processing reserve. Oversized selections fail before payload download. Metadata waits at most 10 minutes; file selection waits at most 20 minutes; the download has a 2-hour cap and fails after 15 minutes without progress. The existing overall workflow timeout still applies, so long downloads leave less encoding time. Seed availability and runner networking determine speed; this does not make a slow torrent fast.

This first version supports **public BitTorrent v1** torrents/magnets. Private torrents and v2-only torrents are rejected. Public UDP trackers are restored after public-DNS validation, with three public fallback trackers and multiple DHT bootstrap nodes. HTTP(S) trackers, web seeds, query/passkey tracker URLs and magnet peer overrides are ignored. Torrents needing those services may not start. Local peer discovery, UPnP and NAT-PMP are disabled and private/local peer IP ranges are blocked. No long-running seedbox is provided: the session stops after the selected video completes; a small upload rate is allowed while downloading. Do not use it for private tracker ratio requirements.

Diagnostics include DHT node count and aggregate tracker reply/error counts, without printing raw alert messages. No tracker URLs, raw peer errors or magnet strings are printed by the script or returned by the status endpoint. Streamtape handling and mapping encryption stay as before. Source metadata and selection JSON remain in repository history unless you remove them; the UI never treats a completed run as still waiting for file selection.

## Validation

Portal tests cover existing behavior plus authenticated file selection, duplicate selections, CSRF, exact file membership and cross-run isolation. Python checks cover single-video automatic selection, unsafe paths/symlinks, multiple-file waiting, same-run continuation and selecting only one video's verified bytes. Torrent metadata parsing was checked against the supplied Merlin file without joining its swarm or downloading episodes.

Tests use mocked peers/GitHub APIs. A live libtorrent download could not be exercised on this Windows environment because the available wheel requires additional OpenSSL libraries; installing those test libraries was blocked by an automatic approval-review usage limit. Ubuntu runtime and a real GitHub/Streamtape run have not been exercised. No actual workflow run or deployment was triggered.

References: [libtorrent Python bindings](https://www.libtorrent.org/python_binding.html), [metadata-only mode](https://www.libtorrent.org/reference-Session.html), [verified-piece file progress](https://www.libtorrent.org/reference-Torrent_Handle.html#file_progress()).

Peer-discovery fix: if the old version is stuck at peers 0, stop that run and update scripts/clixarena_torrent.py before requesting a new run. For .torrent uploads this script is the only required code update; redeploy the portal too to preserve public UDP trackers from magnet links. DHT nodes 0 and no tracker replies can indicate blocked discovery/networking; healthy discovery with no peers can indicate an inactive swarm. The numbers are diagnostics, not proof of any one cause.
