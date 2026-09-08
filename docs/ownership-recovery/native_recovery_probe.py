"""Bounded provider-free reproduction, reusing the maintained loopback peer.

Only the synthetic response stop reason changes. Production adapter/native bytes
are unmodified. Native subprocesses have empty homes, fake credentials, dead
proxies and an OS network policy permitting localhost only.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
from http.server import ThreadingHTTPServer

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--hermes-checkout', type=Path, required=True)
parser.add_argument('--binary', type=Path, action='append', required=True)
args = parser.parse_args()
ROOT = args.hermes_checkout.resolve()
PIN = 'eafb4186a4e8be7ac0ca94d69b10c57e0154aee8'
if subprocess.check_output(['git', '-C', str(ROOT), 'rev-parse', 'HEAD'], text=True).strip() != PIN:
    parser.error('Hermes checkout must match the documented inspected commit')
if not Path('/usr/bin/sandbox-exec').is_file():
    parser.error('macOS sandbox-exec is required; do not run without the network sandbox')
POLICY = '(version 1)(allow default)(deny network-outbound)(allow network-outbound (remote ip "localhost:*"))'


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


fixture = load('maintained_fixture', ROOT / 'evals/directsdk_cache_wire.py')
native = load('inspected_directsdk', ROOT / 'plugins/model-providers/claude-oauth-directsdk/directsdk.py')


class Peer(fixture.Peer):
    def do_POST(self):
        original = self.wfile
        peer = self

        class Writer:
            def write(self, data):
                if data.startswith(b'event: message_delta\n'):
                    event = json.loads(data.split(b'\ndata: ', 1)[1])
                    if peer.server.mode == 'always_limit' or (
                        peer.server.mode == 'once_limit' and len(peer.server.wires) == 1
                    ):
                        event['delta']['stop_reason'] = 'max_tokens'
                    data = ('event: message_delta\ndata: ' + json.dumps(event) + '\n\n').encode()
                return original.write(data)

            def flush(self):
                return original.flush()

        self.wfile = Writer()
        try:
            super().do_POST()
        finally:
            self.wfile = original


def run(binary, mode):
    with tempfile.TemporaryDirectory(prefix='hermes-native-feasibility-') as tmp:
        with ThreadingHTTPServer(('127.0.0.1', 0), Peer) as peer:
            peer.wires, peer.tools, peer.mode = [], False, mode
            worker = threading.Thread(target=peer.serve_forever, daemon=True)
            worker.start()
            env = {'PATH': os.defpath, 'HOME': tmp, 'HERMES_HOME': tmp,
                   'CLAUDE_CONFIG_DIR': str(Path(tmp) / 'config'), 'XDG_CONFIG_HOME': tmp,
                   'ANTHROPIC_API_KEY': 'sk-ant-public-offline-fixture',
                   'ANTHROPIC_BASE_URL': f'http://127.0.0.1:{peer.server_port}',
                   'CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC': '1',
                   'DISABLE_TELEMETRY': '1', 'DISABLE_ERROR_REPORTING': '1',
                   'NO_PROXY': '127.0.0.1,localhost', 'no_proxy': '127.0.0.1,localhost'}
            env.update({key: 'http://127.0.0.1:1' for key in
                        ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'http_proxy', 'https_proxy', 'all_proxy')})
            command = ['/usr/bin/sandbox-exec', '-p', POLICY, str(binary)]
            client = native.Client(command=command, env=env, timeout=25)
            result, error, version = None, None, 'unavailable'
            try:
                version = subprocess.check_output(command + ['--version'], env=env, text=True, timeout=10).strip()
                response = client.create(model='claude-sonnet-5', messages=[
                    {'role': 'system', 'content': 'Public synthetic fixture only.'},
                    {'role': 'user', 'content': 'Return the public fixture text.'}], tools=[])
                result = {'finish_reason': response.choices[0].finish_reason,
                          'native_assistant_count': len(response.choices[0].message.reasoning_details[0]['messages'])}
            except Exception as exc:
                error = type(exc).__name__ + ': ' + str(exc)
            finally:
                client.close()
                peer.shutdown()
                worker.join(timeout=2)
            return {'native_version': version, 'binary_sha256': hashlib.sha256(binary.read_bytes()).hexdigest(),
                    'mode': mode, 'localhost_messages_requests': len(peer.wires),
                    'result': result, 'error': error, 'provider_calls': 0,
                    'controls': ['max-turns=1', 'CLAUDE_CODE_MAX_RETRIES=0', 'native tools disabled',
                                 'autocompaction disabled', 'localhost-only OS outbound policy'],
                    'remaining_client_requests': len(client._requests), 'peer_stopped': not worker.is_alive()}


if __name__ == '__main__':
    rows = [run(binary.resolve(), mode) for binary in args.binary
            for mode in ('normal', 'once_limit', 'always_limit')]
    print(json.dumps({
        'claim_class': 'advisory', 'directsdk_sha': PIN,
        'probe_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'runs': rows,
        'proof_boundary': 'Local synthetic reproduction, not subscription qualification.'
    }, indent=2))
