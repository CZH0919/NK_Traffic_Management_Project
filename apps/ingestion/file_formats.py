"""Source-backed import field descriptions; examples are format hints, not data."""
from .uploads import REQUIRED

DESCRIPTIONS = {
    'collision_id':('事故来源编号','发布方事故记录编号，完整保留'),
    'crash_date':('当地日期','2025-01-31 或官方 ISO 日期时间'),
    'crash_time':('当地时刻','07:15，纽约当地时间'),
    'unique_id':('本行来源编号','车辆／人员数据集中的 unique_id'),
    'vehicle_id':('车辆来源标识','车辆文件中为来源 UUID；人员文件中为所关联车辆的来源记录编号'),
    'source_row_id':('官方行标识','官方 :id；使用项目采集工具会保留此字段'),
    'requestid':('采样请求编号','同一次调查的来源编号，保持原值'),
    'segmentid':('来源路段编号','保持前导零，不用系统内部道路编号替代'),
    'direction':('观测方向','NB、SB、EB、WB 等官方原值'),
    'yr':('年','2025'), 'm':('月','1—12'), 'd':('日','1—31，须为有效日期'),
    'hh':('小时','0—23'), 'mm':('分钟','00、15、30、45；其他分钟会保留核查'),
    'vol':('观测流量','非负整数；真实零为 0，缺失不能填零'),
    'wktgeom':('来源空间几何','保留官方 WKT 原文和坐标，不手工转换或编造'),
    'borough':('行政区','MANHATTAN、BRONX、BROOKLYN、QUEENS、STATEN ISLAND'),
    'longitude':('经度','保持官方原值'), 'latitude':('纬度','保持官方原值'),
    'on_street_name':('所在街道','保持来源街道原文'),
    'vehicle_type':('车辆类别','Sedan 等来源原值'), 'vehicle_year':('车辆年份','四位年份；缺失留空'),
    'vehicle_occupants':('车辆乘员数','非负整数；缺失留空'),
    'person_type':('人员类别','Occupant、Pedestrian、Bicyclist 等来源原值'),
    'person_injury':('伤害类别','Injured、Killed、Unspecified 等来源原值'),
    'person_age':('人员年龄','非负整数；缺失留空'), 'person_sex':('性别来源代码','保留原值'),
}
for who,label in [('persons','全部人员'),('pedestrians','行人'),('cyclists','骑行者'),('motorists','驾乘人员')]:
    for outcome,text in [('injured','受伤'),('killed','死亡')]:
        DESCRIPTIONS[f'number_of_{who}_{outcome}']=(label+text+'人数','非负整数；缺失留空，不补零')
OPTIONAL = {
    'crashes':['borough','longitude','latitude','on_street_name']+[k for k in DESCRIPTIONS if k.startswith('number_of_')],
    'vehicles':['vehicle_type','vehicle_year','vehicle_occupants'],
    'persons':['person_type','person_injury','person_age','person_sex'],
    'traffic':[],
}


def fields_for(kind):
    return [dict(key=key,label=DESCRIPTIONS[key][0],example=DESCRIPTIONS[key][1],required=key in REQUIRED[kind])
            for key in sorted(REQUIRED[kind])+OPTIONAL[kind]]
