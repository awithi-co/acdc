import importlib.util
from pathlib import Path
import unittest

SOURCE = Path(__file__).parents[1] / 'scripts/channel_runtime.py'

class LauncherTests(unittest.TestCase):
    def test_remote_resume_keeps_permissions_on_server(self):
        spec = importlib.util.spec_from_file_location('channel_runtime', SOURCE)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        args = module.client_command('11111111-1111-1111-1111-111111111111')
        self.assertEqual(args[:4], ['codex', 'resume', '--remote', module.ENDPOINT])
        self.assertNotIn('--yolo', args)
        self.assertNotIn('--dangerously-bypass-approvals-and-sandbox', args)
        self.assertIn('11111111-1111-1111-1111-111111111111', args)
        server = module.server_command()
        self.assertIn('mcp_servers.acdc-relay.env.ACDC_CODEX_REMOTE="'+module.ENDPOINT+'"', server)
        self.assertIn('--listen', server)
