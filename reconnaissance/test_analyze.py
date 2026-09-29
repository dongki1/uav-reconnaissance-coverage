import io
import unittest

import numpy as np
from PIL import Image

from reconnaissance.analyze import (LocalPlane, attitude, ground_to_image, inside_ring,
                                    patch_quality, plan_views, validate_row)
from uav_web.core import DEFAULT_CAMERA, project


class CoverageTests(unittest.TestCase):
    def setUp(self):
        self.row=dict(lat=37.,lon=126.,altitudeAgl=50.,roll=0.,pitch=0.,yaw=0.,
                      gimbal=dict(pitch=-90.,yaw=0.,zoom=1))

    def test_nadir_gsd_and_image_boundary(self):
        u,v,gsd,inc,valid=ground_to_image(np.array([0.,200.]),np.array([0.,0.]),50.,
                                        attitude(self.row),DEFAULT_CAMERA)
        self.assertAlmostEqual(u[0],1920)
        self.assertAlmostEqual(v[0],1080)
        self.assertAlmostEqual(gsd[0],50/2248)
        self.assertEqual(valid.tolist(),[True,False])

    def test_round_trip_oblique_and_jacobian(self):
        self.row.update(roll=.03,pitch=-.05,yaw=1.2)
        self.row['gimbal'].update(pitch=-45.,yaw=20.)
        state=dict(latitude=37.,longitude=126.,altitude_agl=50.,roll=.03,pitch=-.05,yaw=1.2,
                   gimbal_pitch=-45.,gimbal_yaw=20.,gimbal_zoom=1)
        pixel=np.array([2450.,1700.])
        q=project(state,pixel,DEFAULT_CAMERA)
        u,v,g,_,valid=ground_to_image(np.array([q['east_m']]),np.array([q['north_m']]),50.,attitude(self.row),DEFAULT_CAMERA)
        np.testing.assert_allclose([u[0],v[0]],pixel,atol=1e-8)
        self.assertTrue(valid[0])
        derivatives=[]
        for axis in (0,1):
            step=np.eye(2)[axis]*.001
            a=project(state,pixel+step,DEFAULT_CAMERA);b=project(state,pixel-step,DEFAULT_CAMERA)
            derivatives.append([(a['north_m']-b['north_m'])/.002,(a['east_m']-b['east_m'])/.002])
        expected=np.linalg.svd(np.array(derivatives).T,compute_uv=False)[0]
        self.assertAlmostEqual(g[0],expected,places=7)

    def test_horizon_and_invalid_zoom(self):
        self.row['gimbal']['pitch']=90
        *_,valid=ground_to_image(np.array([0.]),np.array([0.]),50.,attitude(self.row),DEFAULT_CAMERA)
        self.assertFalse(valid[0])
        self.row['gimbal']['zoom']=2
        with self.assertRaises(ValueError):validate_row(self.row)

    def test_coordinate_round_trip(self):
        p=LocalPlane(37.,126.)
        lonlat=p.lonlat(np.array([100.,-100]),np.array([40.,-30.]))
        np.testing.assert_allclose(np.column_stack(p.xy(*lonlat.T)),[[100,40],[-100,-30]],atol=1e-8)

    def test_aoi_hole(self):
        outer=[[0,0],[10,0],[10,10],[0,10],[0,0]]
        hole=[[3,3],[7,3],[7,7],[3,7],[3,3]]
        x=np.array([1.,5.,12.]);y=np.array([1.,5.,5.])
        mask=inside_ring(x,y,outer)&~inside_ring(x,y,hole)
        self.assertEqual(mask.tolist(),[True,False,False])

    def test_quality_flat_and_textured(self):
        path=io.BytesIO()
        Image.new('L',(1280,720),128).save(path,format='PNG');path.seek(0)
        sharp,clip=patch_quality(path)
        self.assertTrue(np.all(sharp==0));self.assertTrue(np.all(clip==0))
        a=(np.indices((720,1280)).sum(axis=0)%2*150+50).astype('uint8')
        path=io.BytesIO();Image.fromarray(a).save(path,format='PNG');path.seek(0)
        sharp,_=patch_quality(path)
        self.assertTrue(np.all(sharp>30))

    def test_greedy_no_double_count(self):
        x,y=np.meshgrid(np.arange(0,200,5.),np.arange(0,100,5.))
        deficit=np.ones(x.size)
        plans=plan_views(x.ravel(),y.ravel(),deficit,5,DEFAULT_CAMERA,50,.1,20)
        self.assertLessEqual(sum(p['deficient_area_m2'] for p in plans),len(deficit)*25)
        self.assertEqual(len(set((p['east_m'],p['north_m']) for p in plans)),len(plans))
        with self.assertRaises(ValueError):plan_views(x.ravel(),y.ravel(),deficit,5,DEFAULT_CAMERA,500,.1,2)


if __name__=='__main__':
    unittest.main()
