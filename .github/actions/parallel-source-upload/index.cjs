'use strict';
const fs = require('node:fs');
const path = require('node:path');

function plan(raw, workspace) {
  const indices = JSON.parse(raw);
  if (!Array.isArray(indices) || indices.length < 1 || indices.length > 4 ||
      indices.some((n, i) => !Number.isInteger(n) || n !== i)) {
    throw new Error('Invalid source chunk matrix');
  }
  const root = path.resolve(workspace, 'parallel-media');
  if (fs.lstatSync(root).isSymbolicLink()) throw new Error('Invalid media directory');
  // Validate every chunk before starting any transfer. Never upload directories,
  // symlinks, secrets, source URLs or arbitrary user-provided paths.
  return indices.map(index => {
    const file = path.join(root, `part-${String(index).padStart(2, '0')}.mkv`);
    const stat = fs.lstatSync(file);
    if (!stat.isFile() || stat.isSymbolicLink() || stat.size < 1) throw new Error('Missing source chunk');
    return {index, file, root, name: `parallel-source-${index}`};
  });
}

async function uploadAll(client, jobs, log = console.log) {
  const started = Date.now();
  log(`Uploading ${jobs.length} source chunk artifacts concurrently; compression disabled`);
  const results = await Promise.allSettled(jobs.map(async job => {
    const begin = Date.now();
    log(`Source chunk ${job.index + 1}: artifact upload starting`);
    const result = await client.uploadArtifact(job.name, [job.file], job.root,
      {compressionLevel: 0, retentionDays: 1});
    if (!Number.isSafeInteger(result.id) || result.id <= 0) throw new Error('Artifact acceptance not confirmed');
    log(`Source chunk ${job.index + 1}: artifact upload complete | elapsed ${((Date.now() - begin) / 1000).toFixed(1)}s`);
  }));
  // Let all in-flight requests settle. A partial upload must never start workers.
  if (results.some(result => result.status === 'rejected')) {
    throw new Error('One or more source artifacts could not be confirmed. Encoding workers were not started.');
  }
  log(`All source artifacts confirmed | elapsed ${((Date.now() - started) / 1000).toFixed(1)}s`);
}

async function createClient() {
  const {DefaultArtifactClient} = await import('@actions/artifact');
  return new DefaultArtifactClient();
}

async function main() {
  const jobs = plan(process.env.INPUT_MATRIX || '', process.env.GITHUB_WORKSPACE || process.cwd());
  await uploadAll(await createClient(), jobs);
}

module.exports = {plan, uploadAll, createClient};
if (require.main === module) main().catch(() => {
  // Do not emit raw SDK exception bodies, signed endpoints or runtime tokens.
  console.error('::error::Source artifact upload failed. Workers were not started; check artifact storage quota and GitHub service availability.');
  process.exitCode = 1;
});
