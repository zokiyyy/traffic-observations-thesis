import unittest
import engine as E
from derive_states import normalize

def track(x=0):return {'track_id':1,'assigned_zone':'z','box_xyxy':[x,0,x+10,10]}
def frame(i,p,d,valid=True,x=0):return {'frame_index':i,'pts':p,'duration_ticks':d,'valid':valid,'tracks_primary':[track(x)] if valid else [],'zone_counts':{'z':1 if valid else 0}}
def win(i,complete=True,count=5):return {'index':i,'start_s':i*10.,'end_s':i*10.+10,'complete':complete,'zones':[{'zone_id':'z','count_mean':count,'motion_median_px_s':1.,'motion_available':complete}]}
class Checks(unittest.TestCase):
 def test_ticks(self):self.assertEqual(list(E.split_ticks(899999,2)),[(0,1),(1,1)])
 def test_hold_split_keeps_count(self):
  a=E.aggregate_frames([frame(0,0,899999),frame(1,899999,90002)],['z'])
  self.assertEqual(a['held_ticks'],{0:900000,1:90001});self.assertEqual(a['counts'][1]['z'],90001);self.assertEqual(a['motions'],{})
 def test_hold_clears_history_both_sides(self):
  rs=[frame(i,i*3000,3000,x=i) for i in range(31)]
  rs.append(frame(31,93000,60000,x=31));rs.append(frame(32,153000,3000,x=100))
  a=E.aggregate_frames(rs,['z']);self.assertEqual(a['motions'][(0,'z')][1][1],3000)
 def test_half_second_boundary(self):
  a=E.aggregate_frames([frame(0,0,45000),frame(1,45000,45001)],['z']);self.assertEqual(len(a['held_intervals']),1)
 def test_invalid_counts_excluded(self):
  a=E.aggregate_frames([frame(0,0,3000,False),frame(1,3000,3000)],['z']);self.assertEqual(a['valid_ticks'][0],3000)
 def test_persistence_resets_on_held_window(self):
  ws=[win(i,i!=2) for i in range(6)];E.classify_windows(ws,{'z':{'density_low_upper':1,'density_high_lower':2,'motion_low_upper':2}})
  self.assertEqual([w['zones'][0]['joint_duration_s'] for w in ws],[10,20,0,10,20,30])
 def test_missing_motion_resets_joint_only(self):
  ws=[win(i) for i in range(4)];ws[2]['zones'][0]['motion_available']=False
  E.classify_windows(ws,{'z':{'density_low_upper':1,'density_high_lower':2,'motion_low_upper':2}})
  self.assertEqual(ws[2]['zones'][0]['joint_duration_s'],0);self.assertEqual(ws[2]['zones'][0]['high_count_duration_s'],30)
 def test_hold_excluded_from_calibration(self):
  a=[win(0,True,1),win(1,True,3),win(2,False,1000)]
  t=E.calibrate(a,['z'])['z'];self.assertAlmostEqual(t['density_low_upper'],1.66);self.assertEqual(t['density_calibration_windows'],2);self.assertEqual(t['motion_low_upper'],-1)
 def test_collapsed_threshold(self):self.assertEqual(E.density_level(1,0,0),'unknown')
 def test_night_zero_is_not_idle(self):
  w=win(0,count=0);w['zones'][0].update(motion_median_px_s=-1,motion_available=False)
  t={'z':{'density_low_upper':0,'density_high_lower':0,'density_calibration_windows':360,'motion_low_upper':-1}}
  E.classify_windows([w],t)
  d=normalize('NE12th',{'source':{'id':'X__2017-09-10_23-08-29.mp4'},'windows':[w],'thresholds':t})
  self.assertEqual(d['windows'][0]['zones'][0]['state_hint'],'night_no_observations');self.assertIsNone(d['windows'][0]['zones'][0]['motion_median_px_s'])
 def test_discontinuous_windows_reset(self):
  ws=[win(0),win(2)];E.classify_windows(ws,{'z':{'density_low_upper':1,'density_high_lower':2,'motion_low_upper':2}});self.assertEqual(ws[1]['zones'][0]['high_count_duration_s'],10)
 def test_dynamic_zone_count(self):
  for size in (4,5):
   zs=[str(i) for i in range(size)];a=E.aggregate_frames([{'frame_index':0,'pts':0,'duration_ticks':1,'valid':True,'tracks_primary':[],'zone_counts':dict.fromkeys(zs,0)}],zs);self.assertEqual(len(a['counts'][0]),size)
if __name__=='__main__':unittest.main(verbosity=2)
