from .models import Collision,Vehicle,Person

MODELS={'crashes':Collision,'vehicles':Vehicle,'persons':Person}
TITLES={'crashes':'事故','vehicles':'车辆','persons':'人员'}
LABELS={'id':'内部编号','local_date':'当地日期','local_time':'当地时间','occurred_at':'确定的 UTC 时间',
 'time_status':'时间转换状态','borough':'行政区','borough_method':'行政区依据','postal_code':'邮政编码',
 'on_street':'所在道路','off_street':'交叉道路','cross_street':'地点补充','geom':'位置坐标',
 'persons_injured':'受伤人数','persons_killed':'死亡人数','pedestrians_injured':'行人受伤','pedestrians_killed':'行人死亡',
 'cyclists_injured':'骑行者受伤','cyclists_killed':'骑行者死亡','motorists_injured':'车内人员受伤','motorists_killed':'车内人员死亡',
 'collision':'所属事故内部编号','vehicle':'所属车辆内部编号','vehicle_type':'车辆类型','make':'制造商','model':'车型',
 'manufacture_year':'车辆年份','state_registration':'登记州／地区','occupants':'乘员数','travel_direction':'行驶方向',
 'pre_crash':'碰撞前状态','driver_sex':'驾驶员性别代码','license_status':'驾照状态','license_jurisdiction':'驾照辖区',
 'point_of_impact':'碰撞部位','public_property_damage':'公共设施损坏标记','public_property_damage_type':'设施损坏描述',
 'person_type':'人员类型','injury':'伤害类别','age':'年龄','sex':'性别代码','ejection':'抛出情况',
 'emotional_status':'生理／情绪状态','bodily_injury':'身体伤害部位','position_in_vehicle':'车内位置',
 'safety_equipment':'安全装备','pedestrian_location':'行人位置','pedestrian_action':'行人动作','pedestrian_role':'参与者角色',
 'complaint':'伤情描述类别','review_note':'业务核查备注','is_active':'有效状态','factors':'因素槽位','damage':'损伤部位',
 'origin':'记录来源','revision':'记录版本','source_record_id':'来源记录编号','source_collision_id':'来源事故引用',
 'source_vehicle_id':'来源车辆引用','source_vehicle_uuid':'来源车辆 UUID','source_person_uuid':'来源人员 UUID'}
EDITABLE={
 'crashes':['local_date','local_time','borough','postal_code','on_street','off_street','cross_street',
     'persons_injured','persons_killed','pedestrians_injured','pedestrians_killed','cyclists_injured','cyclists_killed',
     'motorists_injured','motorists_killed','review_note'],
 'vehicles':['collision','vehicle_type','make','model','manufacture_year','state_registration','occupants','travel_direction',
     'pre_crash','driver_sex','license_status','license_jurisdiction','point_of_impact','public_property_damage','public_property_damage_type','review_note'],
 'persons':['collision','vehicle','person_type','injury','age','sex','ejection','emotional_status','bodily_injury','position_in_vehicle',
     'safety_equipment','pedestrian_location','pedestrian_action','pedestrian_role','complaint','review_note']}
ORIGINS=[('official','官方记录'),('manual','人工记录'),('demo','演示记录'),('all','全部来源')]

LABELS.update({'status':'处理状态','resolution':'核查说明','resolved_by':'处理账户编号','mapping':'类别映射','roles':'账户角色','password_reset':'已重设密码','username':'登录名','first_name':'显示名字','last_name':'显示姓氏'})
