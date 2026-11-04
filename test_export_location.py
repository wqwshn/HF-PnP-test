import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.request import Request, urlopen
from urllib.error import HTTPError
import app


class ExportLocationTests(unittest.TestCase):
    def test_native_export_download_and_upload_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp)
            source=base/'experiment'
            source.mkdir()
            raw=source/'record.csv'
            marker=source/'record_markers.csv'
            raw.write_text('Time(s),SampleIndex,Ut1(mV),Ut2(mV)\n0,0,1000,2000\n1,1,1100,2100\n2,2,1200,2200\n',encoding='utf-8')
            marker.write_text('SampleIndex,MarkerNo,Note-distance(mm),Note-force(N)\n1,1,1,0\n2,2,-.5,1\n',encoding='utf-8')
            before=(raw.read_bytes(),marker.read_bytes())
            server=app.ThreadingHTTPServer(('127.0.0.1',0),app.Handler)
            thread=threading.Thread(target=server.serve_forever,daemon=True)
            thread.start()
            address=f'http://127.0.0.1:{server.server_port}'
            def post(endpoint,payload):
                with urlopen(Request(address+endpoint,json.dumps(payload).encode(),{'Content-Type':'application/json'})) as response:
                    return json.load(response)
            try:
                with patch.object(app,'ROOT',base/'tool'),patch.object(app,'choose_file',return_value=str(raw)):
                    native=post('/api/choose',{})
                    self.assertEqual(Path(native['export_root']),source.resolve())
                    result=post('/api/export',{'session':native['session'],'config':{}})['export']
                    self.assertEqual(Path(result['folder']).parent,source.resolve())
                    self.assertEqual(len(result['files']),9)
                    for file in result['files']:
                        with urlopen(address+file['url']) as response:
                            self.assertEqual(response.read(),(Path(result['folder'])/file['name']).read_bytes())
                    with self.assertRaises(HTTPError) as error:
                        urlopen(address+'/download/record.csv')
                    self.assertEqual(error.exception.code,404)
                    uploaded=post('/api/upload',{'raw':raw.read_text(),'marker':marker.read_text(),
                                                'raw_name':str(raw),'marker_name':str(marker)})
                    fallback=post('/api/export',{'session':uploaded['session'],'config':{}})['export']
                    self.assertEqual(Path(fallback['folder']).parent,base/'tool'/'exports')
                    self.assertEqual(before,(raw.read_bytes(),marker.read_bytes()))
            finally:
                server.shutdown()
                server.server_close()

if __name__=='__main__':
    unittest.main()
