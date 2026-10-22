# GitHub-only production parallel encoding

Release: 2026.10.19-audio-wait-10m

The normal workflow now has three paths:

- Burn: download and validate once, select audio, prepare approved fonts, subtitle ASS and optional title artwork once, then encode up to four keyframe-aligned video chunks on independent Ubuntu runners. The selected resolution, CRF, preset, watermark, subtitle toggle and title animation settings are preserved. Each chunk uses its original global timeline for subtitles and animation, then resets its output timestamps before concatenation.
- On/off: auto standard canvas, CRF18/veryfast defaults, selected audio, one switchable uploaded subtitle, no watermark/title overlay. Four independent video workers.
- MP4 transfer: H.264 copies directly into MP4 on one runner; it does not require video encoding. Other source video codecs convert to H.264 on four runners at original dimensions. All source audio tracks are retained as AAC (existing AAC copied); supported source text subtitles are converted to mov_text, while image subtitles are omitted. An optional uploaded external SRT remains external and is saved in mappings, not embedded by transfer.

The prepare job retains the workflow run ID during audio, torrent-file and fresh-URL requests. Workers receive only their source video chunk, a safe manifest and, for burn, prepared render assets. Host credentials are not sent to worker jobs. Font files are used as run artifacts from the existing repository; this bundle contains no proprietary fonts.

The final job joins the video without encoding again, restores audio/subtitle tracks, verifies frame packet count, duration within two seconds, dimensions, H.264/AAC and subtitle counts/language as appropriate, then performs the configured host uploads in order. Failed/cancelled workers prevent finalization. Existing success cleanup checks whole-workflow success and does not delete failed runs. Uploads are not retried automatically.

Short inputs with sparse keyframes can yield fewer than four safe chunks. Source and encoded chunks use Actions artifact storage with one-day intermediate retention. Scheduling and transfers add overhead; four workers do not guarantee fourfold end-to-end speed. Account artifact storage and runner disk limits still apply.

Deploy the workflow and scripts to the existing processing repository. Deploy the complete included control project separately. Existing secrets, mappings, fonts and subtitles are retained; none are shipped in this update. No new secret is required. The portal has three methods, and all processing uses GitHub.

Local integration checks cover simultaneous encodes, frame/duration preservation, selected audio timing, one switchable subtitle, removal of replaced subtitles, burn captions and animated logo across chunk boundaries, no-audio input, short input, missing-chunk rejection, and transfer conversion retaining multiple audio languages/source text subtitles. No actual GitHub run, host upload or deployment has been started.
