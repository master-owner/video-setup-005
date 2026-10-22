Release 2026.10.11-r6: MAPPING_PASSWORD must match the portal environment variable. Current mappings are encrypted by the portal Lock existing mappings.json button or the next successful updated mapping job. Plaintext public history is unchanged.

# CLIXARENA advanced workflow

Upload all ZIP contents to the repository root, preserving paths. Replace the workflow and BOTH Python scripts together. Files do not trigger a run; use Run workflow manually.

## Required files and secrets

- fonts/IskoolaPota.ttf: licensed modern Iskoola Pota (version 6+, locally tested with 6.96).
- fonts/Algerian.ttf: licensed Algerian, needed when the title overlay is enabled.
- subtitles/: use GitHub Add file > Upload files to drag-and-drop your Sinhala or English SRT, with ANY filename. Keep exactly one SRT for automatic selection; otherwise enter its repository path. It is copied to sinhala.srt in the runner, without modifying the repository.
- Actions repository secrets: STREAMTAPE_LOGIN, STREAMTAPE_KEY, TMDB_READ_TOKEN. The latter is the TMDB API Read Access Token (not the short API key). Streamtape secrets are unnecessary in sample mode. TMDB token and Algerian are unnecessary when tmdb_url is blank.

No proprietary fonts are included. Only upload fonts where the license permits repository redistribution.

## Subtitle upload

Open subtitle-upload.html locally, drop your SRT, download the renamed sinhala.srt, then upload it to your repository's subtitles/ folder. This page sends nothing to a server and needs no token. Alternatively upload the original filename directly in GitHub: runner normalization handles it. GitHub's native Run workflow form cannot accept file attachments; file upload happens through the repository upload page.

## Inputs

| Input | Behavior |
| --- | --- |
| video_url | Direct public HTTPS media URL; MP4 and MKV are supported. |
| output_name | Streamtape filename; .mp4 is added if omitted. The name before .mp4 appears beneath the title logo. |
| sample_only | Unchecked by default. Checked: first 180 seconds, downloadable sample, no Streamtape upload. |
| subtitle_path | auto selects the only SRT in subtitles/, or provide an exact repository path. |
| font_path | Defaults to fonts/IskoolaPota.ttf. |
| quality | Original/1080p/720p with High (CRF 18) or Balanced (CRF 23). Original - High is default. No upscaling. |
| audio_track | auto selects default or first audio; alternatively zero-based number or language tag such as eng. Available tracks appear in logs. Missing selections fail. |
| tmdb_url | TMDB movie or TV series URL. Blank disables the timed title overlay. |
| overlay_start | Start in seconds or HH:MM:SS, default 5. |
| overlay_duration | Duration in seconds (4-60), default 8. |

## Approved overlay

Only TMDB logos tagged English (en) are eligible. Highest pixel-count PNG wins, rating breaks ties. Download uses the original endpoint, never a thumbnail or lossy WebP conversion. Alpha-capable artwork is required. Proportional Lanczos downscaling fits it to the approved composition; logos are never enlarged past native pixels. Maximum artwork quality means the best English PNG currently available on TMDB, not unlimited resolution or lossless video encoding. Missing English artwork fails before the video download; other languages are never substituted.

Centered logo fades in, filename beneath it appears in white Algerian italic and moves slightly upward, then the Sinhala message fades in. All fade out at the configured end. Word spaces are widened; line spacing follows the approved preview. No background rectangles or replacement backgrounds are applied: the original video remains visible. The small bottom-left CLIXARENA watermark remains throughout. HarfBuzz complex shaping renders the Sinhala subtitles. Names too long for the frame are reduced in size, and ASS commands in filenames are neutralized.

## Progress and results

Encoding reports percentage, elapsed time and estimated remaining minutes about every 20 seconds. Scene complexity affects estimates. Raw tool output, responses and exception strings are suppressed and never published as artifacts.

Streamtape video URLs and IDs are not printed, placed in summaries or exported. Full runs report upload_verified, rename_accepted and filename_verified separately. A verified upload with an accepted rename succeeds even when filename metadata cannot be confirmed; it emits a warning to check the filename manually, without re-uploading. No filename-verification claim is made in that case; find the named file in your Streamtape file manager. No automatic upload retries: a timeout or failed rename can leave a file on the service, so check your account before rerunning. Playback processing may still be pending after successful upload.

Sample runs publish CLIXARENA-sample for three days. Download from the completed run's Artifacts section, extract and play sample.mp4. The entire source downloads again each run. Choose a title timestamp below 180 seconds to preview it. Check Sinhala shaping and synchronization before full encoding.

## Limits and security

Dispatch inputs are retained by GitHub. Masking does not make a URL secret: use non-sensitive video URLs, or configure VIDEO_URL from a repository secret for confidential links. Public HTTPS hosts and redirects are validated, but trusted sources are still necessary because DNS validation is not a complete DNS-rebinding defense. TMDB Bearer auth does not follow redirects; Streamtape auth uses POST bodies; upload URLs go to curl over stdin. Inputs never enter shell expressions.

Hosted runners have limited time/storage. Source size cap is 12 GiB, disk reserve checks apply, job limit is 350 minutes, encode watchdog 16000 seconds, upload limit one hour. High resolution movies can still exceed runner resources. AAC audio is 192 kbps; silent sources remain silent. Streamtape can additionally process the H.264 MP4 output.

## Attribution and validation

TMDB movie images: https://developer.themoviedb.org/reference/movie-images
TV images: https://developer.themoviedb.org/reference/tv-series-images
This product uses the TMDB API but is not endorsed or certified by TMDB. Artwork is supplied by contributors. See https://developer.themoviedb.org/docs/faq for attribution and usage requirements.

Validation includes local FFmpeg rendering/encoding and mocked API, selection and privacy checks. Local rendering uses Windows FFmpeg; Ubuntu behavior and live TMDB/Streamtape authentication still need a manual sample run. No real Actions run or upload was started while preparing the ZIP.

The title logo, output name and Sinhala message have independent switches, all enabled by default. The main switch disables all three. Only the logo needs TMDB; only the name needs Algerian. Update both workflow and Vercel files.

File name and Title shown in video are separate editable fields, both filled from TMDB. TV titles support season and episode selection (including specials). Names use Series - Episode title, falling back to Series - Season-1 Episode-1 when the episode title is missing or generic. The workflow overlay_name input controls only the title on video; output_name controls the MP4 and Streamtape filename.

Preview layout editor: click title/message to edit, drag title/message/logo to reposition, or use percent coordinates. Select an item then click empty preview space to place it. Reset positions restores the original arrangement. Title changes do not change the MP4 filename. Custom text/positions pass through validated overlay_layout JSON and are burned into the final video. The browser preview is approximate; repository fonts are used for encoding.

Logo performance: the original English PNG is resized once with Lanczos to its final display size, preserving alpha without upscaling. The small PNG input starts at the configured timestamp and ends at the end of the animation; EOF passes through the full video. Original resolution, CRF, text positions and subtitles remain unchanged. Jobs already running require no modification; updated scripts affect subsequent runs only.

CRF selector defaults to 18 and supports integers 0-51. It overrides High/Balanced compression while keeping the selected resolution. Auto restores High=18/Balanced=23. CRF controls quality and size, not an encoding-speed guarantee.

Subtitle methods: Burn accepts Sinhala or English SRT, uses your selected subtitle font, and retains watermark/optional overlays. On/off creates MP4 with exactly the one uploaded subtitle track, tagged sin/Sinhala or eng/English; all original subtitle tracks are omitted. Font selection applies only to burn; on/off font appearance is controlled by the player. H.264 and selected AAC audio are copied; other codecs are converted. On/off does not add overlays or resize. A separate UTF-8 subtitle.srt is provided as a 3-day Actions artifact. Streamtape importing embedded MP4 subtitles is not verified. Subtitles already burned into source pixels cannot be removed. VTT generation/storage remains disabled.


MP4 upload without added subtitles: subtitle_method=transfer always prepares an H.264/AAC MP4 before uploading. H.264 video is stream-copied without resizing or quality loss; other video codecs are encoded at the selected CRF/preset, retaining source resolution. All source audio tracks are retained; AAC tracks are copied and other audio is converted to AAC (192 kbps stereo, 384 kbps multichannel). Supported source text subtitles are converted to mov_text; image-based/unsupported subtitles are omitted with a warning. No external subtitle, font, overlay or sample is required. The shared subtitle is not changed by this method. Output is always the chosen name plus .mp4. No live runs/uploads are performed during validation.

Burn preview editor: select Video title or Sinhala message to change its font size (8–160, relative to a 1080p canvas) and text color independently. Reset text style restores defaults. Long titles may shrink to fit. TMDB logo artwork retains its original fonts/colors. Update both the Vercel page and GitHub scripts for these controls.

Direct video URLs support public HTTPS hosts on ports 443 and 8443, including signed query strings. Credentials, fragments and other ports remain rejected; runner checks public DNS addresses on initial and redirected URLs.

Burn defaults: 720p - Balanced (never upscale), CRF Auto = 23, veryfast preset. Encoding speed offers fast/veryfast/superfast/ultrafast; ultrafast usually produces larger files at comparable quality. Selected AAC audio is copied; other audio is encoded to AAC 192 kbps. Sinhala burn, timed artwork/text and upload remain enabled. The preset is passed through overlay_layout.preset; direct Actions users may set that JSON property.

Title logos now prefer English, then no-language, then other languages; within each group select the highest-resolution PNG. No title/name matching guesses are used for mapping. Select Movie/TV (TMDB ID) or Anime (MAL ID). TV/anime require an episode. MAL English title falls back to Romaji/native; each MAL entry represents its own installment, and mapping uses season 1 by default with the selected episode. MAL has no title-logo field in this integration; its logo overlay is skipped while text remains available. Unknown anime episode counts allow entering the episode number manually.
After successful upload, mappings.json is updated on the workflow branch with {type, tmdb_id, season, episode, file_id} for Movie/TV, or {type:"anime", mal_id, season, episode, file_id} for anime. Movie season/episode are 0. Existing entries are preserved; the same identity replaces its previous file_id. Workflow contents:write permission is required; no new API secret is required for MAL or mapping writes. Protected branches can block saving; download the CLIXARENA-mapping artifact and merge it manually rather than uploading again. The mapping artifact is retained for 7 days. Sample runs do not create upload mappings. Streamtape URLs are never written; file IDs are intentionally persisted in the requested mappings file/artifact, so a public repository exposes those mappings. IDs and API responses are not printed in run logs.

Mappings now include name, fetched server-side from TMDB/MAL at dispatch, independent of edited filename/display text. Movie: official English TMDB title. TV: main series title plus episode title (season/episode fallback). Anime: MAL English, then Romaji/native title plus Episode-N. Existing mappings are not retroactively renamed; subsequent uploads update their matching entries.

Audio selection after download: the portal defaults to ask. For Burn/On-off jobs, one audio track starts automatically; multiple tracks publish safe metadata to .clixarena/audio/<job-id>.json on the workflow branch and pause before encoding. Select a language/track in the run status panel and confirm. The same runner keeps the downloaded video and resumes without downloading again. The wait is limited to 20 minutes; expiry fails without encoding/uploading. Zero-audio sources stay silent. Direct Actions users may use auto or a known index/language; ask requires a portal job_id. A known language can be chosen before download to bypass the prompt. Transfer-only preserves the original file and its audio, so it does not prompt. Jobs can be selected in the tracking dropdown; same-tab reload preserves recent job tokens. Contents write access is needed for the temporary audio state files. These files contain track metadata and selected index, never source URLs or API credentials. Runner waiting time counts toward the job time limit.

Successful video run cleanup: install .github/workflows/clixarena-cleanup.yml on the repository default branch. The completed-success workflow_run event deletes only successful manually triggered CLIXARENA video encode and upload runs. Failed/cancelled runs are retained. Deleting a run removes its logs and all attached artifacts, including sample and mapping/subtitle downloads. Repository mappings.json and Streamtape uploads remain. The cleanup workflow itself remains visible as a separate Actions entry. No existing runs are deleted merely by uploading this bundle; future completion events trigger cleanup. A safe receipt under .clixarena/completed preserves portal success status after the run is deleted. The cleanup token needs Actions write and Contents write; the built-in GitHub token supplies them, no new secret required.

Select Title logo in the preview editor to change Logo size from 25 to 200 percent (100 is the existing layout). Reset restores 100. The validated overlay_layout.logoScale applies to the preview and one-time logo preparation for the final burn. Aspect ratio is preserved; original logo pixels are never upscaled and display bounds are limited to 95 percent of frame dimensions.

Parallel video runs are enabled with a distinct concurrency group per github.run_id and cancel-in-progress false. Each hosted runner has separate download/encode temporary files. The shared subtitle path remains overwritten but each request pins the resulting commit; confirmed HTTP 409 write conflicts re-read the current SHA and retry with bounded jitter. Mapping updates re-read and merge rows after conflicts; audio state and cleanup receipt writes also retry confirmed conflicts. Uploads and uncertain dispatches are never automatically retried. Audio selection polling is every 90 seconds to reduce repository API pressure, so encoding may take up to 90 seconds to resume after confirmation; the 20-minute wait remains. GitHub Free permits 20 simultaneous standard hosted jobs across the account, with a 6-hour hosted job limit; this workflow keeps its 350-minute timeout. Standard hosted usage for public repos is free, larger runners remain billed. These are platform limits, not a guarantee of available capacity or unlimited host/API bandwidth.

Subtitle language/font choices: choose Sinhala or English before uploading SRT. UTF-8 and BOM-marked UTF-16 are normalized without changing timestamps. Burn offers Iskoola Pota from the repository, Noto Sans Sinhala, DejaVu Sans and DejaVu Serif. Upload another TTF/OTF into the repository fonts/ folder, then click Refresh repository fonts and select it. System fonts are installed on the runner and need no font upload. Custom repository fonts must be redistributable; no proprietary font is bundled. The runner checks actual font family, used Latin/Sinhala glyph coverage and Sinhala shaping tables; it stops for an incompatible font instead of silently replacing it. Modern Iskoola Pota still requires version 6+. The font selector controls subtitles; Algerian remains the timed title font. English subtitles with a Sinhala promotional message use installed Noto Sans Sinhala for that message. Manual Actions can set overlay_layout={"subtitleLanguage":"eng"} and font_path=system:dejavu-sans (Sinhala remains the backward-compatible default). Keep the existing shared subtitle path; each run pins its own revision.


Google Fonts: search the live catalog in the burn tab. Sinhala-only filtering is enabled initially; uncheck it for all families. Selected Google fonts download in Actions without a new API key. Custom TTF/OTF files up to 2 MB can be uploaded on the page, saved under unique content-based repository paths, and selected automatically. Public repository uploads are public. All selected fonts are checked for subtitle glyph coverage before rendering. Update both Vercel files and the GitHub script before using Google Fonts.


Downloads: direct public HTTPS URLs, Google Drive file share links (/file/d/ID/view, /open?id=ID, /uc?id=ID) and Dropbox file share links are supported. Drive requires Anyone with the link and download permission. Large-file GET confirmation forms and in-memory session cookies are handled automatically; folders, login/CAPTCHA, disabled downloads and quota restrictions are unsupported. Incomplete downloads, network interruptions, HTTP 408/429/5xx retry from zero at most three attempts; strict size checks, DNS/redirect validation, 12 GB cap and 2 GB disk reserve remain enabled. Uploads are never automatically retried.


Download & upload has an optional external SRT checkbox. Choose Sinhala or English, upload an SRT, and provide the TMDB/MAL identity. The normalized UTF-8 SRT is saved under subtitles/jobs/<job>/sinhala.srt and its raw GitHub URL is pinned to the upload commit. After verified Streamtape upload, the mapping includes file_id, subtitle_url, subtitle_language and subtitle_format (srt). The optional SRT is never passed to FFmpeg or Streamtape. Without the checkbox, transfer behaves as before. Public repository SRTs are public; private repositories do not provide public anonymous subtitle URLs. Concurrent jobs use separate paths; earlier video links remain pinned.


PASSWORD-LOCKED MAPPINGS: set MAPPING_PASSWORD to the same value in GitHub Actions repository secrets and Vercel environment variables. You may use the same value as APP_PASSWORD to keep one password. Mapping jobs fail before processing when this secret is absent. mappings.json and mapping-result.json backups use AES-256-GCM authenticated encryption with random salt/nonce and PBKDF2-SHA256 (600000 iterations). The portal download button uses the existing password login session to download the decrypted JSON via /api/mappings (no public plaintext endpoint or automatic player access). Current plaintext mappings migrate on the next updated successful upload. Old public Git history and previously created artifacts are not erased by this change. Wrong encryption passwords or damaged ciphertext never overwrite the existing mapping. Changing APP_PASSWORD does not rotate MAPPING_PASSWORD; retain the encryption password or decrypt/re-encrypt before changing it. External SRT URLs/files remain public as requested; this locks the mapping only.

After deployment, sign in and click Lock existing mappings.json to encrypt the current file without launching a video job. This creates an encrypted revision and keeps previous Git history. Download unlocked mappings.json retrieves the JSON only through the authenticated portal.


Anime metadata now uses the official MyAnimeList API v2. Set MAL_CLIENT_ID in Vercel environment variables (your own registered MAL app client ID); it is sent server-side only as X-MAL-CLIENT-ID. No MAL secret is needed in GitHub Actions. Anime input IDs and new mappings use mal_id; names prefer alternative_titles.en then MAL title. Each MAL anime entry has its own ID; season is stored as 1 and episode uses the MAL episode count, with manual episode entry when unknown. Legacy anilist_id mappings are preserved but never reinterpreted or converted by numeric ID; resubmit with the correct MAL ID to create a new MAL mapping. Movie/TV still use TMDB.

Burn subtitles checkbox defaults ON. Turning it OFF skips uploaded SRT and subtitle font requirements, keeps H.264/AAC encoding, resolution/quality settings, CLIXARENA watermark and enabled title overlays, and strips source subtitle tracks. Enabled text overlays still need their own fonts. Manual Actions can set overlay_layout={"subtitleEnabled":false}. Soft/on-off subtitle mode remains mandatory-SRT; transfer optional external SRT is unchanged.
