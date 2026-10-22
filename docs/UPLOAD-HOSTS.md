# Upload destination: Streamtape or Abyss.to

The main UI now has Upload destination above the video URL. Select one host per job. Streamtape remains the default. The selected host is carried inside the validated overlay layout and signed tracking token, independently of GitHub vs HF execution and burn/soft/transfer method. Sample-only does not upload to either host.

## Deploy and secret
Update vercel-ui/ and redeploy, update github-actions/ workflow plus scripts in the existing Actions repository, and update huggingface-space/ in the separate HF Space. Do not rebuild HF during active jobs.
Add ABYSS_API_KEY as a GitHub Actions repository secret AND an HF Space secret wherever you intend to upload to Abyss. Copy the API key from the Abyss dashboard Document/Profile page. Do not put it in the Vercel UI or source code. No additional Vercel environment variable is required. Existing Streamtape secrets stay unchanged; an Abyss job does not require them and a Streamtape job does not require the Abyss key. The HF startup no longer demands either specific host's credentials; the selected job checks its own credentials before download/encoding.

Abyss's public dashboard API documentation defines multipart POST up.abyss.to/:key with field file, response {"slug":"file-id"}, and GET https://api.abyss.to/v1/files/:id?key=... for metadata. This implementation uses HTTPS only (verified handshake), passes the secret upload URL through curl stdin rather than argv, does not follow redirects, and never logs responses/keys/IDs/playback URLs. It uses the requested filename as the quoted multipart filename. Metadata is polled read-only after upload; transcoding readiness is not required. Upload is never retried automatically, because a lost response can still mean a completed upload.
Reference: https://dash.abyss.to/panel/document . Public dashboard asset inspected: https://dash.abyss.to/assets/index-CbjADjGL.js . No account or video upload was used for that inspection.

## Encrypted mapping schema
Mappings remain AES-GCM encrypted with the existing MAPPING_PASSWORD. Provider choice does not change the encryption format. One title/episode row can retain both host IDs:

```json
{
  "type": "movie", "tmdb_id": 123, "season": 0, "episode": 0,
  "provider": "abyss",
  "file_id": "STREAMTAPE_ID",
  "abyss_id": "ABYSS_ID",
  "providers": {
    "streamtape": {"file_id": "STREAMTAPE_ID"},
    "abyss": {"file_id": "ABYSS_ID"}
  }
}
```
`provider` records the most recently uploaded host. `file_id` remains the legacy Streamtape ID and is omitted on Abyss-only rows; `abyss_id` is the Abyss slug. `providers` is the explicit per-host map. Uploading the same title/episode to another host preserves the previous provider; uploading again to the same host replaces only that host's ID. Existing legacy rows migrate on the next matching update. Title name, TMDB/MAL identity and new external subtitle metadata are included as before. Mapping requires a title identity, as before.

The Streamtape deletion sync removes a confirmed missing Streamtape reference while preserving an Abyss entry for the same title. It does not query/delete Abyss files or delete valid Abyss mappings because a Streamtape API call failed. Automatic Abyss deletion monitoring is not included. Consumers such as the separate HF tape-extractor/site must be updated to read providers/abyss_id if they should play Abyss; they are not modified by this video-setup bundle.

If mapping save fails after an accepted Abyss upload, a warning is shown, the job ends as failed to preserve recovery information, and no upload retry is performed. GitHub keeps its encrypted mapping-result.json artifact; HF keeps an encrypted receipt under SPACE_DATA_DIR/mapping-recovery/ while its disk survives. Inspect the host manager and fix repository access before repeating the upload. Provider IDs and URLs are not printed in job logs.

## Validation
Mock tests cover host allowlisting, credential separation, slug validation, stdin-only upload secrets, no repeated uploads, multi-host mapping preservation and Streamtape removal preserving Abyss. Existing tests and syntax checks pass. The public upload host's HTTPS support was checked without credentials. A live authenticated Abyss upload was NOT performed; do the first deployment test with a small file that you authorize uploading. No encoding, upload or deployment was triggered automatically.
