# Video pipeline update

Use this complete repository folder to replace the previous source files.

Removed files (delete these from an existing checkout before copying):
- .github/workflows/clixarena-telegram.yml
- scripts/clixarena_telegram_standalone.py
- tests/test_telegram.py
- tests/test_crypto_executor.py

Copying over an existing folder alone does not delete old files. Stage deletions with git add -A before committing.

The standalone title intro is now normalized to the final encoded main video's dimensions, frame rate, pixel format, track timescale and AAC sample rate/channel count. Only the short intro is encoded again; the main video is copied. Soft subtitles are offset by the actual intro duration. The joined file is checked for duration, track counts and decoding errors around the transition before it replaces the main output. Failure stops the run before upload. Both single and parallel workflows use this join function.

Validation: regression tests include different intro/main dimensions, frame rates, pixel formats and audio layouts, soft subtitle timing, silent main video, decoded frames before/after the transition, and preserving the main file on failure.
