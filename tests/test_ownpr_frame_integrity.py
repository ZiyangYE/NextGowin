import sys,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from ownpr_frame_integrity import read_frames,encode_frames
from ownpr_image_storage import available


@unittest.skipUnless(available(ROOT/'results/native_device_lut_004/encoding_baseline.fs'),'Requires native capture')
class NativeFrameIntegrityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.frames,cls.header=read_frames(ROOT/'results/native_device_lut_004/encoding_baseline.fs')
    def test_native_frames_reencode_identically(self):
        self.assertTrue((encode_frames(self.frames[:,:190].copy(),self.header)==self.frames).all())

    def test_corruption_and_unknown_header(self):
        data=self.frames[:,:190].copy();data[0,0]^=1;data[500,50]^=128
        rebuilt=encode_frames(data,self.header)
        self.assertFalse((rebuilt[0,190:192]==self.frames[0,190:192]).all())
        self.assertFalse((rebuilt[500,190:192]==self.frames[500,190:192]).all())
        with self.assertRaisesRegex(ValueError,'Uncalibrated'):encode_frames(data,'unknown')


if __name__=='__main__':unittest.main()
