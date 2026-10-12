"""Behavior checks for scientific transformations and export reproducibility."""
import math
import unittest
import numpy as np
import app


class FigureBehaviorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = app.ROOT / 'data'
        raw = root / 'demo_DOWN.csv'
        markers = root / 'demo_DOWN_markers.csv'
        cls.ds = app.parse_dataset(raw.read_text(encoding='utf-8-sig'),
                                  markers.read_text(encoding='utf-8-sig'), raw.name, markers.name)

    def test_real_demo_window_channel_and_missing_force(self):
        c, p, _ = app.summarize(self.ds, {})
        self.assertEqual(len(p), 39)
        self.assertEqual(sum(x['force'] is None for x in p), 22)
        self.assertLess(p[20]['distance'], 0)
        self.assertIsNone(p[20]['pressure'])
        self.assertEqual(p[0]['n'], 100)
        idx = self.ds['markers'][0]['row']
        exact = self.ds['channels']['Ut1'][idx-100:idx]
        self.assertAlmostEqual(p[0]['mean'], np.mean(exact))
        self.assertAlmostEqual(p[0]['sd'], np.std(exact, ddof=1))
        _, other, _ = app.summarize(self.ds, {'channel': 'Ut2'})
        self.assertNotEqual(p[0]['mean'], other[0]['mean'])
        self.assertAlmostEqual(c['area'], math.pi*19**2/4)
        self.assertAlmostEqual(p[22]['pressure'], .38*1000/(math.pi*19**2/4))

    def test_zero_shift_preserves_force_voltage_and_source(self):
        _, original, _ = app.summarize(self.ds, {})
        _, moved, _ = app.summarize(self.ds, {'zero': -.18})
        for a, b in zip(original, moved):
            self.assertAlmostEqual(b['distance'], a['distance']+.18)
            self.assertEqual(a['force'], b['force'])
            self.assertEqual(a['mean'], b['mean'])
        self.assertAlmostEqual(self.ds['markers'][21]['distance'], -.18)

    def test_linear_interpolation_and_no_zero_force_extrapolation(self):
        points = [{'force': .38, 'mean': 1.0}, {'force': 5.68, 'mean': 2.0}]
        segments, ip = app.series(points, 'force', 0, 5)
        self.assertEqual(len(ip), 1)
        self.assertEqual(ip[0]['x'], 5)
        self.assertAlmostEqual(ip[0]['mean'], 1+(5-.38)/(5.68-.38))
        self.assertTrue(all(min(x) >= .38 for x, y in segments))

    def test_note_count_area_and_invalid_input_blocked(self):
        with self.assertRaises(ValueError):
            app.summarize(self.ds, {}, [[0, None]])
        with self.assertRaises(ValueError):
            app.summarize(self.ds, {'area': 0})
        with self.assertRaises(ValueError):
            app.summarize(self.ds, {'area': None, 'mode': 'pressure'})
        _, points, _ = app.summarize(self.ds, {'area': None, 'mode': 'force'})
        self.assertIsNone(points[22]['pressure'])

    def test_per_marker_window_excludes_invalid_samples(self):
        ds = {**self.ds, 'valid': self.ds['valid'].copy()}
        idx = ds['markers'][0]['row']
        ds['valid'][idx-5] = False
        _, points, warnings = app.summarize(ds, {'overrides': {'1': .5}})
        self.assertEqual(points[0]['n'], 49)
        self.assertEqual(points[1]['n'], 100)
        self.assertTrue(warnings)

    def test_render_is_editable_and_aligned(self):
        result = app.render(self.ds, {'config': {}, 'notes': None})
        g, h = result['panels']['g'], result['panels']['h']
        self.assertEqual(g['clipping'], [])
        self.assertEqual(h['clipping'], [])
        self.assertEqual(g['overlaps'], [])
        self.assertEqual(h['overlaps'], [])
        self.assertEqual(g['axes_bounds'], h['axes_bounds'])
        self.assertIn('<text', g['svg'])
        self.assertIn('font-family="Arial"', g['svg'])
        self.assertNotIn('font:', g['svg'])
        self.assertEqual(g['x_range'], [2, -1])
        self.assertAlmostEqual(h['x_range'][1], 5000/(math.pi*19**2/4))

    def test_native_and_browser_newlines_share_source_identity(self):
        raw = 'Time(s),SampleIndex,Ut1(mV),Ut2(mV)\n0,0,1000,2000\n1,1,1100,2100\n'
        markers = 'SampleIndex,MarkerNo,Note-distance(mm),Note-force(N）\n1,1,-.1,\n'
        a = app.parse_dataset(raw, markers, 'raw.csv', 'markers.csv')
        b = app.parse_dataset('\ufeff'+raw.replace('\n', '\r\n'), markers.replace('\n', '\r\n'), 'raw.csv', 'markers.csv')
        self.assertEqual(a['raw_sha256'], b['raw_sha256'])
        self.assertEqual(a['marker_sha256'], b['marker_sha256'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
