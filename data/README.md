# 数据目录

本目录保存固定的官方输入和离线参考地图。网页业务查询读取已清洗、导入 PostgreSQL/PostGIS 的数据；这些文件用于来源追溯、重新导入与地图显示，不是正在运行的数据库。

| 位置 | 内容 | 版本管理 |
|---|---|---|
| `raw/nyc_2021_2025_20261004/` | 2021—2025 事故、车辆、人员、采样流量，LION 26b 道路／节点及同版边界；包含原始空间压缩包及已展开的正式导入输入 | Git LFS，固定快照 |
| `manifests/nyc_2021_2025_20261004/` | 23 份来源、范围、记录数、文件与完整性清单 | 普通 Git |
| `basemaps/nyc-20261027.pmtiles` | 纽约市及周边的离线地理参考背景 | Git LFS |
| `basemaps/README.md` | 地图来源、许可与部署说明 | 普通 Git |
| `raw/uploads/`、`manifests/uploads/` | 本机导入任务产生的用户文件及清单 | 仅本机保管，不提交 |

正式原始输入与地图合计约 681 MiB，文件名和目录保留既有来源清单中的路径。它们是静态发布材料，正常启动不会改写。`static/data/` 中的小型展示边界属于前端资源，另随普通 Git 管理。

安装 Git LFS 后克隆仓库；已有克隆执行：

```powershell
git lfs install --local
git lfs pull
git lfs fsck
git status --short
```

`git lfs ls-files` 可以查看已管理文件。普通 Git 中保存的是指向真实文件的指针；工作区应保留完整数据。参考地图的文件头是 `PMTiles`，不能使用仅含 `version https://git-lfs...` 的指针文件启动详细地图。

克隆和拉取不会自动创建账户、覆盖数据库或导入事实。全新环境的初始化及导入步骤见[项目说明](../readme.md)和[数据导入与质量规则](../docs/数据导入与质量规则.md)；已有运行环境继续使用原库。数据库备份可能包含账户与运行记录，单独保管，不放入公开 Git/LFS 仓库。

来源目录中保留 NYC Open Data／NYC DCP 的清单与元数据；地图的 OpenStreetMap、Protomaps、Natural Earth 署名和许可见[参考底图说明](basemaps/README.md)及[第三方资源说明](../docs/第三方资源说明.md)。
