# 开源副本验证记录

日期：2026-09-23。

- 发布副本中的 117 个打包项目 Python 输入，与保留构建工作副本逐字节一致，见 `source-inputs-0.1.0.json`。
- 初次完整测试：1057 passed、5 failed、279 subtests passed；5 条既有弃用警告。
- 失败项集中在两个旧测试模块，原因是依赖未公开的本地模型或学习记录。修复为人工构造夹具，未改变被测应用逻辑或降低断言要求。
- 修复后复验 `test_a05_normalization_and_readback.py` 和 `test_data_date_validation.py`：31 passed，包含上述全部 5 个失败项。未再重复完整套件。
- Python 源码语法检查、PowerShell 构建工具语法检查通过。
- 最终文件集通过凭据特征、个人绝对路径及私有数据文件类型扫描。此扫描不代表对所有潜在敏感信息的绝对保证。
- 现有安装包未修改；SHA-256 为 `de5d24de92581dadba1775bac02627c26c88e29f91bb917d91a13bc88e588446`。

未在本次源码公开流程中重新构建安装包，未完成干净 Windows 环境人工验收。二进制仍是草稿，剩余材料见 `OPEN_SOURCE.md`。
