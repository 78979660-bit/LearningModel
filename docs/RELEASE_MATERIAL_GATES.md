# v0.1.1 修订候选的材料门禁

工具只组装本地候选，不移动标签、上传或替换 Release。公开操作仍需另行授权。材料验证不是法律合规认证。

修订模式必须传入 --reviewed-commit。仓库 HEAD、固定路径 release/distribution-policy.json、LICENSE、开源路线文档和依赖锁须匹配该提交。禁止外部策略覆盖；原生源码组件及版本集合由代码固定。项目源码 ZIP 须完整包含该提交的全部文件，名称及字节均须匹配，不能缩短策略清单排除源码。

策略记录已选 AGPL-3.0-only 路线，并固定决策文档、LICENSE、依赖源码清单和通知清单的 SHA256。许可证验证独立读取实际安装环境中 wheel RECORD 的原始 SHA256，不信任收集目录自报的哈希。许可证、依赖源码和通知目录均拒绝额外文件；打包只写已验证清单，并核验实际写入字节。材料缺失、未知路线、版本不符、哈希不符均记录 blocked；严格收集选项 --require-release-materials 返回非零。

历史模式保留历史原生清单限制。旧 pytest XML 将通过子测试计入 tests 汇总，未单独输出 testcase；旧 build-record 缺少计数字段时按该历史格式读取，不修改原 XML。新构建通过共同的 regression_build_fields 函数保存 passed_subtests、primary_test_cases 和 test_report_sha256；历史最终组装核对报告哈希及实际计数。修订模式仍要求另行审阅的完整集合与显式子测试计数。

修订模式按真实 PyInstaller Analysis 输入、精确锁定 wheel RECORD 和实际打包字节核验原生文件，拒绝重复、缺失、未知及篡改文件。CPython DLL、明确指定的 CPython DLLs/System32 目录属于受限平台来源，不将未知来源自动归类为可信。

回归门禁检查实际 testcase，拒绝空报告、重复或部分用例，以及被汇总字段掩盖的 failure/error。跳过默认阻断，只有下述已审阅的固定专项可以覆盖。修订模式还要求 release/regression-evidence.json 已包含在另行审阅的提交中。该文件使用 schema 1，并绑定以下内容：

- report_sha256：实际完整回归 XML 的 SHA256。
- source_files：除证据文件自身外，全部提交文件的路径与 SHA256 映射。
- python_version 和 pointer_bits：实际测试解释器的版本和位数。
- command：完整测试范围 ['-m', 'pytest', 'tests']。
- nodeids：排序的完整测试节点列表。
- passed_subtests：通过的子测试数量，用于核对 pytest XML 汇总计数。

门禁重新收集当前全套测试，比较节点集合和 XML 用例身份。报告与源码、环境或完整集合不符即阻断。它不自动生成可自行授权的证据，也不依据可改写的 build-record 自行放行。证据文件应在测试完成后单独审阅；绑定的代码、测试及策略先冻结，不得随后修改而继续复用旧报告。

specialist 子记录只能绑定固定 11 个 symlink 用例。普通全套必须完整覆盖当前集合 C；通过集合 P 和跳过集合 S 不相交，且 P∪S=C。S 必须严格等于代码中的 11 项白名单，且全部原始跳过原因含 WinError 1314。专项实际通过集合 A 必须严格等于 S，不能有重复、额外用例、失败、错误或跳过。

子记录固定原始 XML、日志、启动脚本、过程和启动退出记录的哈希，以及 11 项身份、测试/实现/conftest 哈希、Python 版本、位数和精确依赖锁。门禁核对原测试哈希 guard、授权命令、管理员专项完成及进程退出状态、启动退出时序。通过 --specialist-evidence-root 提供原件目录只选择原件位置，不能覆盖已审阅子记录的哈希、版本或范围；默认目录为 release/artifacts/verification/regression。

复用记录必须明确 reviewed_reuse=true、missing_contemporaneous_implementation_hashes=true，并说明独立复审接受复用的理由。原脚本保存了测试执行前哈希 guard，但未分别保存当时实现与 conftest 的前后哈希；不能将当前与原提交一致冒充历史实测。已有专项复用依据是独立复审认可的未变代码记录、原始启动/退出证据和严格集合匹配，原件一并保留。

结果如实保留全套 skipped=11，并单列 specialist_passed=11、covered_full_run_skips=11、uncovered_skips=0；不能宣称全套本身零跳过。当前尚未提交与最终代码绑定的完整回归证据，最终组装仍阻断。此修正不再次提权、不扩大专项范围，不执行安装器或修改系统权限。

修订输出必须为明确选择的新目录。材料失败时停止组装；发布操作与本地验证分离。
