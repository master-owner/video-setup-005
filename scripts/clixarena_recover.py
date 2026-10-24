"""Reuse completed encode artifacts with current merge code; never download source or encode chunks."""
import json
import os
from pathlib import Path
import re
import subprocess
import sys

from clixarena_hosts import selected_hosts
from clixarena_video import output_filename

CONTEXT = Path('recovery-context.json')
ALLOWED = {'clixarena-video.yml', 'clixarena-video-docker.yml', 'clixarena-video-legacy.yml'}


def gh(*args):
    result = subprocess.run(['gh', *args], capture_output=True, text=True, timeout=180)
    if result.returncode:
        raise ValueError('Cannot read the source run or its logs. Check the run ID, artifact expiry and Actions access.')
    return result.stdout


def extract_context(logs):
    found = {}
    for key in ('OVERLAY_LAYOUT', 'OUTPUT_NAME', 'SAMPLE_ONLY', 'SUBTITLE_PATH', 'PORTAL_JOB_ID'):
        values = set()
        for line in logs.splitlines():
            match = re.search(r'\b' + key + r':\s*(.*)$', line)
            if match:
                values.add(match[1].strip())
        if len(values) > 1:
            raise ValueError('Source logs contain conflicting recovery settings: ' + key)
        if values:
            found[key] = values.pop()
    return found


def prepare():
    source = os.environ.get('SOURCE_RUN_ID', '').strip()
    repo = os.environ.get('GITHUB_REPOSITORY', '')
    if not re.fullmatch(r'[1-9][0-9]*', source):
        raise ValueError('Enter the numeric source run ID from /actions/runs/NUMBER, not the run number.')
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repo):
        raise ValueError('Invalid repository')
    run = json.loads(gh('api', f'repos/{repo}/actions/runs/{source}'))
    path = run.get('path', '').split('@')[0].rsplit('/', 1)[-1]
    if (run.get('status') != 'completed' or run.get('conclusion') not in ('failure', 'cancelled', 'timed_out')
            or run.get('event') != 'workflow_dispatch' or path not in ALLOWED
            or (run.get('head_repository') or {}).get('full_name', '').lower() != repo.lower()):
        raise ValueError('Source must be a completed failed/cancelled video run in this repository.')
    pages = json.loads(gh('api', '--paginate', '--slurp', f'repos/{repo}/actions/runs/{source}/artifacts?per_page=100'))
    artifacts = {a['name']: a for page in pages for a in page.get('artifacts', []) if not a.get('expired')}
    if not {'parallel-manifest', 'parallel-companion'} <= artifacts.keys() or not any(n.startswith('parallel-encoded-') for n in artifacts):
        raise ValueError('Required encoded, companion or manifest artifacts are missing/expired. Keeping a run does not prevent artifact expiry.')
    logs = gh('run', 'view', source, '--repo', repo, '--log')
    # This recovery is for failures before host uploads. Never silently repeat an accepted upload.
    upload_markers = (' upload starting:', ' upload complete.', 'Ok.ru upload server validated',
                      'Requesting Ok.ru video upload URL', 'Uploading video to ', 'Streamtape upload')
    if any(marker.lower() in logs.lower() for marker in upload_markers):
        raise ValueError('Source logs show host upload activity. Check the host before attempting recovery to avoid duplicate uploads.')
    context = extract_context(logs)
    override = os.environ.get('RECOVERY_LAYOUT', '').strip()
    if override:
        context['OVERLAY_LAYOUT'] = override
    if 'OVERLAY_LAYOUT' not in context:
        raise ValueError('Original layout was not found in logs. Supply the original overlay_layout JSON in the recovery input.')
    layout = json.loads(context['OVERLAY_LAYOUT'] or '{}')
    if not isinstance(layout, dict):
        raise ValueError('Recovery overlay_layout must be a JSON object')
    selected_hosts(layout)
    context['OUTPUT_NAME'] = output_filename(os.environ.get('RECOVERY_OUTPUT_NAME', '').strip() or context.get('OUTPUT_NAME', 'CLIXARENA.mp4'))
    if context.get('SAMPLE_ONLY') not in ('true', 'false'):
        raise ValueError('Original sample/upload setting could not be recovered; no upload will start.')
    context['SOURCE_RUN_ID'] = source
    CONTEXT.write_text(json.dumps(context), encoding='utf-8')
    print('Source artifacts available; original host, output and mapping settings restored. No source download or chunk encoding.', flush=True)


def merge():
    context = json.loads(CONTEXT.read_text(encoding='utf-8'))
    from clixarena_distributed import ROOT, manifest
    data = manifest()
    count = data.get('count')
    if type(count) is not int or not 1 <= count <= 4:
        raise ValueError('Invalid recovered chunk count')
    for i in range(count):
        if not (ROOT / f'encoded-{i:02d}.mp4').is_file():
            raise ValueError('Missing recovered encoded chunk ' + str(i))
    if data.get('render', {}).get('skip_burn_intro') and not (ROOT/'standalone-intro.mp4').is_file():
        raise ValueError('Standalone intro artifact is missing; recovery stopped before upload.')
    env = dict(os.environ)
    env.update({k: v for k, v in context.items() if k != 'SOURCE_RUN_ID'})
    print('Recovering merge, intro and upload only; using current checkout code.', flush=True)
    return subprocess.call([sys.executable, '-u', str(Path(__file__).with_name('clixarena_distributed.py')), 'merge'], env=env)


if __name__ == '__main__':
    try:
        if sys.argv[1:] == ['prepare']:
            prepare()
        elif sys.argv[1:] == ['merge']:
            raise SystemExit(merge())
        else:
            raise ValueError('Use prepare or merge')
    except Exception as error:
        # Do not dump log text, settings, credentials or remote error bodies.
        if isinstance(error, ValueError) and not isinstance(error, json.JSONDecodeError):
            print('::error::' + str(error).replace('\n', ' ')[:500], flush=True)
        else:
            print('::error::Recovery preparation failed (' + type(error).__name__ + '); no automatic upload retry.', flush=True)
        raise SystemExit(1)
