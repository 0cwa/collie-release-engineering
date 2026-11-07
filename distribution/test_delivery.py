"""Local HTTP cache contract; does not claim real CDN behavior."""
from http.server import ThreadingHTTPServer
from pathlib import Path
import tempfile,threading,unittest,urllib.request,urllib.error,os
import serve_staging

class DeliveryTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        self.server=ThreadingHTTPServer(('127.0.0.1',0),serve_staging.handler(self.root))
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.addCleanup(self.server.server_close);self.addCleanup(self.server.shutdown)
        self.url=f'http://127.0.0.1:{self.server.server_port}/fdroid/repo/'
    def test_negative_cache_does_not_hide_new_immutable_apk(self):
        with self.assertRaises(urllib.error.HTTPError) as error:urllib.request.urlopen(self.url+'im.collie.app_1.apk')
        self.assertIn('no-store',error.exception.headers['Cache-Control'])
        (self.root/'im.collie.app_1.apk').write_bytes(b'approved')
        with urllib.request.urlopen(self.url+'im.collie.app_1.apk') as result:
            self.assertIn('immutable',result.headers['Cache-Control']);self.assertEqual(result.read(),b'approved')
    def test_same_second_entry_replacement_returns_new_bytes_with_ims(self):
        entry=self.root/'entry.jar';entry.write_bytes(b'first');os.utime(entry,(1700000000,1700000000))
        with urllib.request.urlopen(self.url+'entry.jar') as result:last_modified=result.headers['Last-Modified']
        entry.write_bytes(b'other');os.utime(entry,(1700000000,1700000000))
        request=urllib.request.Request(self.url+'entry.jar',headers={'If-Modified-Since':last_modified})
        with urllib.request.urlopen(request) as result:
            self.assertEqual(result.status,200);self.assertEqual(result.read(),b'other')
    def test_mutable_entry_revalidates_and_traversal_denied(self):
        (self.root/'entry.jar').write_bytes(b'first')
        with urllib.request.urlopen(self.url+'entry.jar') as result:self.assertIn('must-revalidate',result.headers['Cache-Control'])
        (self.root/'entry.jar').write_bytes(b'next')
        with urllib.request.urlopen(self.url+'entry.jar') as result:self.assertEqual(result.read(),b'next')
        (self.root/'private.json').write_bytes(b'private')
        for path in ['../config.yml','%2e%2e/config.yml','private.json']:
            with self.assertRaises(urllib.error.HTTPError) as error:urllib.request.urlopen(self.url+path)
            self.assertEqual(error.exception.code,404)
