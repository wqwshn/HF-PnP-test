import base64
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.request import Request, urlopen
from urllib.error import HTTPError
import app

RAW = 'Time(s),SampleIndex,Ut1(mV),Ut2(mV)\n0,0,1000,2000\n1,1,1100,2100\n'
MARKERS = 'SampleIndex,MarkerNo,Note-distance(mm),Note-force(N）\n1,1,-0.1,0.5\n'


class EncodingTests(unittest.TestCase):
    def request(self, endpoint, payload, raw_path):
        server = app.ThreadingHTTPServer(('127.0.0.1', 0), app.Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with patch.object(app, 'choose_file', return_value=str(raw_path)):
                request = Request(f'http://127.0.0.1:{server.server_port}{endpoint}',
                                  json.dumps(payload).encode(), {'Content-Type': 'application/json'})
                try:
                    response = urlopen(request)
                except HTTPError as error:
                    self.fail(error.read().decode())
                with response:
                    result = json.load(response)
            self.assertEqual(result['markers'][0]['force'], .5)
            self.assertEqual(result['markers'][0]['distance'], -.1)
            return result
        finally:
            server.shutdown()
            server.server_close()

    def test_native_csv_encodings(self):
        with tempfile.TemporaryDirectory() as folder:
            raw = Path(folder)/'sample.csv'
            marker = Path(folder)/'sample_markers.csv'
            for encoding in ('gbk', 'utf-8-sig', 'utf-16'):
                with self.subTest(encoding=encoding):
                    raw.write_bytes(RAW.encode('utf-8'))
                    marker.write_bytes(MARKERS.encode(encoding))
                    self.request('/api/choose', {}, raw)

    def test_browser_bytes_match_native_identity(self):
        with tempfile.TemporaryDirectory() as folder:
            raw = Path(folder)/'sample.csv'
            raw.write_bytes(RAW.encode())
            raw.with_name('sample_markers.csv').write_bytes(MARKERS.encode('gbk'))
            native = self.request('/api/choose', {}, raw)
            uploaded = self.request('/api/upload', {
                'raw_base64': base64.b64encode(RAW.encode()).decode(),
                'marker_base64': base64.b64encode(MARKERS.encode('gbk')).decode(),
                'raw_name': raw.name, 'marker_name': 'sample_markers.csv'}, raw)
            self.assertEqual(native['marker_sha256'], uploaded['marker_sha256'])

if __name__ == '__main__':
    unittest.main()
