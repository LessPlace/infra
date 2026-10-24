#!/usr/bin/env python3
"""Проверяет аварийные стеки отдельной программы внутри выбранного образа сервиса."""
import argparse
import json
import pathlib
import subprocess
import shutil
import tempfile

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('image')
parser.add_argument('--platform', default='linux/amd64')
parser.add_argument('--builder', default='swift:6.4-noble')
parser.add_argument('--output', type=pathlib.Path, required=True)
parser.add_argument('--binary-output', type=pathlib.Path, help='Сохранить пробу для проверки на настоящем сервере')
args = parser.parse_args()
args.output.mkdir(parents=True, exist_ok=True)
source = pathlib.Path(__file__).resolve().parent / 'crash-probe'
with tempfile.TemporaryDirectory(prefix='less-crash-probe-') as temporary:
    root = pathlib.Path(temporary)
    build = ['docker', 'run', '--rm', '--platform', args.platform, '--network', 'none',
             '--cpus', '2', '--memory', '2g', '-v', f'{source}:/source:ro', '-v', f'{root}:/build',
             '-w', '/source', args.builder, 'sh', '-ec',
             'swift build --build-system native --scratch-path /build -c release --jobs 2 '
             '-debug-info-format none -Xswiftc -gline-tables-only -Xcc -gline-tables-only '
             '&& objcopy --compress-debug-sections=zlib /build/release/CrashProbe']
    with (args.output / 'build.log').open('w') as log:
        subprocess.run(build, check=True, stdout=log, stderr=subprocess.STDOUT)
    if args.binary_output:
        args.binary_output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(root / "release/CrashProbe", args.binary_output)
    results = []
    for mode, expected in [('sync', 'synchronousCrash'), ('async', 'asynchronousCrash'),
                           ('inline', 'inlinedCrashLeaf'), ('fatal', 'CrashProbe.main')]:
        name = f'less-crash-probe-{root.name}-{mode}'
        command = ['docker', 'run', '--rm', '--name', name, '--platform', args.platform,
                   '--network', 'none', '--read-only', '--no-healthcheck', '--cpus', '1',
                   '--memory', '512m', '--ulimit', 'core=0', '--user', '10001:10001',
                   '-v', f'{root}:/probe:ro', '-e',
                   'SWIFT_BACKTRACE=enable=yes,interactive=no,color=no,symbolicate=full,output-to=stderr',
                   '--entrypoint', '/probe/release/CrashProbe', args.image, mode]
        # tempfile defaults to 0700; the runtime user must traverse the mounted directory.
        root.chmod(0o755)
        try:
            result = subprocess.run(command, text=True, capture_output=True, timeout=25)
            output = result.stdout + result.stderr
            code = result.returncode
        except subprocess.TimeoutExpired:
            output = subprocess.check_output(['docker', 'logs', name], text=True, stderr=subprocess.STDOUT)
            subprocess.run(['docker', 'rm', '-f', name], check=True, stdout=subprocess.DEVNULL)
            code = 'timeout'
        (args.output / f'{mode}.log').write_text(output)
        passed = isinstance(code, int) and code not in (0, 125, 126, 127) and expected in output
        passed = passed and 'CrashProbe.swift:' in output
        results.append({'mode': mode, 'exit': code, 'source_and_function': passed})
    (args.output / 'results.json').write_text(json.dumps(results, indent=2) + '\n')
    print(json.dumps(results))
    if not all(item['source_and_function'] for item in results):
        raise SystemExit('Проверка аварийных стеков не прошла; образ выпускать нельзя.')
