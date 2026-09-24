# 开源发布范围与决定

2026-09-23，项目负责人选择开源分发路线。本仓库项目自身代码使用 AGPL-3.0-only；不采用 PyMuPDF 商业许可路线。

## 本次公开内容

应用源码、算法模块、干净默认资源、测试、安装包构建配置、必要工具、第三方许可收集结果和构建说明。没有上传原项目 Git 历史。

排除个人数据库、根目录学习模型和记录 JSON、API 凭据、备份、迁移回执、性能日志、私有教材和作业。排除一次性的个人记录修订工具，以及含个人资料路径、未进入安装包的化学资料导入脚本。两个旧测试改为使用人工构造的测试学科和记录。

## v0.1.0 公开预发布状态

公开二进制时需提供对应源码、构建材料以及第三方组件的许可和归属信息；源码公开本身不能替代这些材料。

v0.1.0 已于 2026-09-24 作为[公开预发布测试版](https://github.com/78979660-bit/LearningModel/releases/tag/v0.1.0)开放下载。安装包对应[源码提交 `8aeaf92`](https://github.com/78979660-bit/LearningModel/tree/8aeaf92bc786adc517b8d2569b291cdad79243c1)，发布附件同时提供项目源码、第三方源码与许可材料、Windows 验收包及校验值。第三方材料收集结果和局限见该提交的[补充说明](https://github.com/78979660-bit/LearningModel/blob/8aeaf92bc786adc517b8d2569b291cdad79243c1/docs/RELEASE_SUPPLEMENT.md)。

**版本定位：**适合初步试用。Windows 11 虚拟机已完成安装、学科管理、计划和卸载重装等检查；Windows 10、多档系统缩放、真实做题评分、外部 AI 服务与 OCR 尚未完成端到端验收。安装包未进行代码签名。具体范围见[验收说明](https://github.com/78979660-bit/LearningModel/blob/8aeaf92bc786adc517b8d2569b291cdad79243c1/docs/RELEASE_ACCEPTANCE.md)。

## 发布附件

以下材料均可从 [v0.1.0 发布页面](https://github.com/78979660-bit/LearningModel/releases/tag/v0.1.0)下载：

| 附件 | 内容 |
| --- | --- |
| `LearningModel-Setup-0.1.0-x64.exe` | Windows x64 安装包 |
| `LearningModel-0.1.0-source.zip` | 对应该安装包的项目源码、测试与构建配置 |
| `LearningModel-0.1.0-third-party-sources.zip` | 第三方对应源码归档及版本、来源清单 |
| `LearningModel-0.1.0-third-party-notices.zip` | 第三方许可与归属文本 |
| `LearningModel-0.1.0-docs.zip` | 构建时的文档与验收记录 |
| `LearningModel-0.1.0-Windows-QA.zip` | 安装包与独立机器验收工具 |
| `SHA256SUMS.txt`、`SUPPLEMENT-SHA256SUMS.txt` | 附件的 SHA-256 校验值 |

发布附件保留了构建时的文档快照，部分验收材料中的“草稿”描述反映当时状态；当前下载状态以发布页面和本文为准。再次分发时，请保留对应版本的源码、许可和归属材料。

## 源码版本关系

`v0.1.0` Git 标签仍指向首次源码公开提交 `121e68d`；公开安装包及源码 ZIP 对应后续的 `8aeaf92`。核对或复现安装包时，请使用后者，不能仅按标签名判断源码版本。

本文件记录已提供的材料和已完成的验收范围，不宣称所有第三方分发义务或系统兼容性已获得完整认证。
