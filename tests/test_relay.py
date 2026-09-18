"""Stateless relay: real Unix sockets, bounded delivery tasks, no mailbox."""
import asyncio
import gc
import importlib.util
from pathlib import Path
import tempfile
import tracemalloc
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).parents[1] / 'plugins/claude/mcp/relay.py'

class RelayTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.assertTrue(SOURCE.exists(), 'Stateless relay is not implemented')
        spec = importlib.util.spec_from_file_location('relay', SOURCE)
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        self.Relay = module.Relay
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'relay'
        async def deliver(message):
            # The receiving chat boundary responds; the relay must not retain messages.
            return {'status': 'transport_written', 'echo': message['text'], 'sender': message['sender']['agent']}
        self.a = self.Relay(self.root, 'codex', '/a', deliver)
        self.b = self.Relay(self.root, 'claude', '/b', deliver)
        await self.a.start(); await self.b.start()
        self.addAsyncCleanup(self.a.close); self.addAsyncCleanup(self.b.close)

    async def test_discovery_and_direct_delivery(self):
        self.b.name = 'implementer'
        peers = await self.a.peers()
        self.assertIn(self.b.id, [p['id'] for p in peers])
        result = await self.a.send(self.b.id, 'Review grain')
        self.assertEqual(result, {'status':'transport_written','echo':'Review grain','sender':'codex'})
        self.assertEqual((await self.b.send(self.a.id, 'Reviewed'))['sender'], 'claude')
        self.assertTrue(all(p.suffix == '.sock' for p in self.root.iterdir()))

    async def test_discovery_survives_a_peer_disappearing_during_scan(self):
        original = Path.lstat
        def disappearing(path, *args, **kwargs):
            if path == self.b.path:
                raise FileNotFoundError(path)
            return original(path, *args, **kwargs)
        with patch.object(Path, 'lstat', disappearing):
            peers = await self.a.peers()
        self.assertIn(self.a.id, [peer['id'] for peer in peers])

    async def test_offline_peer_fails_and_close_cleans_up(self):
        await self.b.close()
        with self.assertRaises(ConnectionError): await self.a.send(self.b.id, 'hello')
        self.assertNotIn(self.b.id, [p['id'] for p in await self.a.peers()])
        self.assertFalse(self.b.path.exists())

    async def test_input_limits_and_private_socket(self):
        for to, text in [('../bad','text'), (self.b.id,''), (self.b.id,'x'*17000)]:
            with self.assertRaises(ValueError): await self.a.send(to,text)
        self.assertEqual(self.root.stat().st_mode & 0o777, 0o700)
        self.assertEqual(self.b.path.stat().st_mode & 0o777, 0o600)

    async def test_backpressure_rejects_excess_delivery_without_a_waiting_queue(self):
        gate = asyncio.Event()
        async def slow(message): await gate.wait(); return {'status':'transport_written'}
        self.b.deliver = slow
        tasks = [asyncio.create_task(self.a.send(self.b.id,str(i))) for i in range(8)]
        try:
            for _ in range(100):
                if len(self.b.connections) == 8: break
                await asyncio.sleep(.01)
            self.assertEqual(len(self.b.connections),8)
            with self.assertRaisesRegex(ValueError,'busy'): await self.a.send(self.b.id,'overflow')
        finally:
            gate.set(); await asyncio.gather(*tasks)
        await asyncio.sleep(.02)
        self.assertEqual(len(self.b.connections),0)

    async def test_repeated_messages_do_not_accumulate_in_memory(self):
        self._asyncioTestLoop = asyncio.get_running_loop()
        self._asyncioTestLoop.set_debug(False)
        tracemalloc.start()
        try:
            for _ in range(200): await self.a.send(self.b.id,'warmup')
            await asyncio.sleep(0);gc.collect();before=tracemalloc.get_traced_memory()[0]
            for _ in range(2000): await self.a.send(self.b.id,'x'*1024)
            await asyncio.sleep(0);gc.collect();after=tracemalloc.get_traced_memory()[0]
            self.assertLess(after-before,512*1024, f'Retained allocations grew by {after-before} bytes')
            self.assertEqual(len(self.b.connections),0)
        finally: tracemalloc.stop()

if __name__ == '__main__': unittest.main()
