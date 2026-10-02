# GitHub 警示维护记录

检查日期：2026-10-02。基线：`main` 的 `ea90a9c868dac8de5e106c4aeb727bcc77d9e145`。

## 警示状态与来源

| 项目 | 核实结果 |
| --- | --- |
| [预算计划科目生命周期](https://github.com/78979660-bit/LearningModel/pull/2#discussion_r4094609117) | 基线仍可保存已归档或未登记科目的 selected/completed 条目并归档旧计划；本次修复。 |
| [SQLite 只读探测清理](https://github.com/78979660-bit/LearningModel/pull/2#discussion_r4094609207) | 基线在 PRAGMA 或结构查询失败时未显式关闭连接；本次修复。 |
| [AI 子功能保存](https://github.com/78979660-bit/LearningModel/pull/2#discussion_r4094609274) | 两个解析父开关均关闭后仍保留子功能勾选，保存及重新启用时会恢复；本次修复。 |
| [许可证清单路径](https://github.com/78979660-bit/LearningModel/pull/1#discussion_r4092324233) | 基线在读取归档前未校验清单文件名；本次修复。 |
| [Release 草稿状态](https://github.com/78979660-bit/LearningModel/pull/1#discussion_r4092324275) | 历史意见已过时：v0.1.0 当前是公开预发布，`draft=false`。审查线程本身仍未标记解决。 |
| [Dependabot](https://github.com/78979660-bit/LearningModel/security/dependabot) | 警示 API 返回 403，明确说明仓库已禁用 Dependabot alerts；security updates 也为 disabled。无法据此查询当前或历史警示，更不能据此认定依赖安全。 |
| [代码扫描](https://github.com/78979660-bit/LearningModel/security/code-scanning) | API 返回 404 `no analysis found`，没有可读分析结果。 |
| [泄密扫描](https://github.com/78979660-bit/LearningModel/security/secret-scanning) | 已启用，警示 API 返回 0 项；push protection 已启用。未读取任何秘密值。 |
| [Actions](https://github.com/78979660-bit/LearningModel/actions) | 检查时历史仅 2 次 Copilot code review，均成功；没有测试 CI 工作流，没有失败运行。审查成功不代表测试或安全检查通过。 |

上述四条源码意见已由本地回归测试复现；它们是审查发现，不是 GitHub CodeQL 或 Dependabot 漏洞警示。未修改旧 PR 的线程状态。

## 依赖审计

使用 `pip-audit 2.10.1` 的 PyPI advisory 服务检查锁文件中 21 个固定版本，未跳过任何包。`--no-deps --disable-pip` 直接审计清单中的版本，不解析额外依赖，也不审计 Qt、MuPDF 等原生组件的独立公告。

基线审计产生 41 条记录，按 advisory ID 去重为 21 项，也对应 21 个独立 CVE：Pillow 12.1.0 为 19 项，pip 26.1.1 为 2 项。别名及重复数据使原始计数较大，不能将 41 条解释为 41 个独立漏洞。Pillow 是运行时依赖，风险涉及恶意图片、字体、PDF 的内存越界或资源耗尽等；pip 位于锁文件的 build/verification-only 部分，其风险涉及包安装路径处理，未认定它随桌面安装包交付。版本匹配结果不等于每项都能通过本应用实际调用路径利用，此次未逐项进行利用验证。

- 构建锁文件升级为 `Pillow==12.3.0`、`pip==26.2.1`；源码运行依赖最低版本改为 `Pillow>=12.3.0`。
- 修复后，同一审计命令对 21 个版本返回 0 个已知漏洞。此结果限于服务当时提供的公告和清单范围。
- 参考：[Pillow 12.3.0 安全修复说明](https://pillow.readthedocs.io/en/stable/releasenotes/12.3.0.html)、[pip 官方修复记录](https://pip.pypa.io/en/stable/news/)、[pip 安装路径公告](https://github.com/pypa/pip/security/advisories/GHSA-wf93-45jw-7689)、[pip 包 URL 公告](https://github.com/pypa/pip/security/advisories/GHSA-qwm4-qh6w-59xr)。

复核命令：

```powershell
python -m pip_audit -r requirements-build.lock.txt --no-deps --disable-pip --format json
```

## 修复范围

- 预算计划先开启 `BEGIN IMMEDIATE`，在同一写事务中校验 selected/completed 条目的科目，再归档旧计划；校验失败保留旧计划。excluded 条目可继续保存，供展示排除原因。
- SQLite 只读连接在探测异常后显式关闭，并保留原始异常原因。
- 两个解析父开关均关闭时取消三个子功能勾选，保存时过滤禁用项；仍有一个父开关开启时保留用户选择。
- 清单归档只接受非空单一文件名，拒绝绝对路径、目录分隔符、冒号及点目录，并验证解析后的父目录，随后才读取和校验归档哈希。

## 验证与边界

新增 20 项回归测试覆盖科目生命周期、事务保留、异常清理、父子开关保存及归档路径。相同测试在基线源码上为 17 失败、3 通过，修复后为 20 通过。

本轮使用 Windows x64 CPython 3.12.14；完整安装更新后的构建锁文件。发布版本曾使用 CPython 3.14.5，此次未重建安装包，也未验证安装程序、真实外部 AI 或完整 OCR 链路。

全量验证命令与结果：

```text
python -B -m pytest -q -p no:cacheprovider --junitxml full-test-results.xml
1141 passed, 3 warnings, 279 subtests passed in 364.46s
```

JUnit 为 1420 项，0 失败、0 错误、0 跳过。297 个 Python 文件语法检查通过；Git 使用 `cr-at-eol` 识别仓库保留的 CRLF 后，差异空白检查通过。3 个警告均为现有 ChatGPT bridge 中 `datetime.utcnow()` 的弃用警告。

## 现有安装包

[v0.1.0 公开预发布](https://github.com/78979660-bit/LearningModel/releases/tag/v0.1.0)的安装包及项目源代码 ZIP 对应 `8aeaf92bc786adc517b8d2569b291cdad79243c1`，早于 PR2 和本轮维护。此维护分支与依赖更新不会改变已经下载的安装包。旧安装包需要单独核验打包组件、评估风险并重建验收，本轮没有拆包或发布新版。原发布的源码、第三方源包、许可证材料和校验值均保持原样；未来构建应重新收集与新依赖对应的材料并完成验收。

检查未启用、关闭或削弱安全功能，未合并或发布新版。Dependabot 和测试 CI 的配置缺口需要另行授权维护。
