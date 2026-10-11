"""Export a field-level dictionary from the migration-backed Django model state."""
import os
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
os.environ.setdefault('DJANGO_SETTINGS_MODULE','config.settings')
import django
django.setup()
from django.apps import apps
from django.db import connection

TITLES={
'core.Borough':'行政区字典','core.AuditEvent':'操作记录','core.FieldChange':'字段修正证据','core.MaintenanceRun':'备份与隔离恢复记录',
'core.SavedQuery':'个人查询条件','core.CategoryMapping':'类别映射版本','core.DataRevision':'数据修订水位',
'ingestion.ImportJob':'文件校验与导入任务',
'ingestion.DataSource':'来源目录','ingestion.Snapshot':'原始快照','ingestion.ImportBatch':'导入批次','ingestion.StagedRecord':'原始逐行证据','ingestion.QualityIssue':'数据质量问题',
'network.NetworkVersion':'道路网络版本','network.Junction':'网络节点/路口','network.RoadSegment':'道路路段',
'network.SpatialRun':'空间匹配批次','network.SpatialMatch':'事故空间关联','network.SamplingRoadMatch':'采样地点道路关联',
'collisions.Collision':'事故事件','collisions.Vehicle':'涉事车辆','collisions.Person':'涉事人员','collisions.Factor':'影响因素字典','collisions.CollisionFactor':'事故来源因素槽位','collisions.VehicleFactor':'车辆因素','collisions.PersonFactor':'人员因素','collisions.VehicleDamage':'车辆损伤位置',
'traffic.SamplingRequest':'采样请求','traffic.SamplingSite':'采样地点及方向','traffic.FlowConflict':'流量冲突组','traffic.TrafficObservation':'15 分钟流量观测',
'analytics.AnalysisTarget':'历史分析对象','analytics.RuleVersion':'不可变规则版本','analytics.RiskRun':'风险计算任务','analytics.RiskResult':'不可变风险结果','analytics.AggregateCache':'版本化统计缓存',
'forecasting.ForecastRun':'预测计算与历史序列','forecasting.ForecastPoint':'周期预测值与经验参考带','forecasting.BacktestFold':'时间顺序回测证据','forecasting.AnomalyResult':'已观测周期异常判断',
'reports.Report':'不可变分析报告','reports.ReportSnapshot':'报告来源快照','reports.ExportJob':'完整文件导出任务','reports.AIBrief':'模型简报与当前编辑','reports.AIRequest':'模型调用尝试','reports.BriefRevision':'简报编辑证据',
}
NOTES={
'template_version':'报告模板与图表表达版本','risk_run':'报告引用的已完成风险计算','forecast_run':'报告引用的已完成预测计算',
'report':'所属完成报告；所有者必须一致','format':'csv 为分卷 CSV ZIP；xlsx 为 Excel 工作簿','row_count':'实际写入的全部数据行数，不含表头和说明','file_size':'文件字节数',
'metadata':'导出时保存的范围、单位、来源、规则、质量及完整表行数','provider':'模型 API 提供方','endpoint':'已校验 HTTPS 服务地址，不含密钥','prompt_version':'汇总事实与引用校验协议版本','fingerprint':'内部输入指纹，仅用于同报告复用，不对外展示',
'input_summary':'送往模型的确定汇总与限制，不含逐行人员／车辆事实','raw_text':'成功模型返回的原始 JSON 文本','generated_text':'依据校验引用形成的确定模型稿','edited_text':'当前人工编辑稿；revision 为零时无编辑',
'http_status':'已取得的 HTTP 状态；网络错误或历史未记录时为空','prompt_tokens':'服务返回的输入 token 用量；不可取得时为空','completion_tokens':'服务返回的输出 token 用量；不可取得时为空','elapsed_ms':'尝试耗时毫秒','brief':'所属模型简报','number':'从一开始的连续编辑版本号','text':'该版本的人工编辑文本',
'canonical':'同来源路段编号的稳定代表要素；所有原要素保留','is_canonical':'是否代表要素','match_eligible':'无几何和关键属性歧义的实际机动车道路候选',
'normalized_segment_id':'仅为编号对照去除前导零；不替代原始来源标识','rb_layer':'来源 R/B/G/N 实路与概化层','segment_type':'来源道路类型；排除虚拟、连接、终止等要素作为自动候选',
'level_from':'来源起点层级，M 为地面','level_to':'来源终点层级，M 为地面','road_type':'来源 RW_TYPE','bearing_deg':'起终点测地线方位角，用于保守方向对照',
'source_variants_count':'同编号来源要素数','geometry_variants':'同编号不同几何数','aliases':'同编号来源街名集合','classification_evidence':'来源字段、规则版本与派生依据',
'display_name':'人工展示名称，不覆盖来源名称','virtual_intersection':'来源标记的虚拟交点','road_degree':'有效地面道路连接数','street_names':'有效地面道路来源名称集合',
'rule_version':'计算使用的规则版本','parameters':'本次匹配半径和阈值','distance_m':'PostGIS geography 测地距离，单位米',
'input_revision':'匹配时对象修订号；不等于当前号时禁止用于可靠关联统计','candidates':'候选内部编号、来源编号、名称、方向、距离及当时版本',
'evidence':'空间核查证据；人工确认可记录历史适用起止日和目标版本','reviewed_by':'最后人工复核账户','reviewed_at':'人工复核时刻',
'source_id_match':'来源路段编号标准化后是否相符','direction_compatible':'采样方向与目标方位／通行方向的对照，未知保持 NULL',
'review_note':'业务核查备注；人工维护需记录依据，原始载荷保持不变',
'schema_version':'筛选条件表达版本；档案为 1，统计／对比／风险／预测为 2','conditions':'已校验的统一筛选条件，不保存查询结果',
'canonical_code':'统一分类代码','canonical_label':'统一分类显示名称','raw_value':'来源类别原值',
'snapshot_ids':'本次修订涉及的来源快照编号集合','domain':'受影响数据域','committed_at':'修订提交时刻',
'owner':'仅本人可读写查询条件','requested_by':'本次请求执行或重试的账户，执行时重新校验权限',
'original_name':'原文件显示名称，不参与服务端路径构造','file_size':'原文件字节数','phase':'validate / import',
'progress_rows':'当前阶段已处理或已提交的行数','summary':'文件范围与分类计数；不声明全量覆盖',
'manifest_path':'受控文件清单相对路径','worker_token':'本次工作领取标识','heartbeat_at':'最近分批进度心跳',
'id':'内部主键；与来源编号分离','code':'稳定代码','name':'名称/来源原文','chinese_name':'中文显示名称',
'source_record_id':'来源唯一记录键；流量采用稳定 source_row_id，不能把冲突候选键当唯一键',
'raw_record':'当前值对应的原始证据；原始文件与载荷不得改写','origin':'official / manual / demo；默认官方统计排除 demo 与 manual',
'revision':'当前业务修订号，从 1 开始；修改需留痕','is_active':'逻辑有效标志；失效不删除来源证据',
'local_date':'纽约当地日期','local_time':'纽约当地时刻；不是北京时间','occurred_at':'仅唯一时刻有 UTC 值；timestamptz','observed_at':'仅唯一时刻有 UTC 值；timestamptz',
'time_status':'unique / ambiguous / nonexistent / missing','geom':'WGS84 类型化几何；距离不得直接按度计算',
'source_collision_id':'来源事故引用，缺父记录也保留','source_vehicle_id':'Person.vehicle_id 原值，对应 Vehicles.unique_id',
'source_vehicle_uuid':'Vehicles.vehicle_id 原值，不是来源数字主键','source_person_uuid':'来源统计记录 UUID，不是个人身份证件',
'source_slot':'重复字段的来源槽位；事故槽位不得猜配车辆明细','actor_label':'发生时的账户名称，不存凭据',
'details':'有结构的非敏感上下文，不接收密码/表单/任意 SQL','payload':'完整原始行载荷，不用于替代常规关系分析',
'relative_path':'受控原始文件相对路径；导入服务必须限制到快照目录','checksum':'内部完整性/重复识别，页面不展示摘要清单',
'manifest':'来源字段、请求范围、版本、单位和文件清单','source_count':'源记录数；未知为 NULL，不填 0',
'data_through':'实际数据截止日期，区别于捕获日期和电脑日期','captured_at':'来源捕获时间，带时区',
'period_start':'快照范围起日（含）','period_end':'快照范围止日（含）','attempt':'同快照的尝试编号，从 1 开始',
'checkpoint':'已提交块对应的原始行检查点','row_number':'原始文件行序号，从 1 开始','source_key':'原始来源键，可空；不等于内部主键',
'disposition':'pending / accepted / rejected / conflict','resolution':'核查理由/证据摘要；已处理问题必填',
'source_node_id':'带版本的节点来源标识，保留前导零','source_feature_id':'版本内来源要素标识，例如 OBJECTID',
'source_segment_id':'来源路段编号字符串，不能当成跨版本内部 ID','native_crs':'原始 CRS 与单位依据，不能凭坐标数值静默猜定',
'is_intersection':'可空；不能把所有节点自动视为交叉路口','classification_method':'路口认定规则/来源',
'length_m':'派生长度，单位米；保留计算版本','location_key':'请求内候选地点指纹，使用来源路段编号与方向；位置描述和几何另行保留并核查',
'source_network_version':'流量字段说明为 LION 14，必须与目标道路网络分开','volume':'单个 15 分钟观测数；0 是真实零；缺测没有行',
'quality_status':'pending / valid / conflict / excluded；冲突未解决不能标为有效',
'previous_value':'人工修正前值，与事件同事务提交','new_value':'人工修正后值，与事件同事务提交','reason':'人工修正依据，非空',
'borough_method':'reported / spatial / manual / unknown；推导不覆盖原始载荷',
'age':'有效年龄 0—130；异常原值仍留原始证据，不强行修剪','persons_injured':'事故级伤人数，NULL 与 0 不同，不强制等于人员明细汇总',
'persons_killed':'事故级死亡人数，NULL 与 0 不同',
'scope':'全市／行政区／道路／路口；CHECK 保证只设置对应目标外键',
'definition':'规则依据与限制；版本建立后不可改写','date_from':'分析窗口起日（含）','date_to':'分析窗口止日（含）',
'data_version':'当时统计引擎、来源快照状态与范围、业务修订水位的确定上下文',
'counts':'确定的事故／已报告伤亡／缺失／近期数及时间分母','components':'特征原值、经验百分位、权重和贡献',
'score':'历史强度分 0—100；不满足条件则 NULL，不代表事故概率','grade':'按版本规则分级，不分级与低风险区分',
'rank':'同分并列名次；不分级为 NULL','limitation':'样本、覆盖与字段缺失等限制说明','point':'计算当时的目标代表坐标 [经度,纬度]，用于历史热点显示',
'query_key':'规范化查询条件与引擎版本的内部缓存键','data_token':'来源与修订上下文的内部版本键，版本不同不可命中','expires_at':'缓存有效期止时刻，最长 15 分钟','attempts':'后台任务实际领取次数',
'training_end':'本次历史范围的末个完整日历周期截止；不等于当前电脑日期','frequency':'纽约当地自然月或周一至周日','metric':'事故级数量或已报告伤亡指标','horizon':'月度 1—12 期，周度 1—26 期',
'history':'所有日历周期的确定历史、部分量、覆盖、缺失及支撑范围；缺失不补零',
'step':'距训练原点的预测步长，从 1 开始','estimate':'未来周期的非负数量估计','lower':'预测经验参考带或历史异常阈值下界；无依据则 NULL','upper':'预测经验参考带或历史异常阈值上界；无依据则 NULL',
'interval_samples':'该步长的独立评估绝对误差样本数','interval_method':'经验参考带构造版本；样本不足时为空',
'origin_date':'历史预测原点，必须早于目标周期','train_end':'该折训练截止，不晚于预测原点','actual':'完整目标周期的实际指标值','predicted':'仅使用原点以前数据的预测值','baseline':'上一期基线或历史参照中位数，按实体区分','absolute_error':'实际与预测之差的绝对值',
'observed':'历史完整周期值；不完整或缺失时 NULL','reference_from':'历史异常参照窗口起日','reference_to':'参照窗口止日，严格早于当前被判断周期','reference_count':'实际历史参照周期数',
}
LABELS={
'publisher':'发布机构','dataset_id':'来源数据集标识','kind':'数据种类','homepage':'官方来源地址','description':'说明','license_note':'许可与署名说明','version':'来源版本','status':'生命周期状态','created_by':'提交账户','created_at':'创建时间','started_at':'开始时间','finished_at':'结束时间','updated_at':'最近修改时间','total_rows':'源总行数','accepted_rows':'接受行数','rejected_rows':'拒绝行数','pending_rows':'待核查行数','skipped_rows':'跳过行数','error_code':'可公开的错误代码','severity':'问题严重程度','resolved_by':'处置账户','resolved_at':'处置时间','field_name':'变更字段名','action':'操作类别','object_type':'目标类型','object_id':'目标内部标识','outcome':'执行结果','actor':'操作账户','label':'版本标签','postal_code':'邮政编码原文','on_street':'事发道路原文','off_street':'邻接道路原文','cross_street':'地址/交叉街道原文','vehicle_type':'来源车辆类型','make':'品牌','model':'型号','manufacture_year':'制造年份','state_registration':'登记州','occupants':'车内人数','travel_direction':'行驶方向','pre_crash':'事故前动作','driver_sex':'驾驶员性别统计值','license_status':'驾驶资格状态','license_jurisdiction':'驾驶资格地区','point_of_impact':'首次撞击位置','public_property_damage':'公共财产受损状态','public_property_damage_type':'受损公共财产类型','person_type':'人员类别','injury':'伤害类别','sex':'性别统计值','ejection':'甩出状态','emotional_status':'意识/状态分类','bodily_injury':'受伤部位','position_in_vehicle':'车内位置','safety_equipment':'安全装备','pedestrian_location':'行人位置','pedestrian_action':'行人动作','pedestrian_role':'行人类别','complaint':'伤情描述类别','direction':'观测方向','street':'采样道路原文','from_street':'起点道路原文','to_street':'终点道路原文','source_request_id':'来源采样请求编号','feature_type':'来源道路要素类型','traffic_direction':'通行方向',
'pedestrians_injured':'事故级行人受伤数','pedestrians_killed':'事故级行人死亡数','cyclists_injured':'事故级骑行者受伤数','cyclists_killed':'事故级骑行者死亡数','motorists_injured':'事故级车内人员受伤数','motorists_killed':'事故级车内人员死亡数',
}

def main():
    lines=['# 数据字典','', f'版本 1.7；字段由 Django 模型导出，数据库额外约束见文末。内部主键与来源主键区分；NULL 表示未知/未匹配，不能等同零。当前共 {len(TITLES)} 张项目表。', '', '## 1. 已建立的物理表', '']
    for label,title in TITLES.items():
        model=apps.get_model(label)
        lines.extend([f'### {title}：`{model._meta.db_table}`','', '| 字段 | PostgreSQL 类型 | 可空 | 键 / 含义 |','|---|---|---|---|'])
        for field in model._meta.local_fields:
            desc=NOTES.get(field.name,LABELS.get(field.name,''))
            if label=='ingestion.ImportJob' and field.name=='attempt': desc='后台领取次数；排队未执行时为 0'
            if label=='network.SpatialRun' and field.name=='phase': desc='prepare / collisions / sites / complete'
            if label=='network.SpatialRun' and field.name=='checkpoint': desc='当前对象阶段最后提交的内部主键，可断点继续'
            if label=='network.SpatialRun' and field.name=='summary': desc='路网派生分类与匹配状态分类计数'
            if label=='core.CategoryMapping' and field.name=='version': desc='同类别同原值的映射修订号，从 1 开始'
            if label=='analytics.RuleVersion' and field.name=='parameters': desc='按 code 区分：历史强度权重与门槛，或预测参数网格、时序切分、样本资格、参考带与异常阈值'
            if label=='analytics.RuleVersion' and field.name=='version': desc='同规则代码下的不可变版本号，从 1 开始'
            if label=='analytics.RiskRun' and field.name=='summary': desc='覆盖、参照类型／规模／范围、目标数和关联事故边界'
            if label=='analytics.AggregateCache' and field.name=='payload': desc='已计算的统计／对比聚合；不保存原始逐行载荷'
            if label=='analytics.AggregateCache' and field.name=='created_at': desc='缓存最近生成时刻'
            if label=='forecasting.ForecastRun' and field.name=='summary': desc='适用性、比较／评估分界、所选参数、逐步长误差与方法对照、异常计数及限制'
            if label.startswith('forecasting.') and field.name in ('period_start','period_end'): desc='对应历史或外推日历周期的起日／止日（含）'
            if label=='forecasting.BacktestFold' and field.name=='phase': desc='参数比较或独立评估；选择参数不使用独立评估结果'
            if label=='forecasting.BacktestFold' and field.name=='method': desc='固定算法及参数标识，如 naive、ma:12、ses:0.3'
            if label=='forecasting.AnomalyResult' and field.name=='reason': desc='历史阈值判断或不可判断的原因；不作因果解释'
            if label=='reports.Report' and field.name=='payload':desc='冻结指标、完整小表、图表 SVG、口径与规则副本；大型子表由不可变分析任务关联'
            if label.startswith('reports.') and field.name=='owner':desc='报告／导出／简报所有者，访问与执行均检查权限'
            if label=='reports.AIRequest' and field.name=='attempt':desc='该简报从一开始的调用尝试次数，失败也计入配额'
            if label=='core.MaintenanceRun':
                desc={'source_run':'恢复所引用的已完成备份；备份自身为空','target_label':'数据库目标名称；恢复工具只接受新的隔离库','artifact_relative_path':'受控备份／恢复目录相对路径，不含私有配置位置','summary':'范围、表与文件计数、耗时、完整性和恢复核对结果；内部校验值不在页面展示','operation':'backup 或 restore','actor':'本地维护操作归属的应用管理员'}.get(field.name,desc)
            if field.primary_key: desc='PK；'+desc
            elif field.unique: desc='UNIQUE；'+desc
            if field.is_relation:
                desc=f'FK → {field.related_model._meta.db_table}.{field.target_field.column}；PROTECT；'+desc
            if field.get_internal_type().startswith('Positive'): desc+='；非负'
            if field.choices:
                desc+='；枚举：'+', '.join(str(k) for k,v in field.choices)
            lines.append(f'| `{field.column}` | `{field.db_type(connection)}` | {"是" if field.null else "否"} | {desc or "见关联实体职责"} |')
        lines.append('')
        uniques=[', '.join(c.fields) for c in model._meta.constraints if getattr(c,'fields',None)]
        if uniques: lines.extend(['组合唯一约束：'+ '；'.join('`'+x+'`' for x in uniques)+'。',''])
        indexes=[', '.join(idx.fields) for idx in model._meta.indexes]
        if indexes: lines.extend(['显式索引：'+'；'.join('`'+x+'`' for x in indexes)+'。另有主键、唯一及外键索引。',''])
    lines.extend(['## 2. 数据库额外约束','',
    '- 事故、车辆、人员、观测的来源官方记录必须引用来源、来源键及原始行；来源+来源键的条件唯一性排除 NULL，不排除有效来源记录。',
    '- Person(vehicle_id,collision_id) 组合外键引用 Vehicle(id,collision_id)；RoadSegment 端点与 NetworkVersion 一致；Observation 引用 FlowConflict 的地点和当地时段必须相同。上述关联可延迟至事务提交核对。',
    '- 所有空间字段由 PostGIS 限制类型/SRID；点检查经纬度范围，行政区/道路检查几何有效性。geom 为 GiST 空间索引。',
    '- Raw 原始身份及载荷不可更新/删除，AuditEvent/FieldChange/DataRevision 不可更新/删除；CategoryMapping 只允许切换有效状态，不能覆盖版本内容或删除；数据库触发器定义在迁移中。',
    '- SavedQuery 在有效记录范围内按 owner + name 唯一；CategoryMapping 同种类同原值同版本唯一，且最多一个有效版本；DataSource 的非空数据集标识在 kind 内唯一。',
    '- 时间 unique 状态必须有 UTC，非 unique 不允许虚构 UTC；流量分钟为 0/15/30/45 且秒为 0。',
    '- 批次分类行数和不得大于总数；已处理质量问题必须有理由、操作者和时间。',
    '- RoadSegment 与 Junction 的来源身份及几何不可覆盖；空间关联目标必须属于指定网络。事故 matched 状态恰有一个道路或路口目标；其他状态目标为空。每对象每网络只保留一个当前关联，人工变更前后值保存在审计中。',
    '- 道路候选附加条件 GiST 索引 road_match_geom_gist；网络状态、路段关联与地点日期时间组合索引用于地图和观测查询。',
    '- AnalysisTarget 的 scope 与唯一目标外键一致；RuleVersion 的 code/version 唯一。规则、目标、RiskResult 禁止更新或删除；成功 RiskRun 禁止更新或删除。结果与任务层级及网络一致，由额外触发器校验。',
    '- RiskResult 的 run/target 唯一，score 为 NULL 或 0—100；任务起止日合法。任务状态／创建时间、结果任务／分值及任务／等级索引支持领取、排名和筛选。',
    '- 成功 ForecastRun 及其 ForecastPoint、BacktestFold、AnomalyResult 不可改写或删除。仅运行中任务能追加子结果，整体计算事务失败全部回滚；预测任务必须使用 count-forecast 规则，目标与任务网络一致。',
    '- ForecastPoint 的 run/step 唯一，预测周期在完整历史截止之后且步长不超任务范围；估计非负，参考带上下界同时为空或有序包含估计值。BacktestFold 的 run/phase/method/origin_date/step 唯一，训练截止不晚于原点，目标严格晚于原点且不超已观测完整截止。AnomalyResult 的 run/period_start 唯一，参照窗口严格早于当前周期。',
    '- 成功 Report 和 ExportJob 不可更新／删除；ReportSnapshot 与 BriefRevision 不可更新／删除。报告按 kind 恰好引用已完成风险或预测，来源只可在报告运行中追加。导出／简报所引用报告必须已成功且 owner 相同。',
    '- 成功 AIBrief 仅允许更新 edited_text、revision、edited_at，版本须递增一；原始模型文、汇总与模型标识保持不变。AIRequest 的成功／失败尝试不可改写。编辑记录 actor 必须为简报 owner，brief/number 唯一。',
    '- MaintenanceRun 的 operation 与 source_run 可空性相符；成功／失败的结束记录不可覆盖或删除。恢复服务另校验来源备份成功、目标新建、归档及文件完整性，并记录执行与核对结果。',
    '', '## 3. Django 基础表','',
    '| 表 | 职责 |','|---|---|','| auth_user | 用户名、密码哈希、有效状态及登录时间；业务用户不是数据库登录角色 |','| auth_group / auth_permission | 三类角色及受控能力；AccessPolicy 只作为权限载体，不产生业务表 |','| auth_user_groups / auth_group_permissions / auth_user_user_permissions | 认证框架关联表；本系统默认采用角色授权 |','| django_content_type | 权限对应模型类型 |','| django_session | 服务端会话；浏览器只持会话标识 |','| django_migrations | 迁移执行版本 |','',
    '## 4. 后续实体与统计字典','',
    '完整实体职责及模块接口见[详细设计第 8 节](详细设计.md#8-后续逻辑表与模块接口)。维护记录、报告六表、统计风险五表、预测四表、空间关联及业务维护结构均已建立；运行维护记录亦已建立。流量小时／日结果按请求计算，不存储可陈旧的汇总表。方法见[统计与历史风险评价](统计与历史风险评价.md)、[统计预测与回测](统计预测与回测.md)和[报表导出与 AI 简报](报表导出与AI简报.md)。', '',
    '| 指标/范围 | 统计单位与口径 |','|---|---|',
    '| 事故数 | Collision 的事件数；不能直接统计多表展开后的行数 |',
    '| 事故级伤亡 | Collision 各伤亡列之和，同时提供缺失数；不以 Person 行数替代 |',
    '| 车辆/人员数 | 对应实体行数，各自来源主键去重；不将“缺少明细”解释为数量真实为零 |',
    '| 15 分钟流量 | 每个有效观测的 volume；冲突需单独处置；缺测不是观测为零 |',
    '| 小时/日流量 | 分为部分观测合计和通过完整性检查的时段合计，附槽位覆盖、冲突、夏令时状态 |',
    '| 流量归一化事故率 | 地点、方向、观测窗口、事故口径一致且暴露量可靠时才定义 |',
    '| 历史风险 | 指定窗口/规则下的可解释派生分值；不是未来单位出行事故概率 |',
    '| 预测与误差 | 按预测原点以前的数据计算，回测采用时间顺序；MAE 的单位随预测对象 |',
    '| 数据版本 | 来源快照集合 + 人工修订水位/版本；历史报告保存生成时的确定结果 |',''])
    (ROOT/'docs/数据字典.md').write_text('\n'.join(lines),encoding='utf-8')
    print(f'Dictionary exported: {len(TITLES)} project tables.')

if __name__=='__main__': main()
