# 纽约区域离线参考底图

`nyc-20261027.pmtiles` 由 Protomaps 2026-10-27 全球快照按区域提取，范围为西经 74.65—73.30、北纬 40.35—41.06，原生缩放级别 0—15。它只用于地理背景，不参与交通业务统计或空间归属。

来源：[Protomaps 数据下载](https://docs.protomaps.com/basemaps/downloads)、[固定快照](https://build.protomaps.com/20261027.pmtiles)。底图基于 OpenStreetMap 和 Natural Earth；保留 © OpenStreetMap contributors / Protomaps 署名，适用 [Open Database License 1.0](https://opendatacommons.org/licenses/odbl/1-0/) 和[来源使用说明](https://www.openstreetmap.org/copyright)。Natural Earth 为公共领域。

区域包与本说明共同保留，区域包通过 Git LFS 管理，执行 `git lfs pull` 可取得完整文件。既有数据库维护包的文件范围不包含此参考底图；仅有 Git 指针不能作为地图使用。新设备可从 LFS 或保管副本恢复，也可在固定远端快照仍可获取时按 `docs/道路地图与流量分析.md` 的安装步骤重新提取。资源清单和校验信息见 `config/map_resources.json`。
