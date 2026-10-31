import importlib.util
from pathlib import Path
import unittest

SOURCE = Path(__file__).parents[1] / 'scripts/channel_runtime.py'

def load():
    spec = importlib.util.spec_from_file_location('channel_runtime', SOURCE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

class LauncherTests(unittest.TestCase):
    def test_remote_resume_keeps_permissions_on_server(self):
        module = load()
        args = module.client_command('11111111-1111-1111-1111-111111111111', '/work/repo')
        self.assertEqual(args[:6], ['codex', 'resume', '--remote', module.ENDPOINT, '-C', '/work/repo'])
        self.assertNotIn('--dangerously-bypass-approvals-and-sandbox', args)
        self.assertIn('11111111-1111-1111-1111-111111111111', args)
        server = module.server_command()
        self.assertIn('mcp_servers.acdc-relay.env.ACDC_CODEX_REMOTE="'+module.ENDPOINT+'"', server)
        self.assertIn('--listen', server)
        self.assertFalse(any('approval_policy' in arg or 'sandbox_mode' in arg for arg in server))

    def test_yolo_is_a_client_option(self):
        module = load()
        args = module.client_command(None, '/work/repo', yolo=True)
        self.assertEqual(args[:5], ['codex', '--remote', module.ENDPOINT, '-C', '/work/repo'])
        self.assertIn('--dangerously-bypass-approvals-and-sandbox', args)
        self.assertEqual(args[-1], module.PROMPT)

    def test_resume_never_overrides_permissions(self):
        module = load()
        args = module.client_command('11111111-1111-1111-1111-111111111111', '/work/repo', yolo=True)
        self.assertNotIn('--dangerously-bypass-approvals-and-sandbox', args)
