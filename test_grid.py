import unittest
import tempfile
import json
import csv
from pathlib import Path
from unittest.mock import patch
import app
from grid_points import build_grid, DISTANCES


class GridTests(unittest.TestCase):
    def test_grid_interpolation_edits_and_invalidation(self):
        points=[{'distance':x,'mean':2+x/10} for x in [2,1,0,-1]]
        rows,basis,_=build_grid(points,'Ut1')
        self.assertEqual(len(rows),26)
        self.assertEqual(DISTANCES.count(1),1)
        self.assertEqual(DISTANCES[-1],-1)
        for r in rows:self.assertAlmostEqual(r['voltage_V'],2+r['distance_mm']/10)
        changed,_,edits=build_grid(points,'Ut1',{'0.0':2.5},basis)
        self.assertEqual(changed[15]['voltage_V'],2.5)
        self.assertEqual(changed[15]['interpolated_voltage_V'],2)
        self.assertTrue(changed[15]['edited'])
        self.assertFalse(build_grid(points,'Ut2',edits,basis)[2])
        self.assertFalse(build_grid([{'distance':x['distance']+.1,'mean':x['mean']} for x in points],'Ut1',edits,basis)[2])
        up=build_grid(points[::-1],'Ut1')[0]
        for a,b in zip(rows,up):self.assertAlmostEqual(a['voltage_V'],b['voltage_V'])

    def test_uncovered_and_ambiguous_points(self):
        rows,_,_=build_grid([{'distance':1.99,'mean':2},{'distance':-1,'mean':1}],'Ut1')
        self.assertIsNone(rows[0]['voltage_V'])
        rows,_,_=build_grid([{'distance':2,'mean':1},{'distance':0,'mean':2},{'distance':2,'mean':3}],'Ut1')
        self.assertEqual(rows[5]['status'],'ambiguous')

    def test_recent_directory_survives_and_falls_back(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(app,'ROOT',Path(tmp)):
            source=Path(tmp)/'records';source.mkdir()
            app.remember_directory(source/'test.csv')
            self.assertEqual(app.recent_directory(),source)
            (Path(tmp)/'.ui_preferences.json').write_text('{}')
            self.assertEqual(app.recent_directory(),Path(tmp)/'data')

    def test_export_contains_edited_table_and_settings(self):
        raw='Time(s),SampleIndex,Ut1(mV),Ut2(mV)\n'+''.join(f'{i/100},{i},{1400+i/10},{1500+i/10}\n' for i in range(401))
        markers='SampleIndex,MarkerNo,Note-distance(mm),Note-force(N)\n100,1,2,0\n200,2,1,0\n300,3,0,1\n400,4,-1,2\n'
        ds=app.parse_dataset(raw,markers,'demo.csv','demo_markers.csv')
        _,points,_=app.summarize(ds,{})
        _,basis,_=build_grid(points,'Ut1')
        with tempfile.TemporaryDirectory() as tmp:
            ds['export_root']=Path(tmp)
            result=app.render(ds,{'config':{'grid_basis':basis,'grid_edits':{'0.0':1.5}}},export=True)
            folder=Path(result['export']['folder'])
            with (folder/'distance_voltage.csv').open(encoding='utf-8-sig') as f:rows=list(csv.DictReader(f))
            self.assertEqual(len(rows),26)
            self.assertEqual(float(rows[15]['voltage_V']),1.5)
            self.assertEqual(json.loads((folder/'settings.json').read_text())['config']['grid_edits'],{'0.0':1.5})

if __name__=='__main__':unittest.main()
