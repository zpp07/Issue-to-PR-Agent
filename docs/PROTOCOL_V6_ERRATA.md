# v6 检索协议勘误（不改历史账本）

E2 正式运行的 36 行、三个 v6 `protocol_hash` 和已落盘 manifest 均保持原样。
本说明补充 v6 运行代码的实际行为，不声称旧 manifest 已完整描述这些细节。
复现历史运行时应同时使用账本所记代码版本
`189bd42542196f616072c8692a9b57036524ddb9`，而不是只看 manifest。

| 环节 | v6 实际行为 | manifest 已记录 |
|---|---|---|
| 初始证据检索 | `top_k=10`、`candidate_k=50`，再按文件去重，最多选 3 个文件 | 只记了文件上限 3；未记 10/50 |
| `search_code` 工具检索 | 模型可指定 `top_k`，默认 5；`candidate_k` 默认 20 | 记了默认 5/20 |
| 检索参数钳制 | `top_k` 限于 1–10；`candidate_k = max(top_k, min(candidate_k, 100))` | 未记钳制规则 |
| Python AST 切块 | 单块最多 120 行；语法错误时退回 80 行窗口 | 只记 `chunk_max_lines=120` |
| 文档切块 | `.md/.rst/.txt/.toml/.yaml/.yml/.json` 单块最多 80 行 | 未单列 80 行 |

`benchmark/real_run.py::evaluate_one` 对 A/B/C 均使用相同的初始检索路径，
所以这些漏记的常量**没有使 E2 组间比较产生不同的检索配置**；但仅凭 manifest
不足以独立复现初始证据。当前压缩 trace 还保留了工具检索的 `query`，未保留模型
指定的 `top_k`，因此也不能从账本逐次重建所有非默认检索调用。

不要修改 v6 manifest 或补写历史 JSONL，也不要为了让未来运行沿用 v6 哈希而将
影响行为的字段排除在哈希输入之外。若开 v7，应在冻结运行前把初始检索、各类切块
上限和参数钳制纳入新协议定义，并在新 trace schema 中保留实际 `top_k`；
新协议使用新哈希，与 v6 并列比较，不能直接合并为同一实验组。
