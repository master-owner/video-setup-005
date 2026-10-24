# Video pipeline update

Use this complete repository folder to replace the previous source files.

Removed files (delete these from an existing checkout before copying):
- .github/workflows/clixarena-telegram.yml
- scripts/clixarena_telegram_standalone.py
- tests/test_telegram.py
- tests/test_crypto_executor.py

Copying over an existing folder alone does not delete old files. Stage deletions with git add -A before committing.

The standalone title intro is now normalized to the final encoded main video's dimensions, frame rate, pixel format, track timescale and AAC sample rate/channel count. The normalized intro and main are encoded together through one H.264 encoder (CRF 18, veryfast) so playback does not require switching decoder configurations. Soft subtitles are offset by the actual intro duration. The joined file is checked for duration, track counts and decoding errors around the transition before it replaces the main output. Failure stops the run before upload. Both single and parallel workflows use this join function.

Validation: regression tests include different intro/main dimensions, frame rates, pixel formats and audio layouts, soft subtitle timing, silent main video, decoded frames before/after the transition, and preserving the main file on failure.

## Intro failure diagnostics update
The merge log now reports intro normalization, MP4 joining and transition decoding separately, with bounded local FFmpeg error details (URLs redacted). Available disk space is checked before writing another full MP4. Intro encoding uses the final video's nominal frame rate, with a validated fallback, rather than a chunk-derived average.

Use a new workflow run after committing these files. Re-running a run pinned to an older source_commit still uses its older code.

A generic failure after 'Four-runner MP4 validation passed' alone cannot establish the underlying cause; this update exposes the failing intro stage and its diagnostic. The missing mapping-result.json notice is expected when upload/mapping creation has not been reached.

After final MP4 validation, the disposable parallel-media/joined.mp4 intermediate is removed before the intro join to release disk space. Final validation: 137 Python tests and 3 artifact uploader tests passed; the 6 intro tests also passed after this cleanup change.

## Recover a failed run using saved encoded chunks

1. Commit/push this updated scripts/ folder and .github/workflows/clixarena-recover.yml to the default branch.
2. Open GitHub Actions → CLIXARENA recover merge and upload → Run workflow.
3. Enter source_run_id: the long number after /actions/runs/ in the failed run's URL (not its short run number).
4. Leave overlay_layout and output_name blank to restore the original values from source logs. Run the workflow on the branch containing this updated code.

The recovery reads the failed run's logs to restore its original upload host, filename, sample flag, subtitle path and mapping identity. It downloads only parallel-encoded-*, parallel-companion and parallel-manifest artifacts from that run. It then runs the current merge/intro/upload code. It never downloads the source video or launches encoding workers. The FINAL combined intro/main is encoded again for playback compatibility; the original source download and four encoding workers are not repeated. The retained intro audio is regenerated as Sinhala.

Recovery stops if artifacts have expired, original settings cannot be recovered, the source run is successful or from another repository, or the source logs already show upload activity. If the original layout is absent from logs, provide its original JSON explicitly. A retained run does not extend the artifacts' configured retention period. Do not launch recovery again after a successful upload.

## Playback and Sinhala speech repair
Prior stream-copy joins could retain different H.264 parameter sets across the intro boundary. The joined timeline now passes through ONE H.264 encoder; validation decodes continuously from the first frame across the transition, without seeking. This is a local compatibility repair, not proof of Ok.ru playback, since the reported hosted file has not been inspected.

Automatic use of fonts/intro-voice.mp3 or intro-voice.mp3 is disabled; those files can contain English. Custom recordings are used only when INTRO_USE_CUSTOM_VOICE=true is explicitly set. English-only message text is replaced with a fixed Sinhala message. The speech engines request the Sinhala si-LK-SameeraNeural voice, or espeak-ng's si voice. If Sinhala speech fails, the run stops rather than silently substituting music. Recovery replaces the retained intro's old audio.

For the already uploaded broken video, run the recovery workflow using the ORIGINAL failed encoding run ID again (not the successful recovery run). The original encoded artifacts must still be available. It uploads a repaired copy and updates the mapping; the old remote upload is not deleted. Normal new jobs also use the repaired join. Docker users must rebuild the runner image for the added speech dependencies.

Playback/speech repair validation: 148 Python tests passed, including one SPS configuration across the combined stream, continuous transition decode, Sinhala voice engine/text selection, and replacing retained audio without changing retained intro video. Network TTS speech and actual hosted Ok.ru playback were not tested locally.
