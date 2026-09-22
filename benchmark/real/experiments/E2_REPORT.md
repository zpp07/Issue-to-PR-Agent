# E2：确定性终止与单轮 Reviewer/Reviser 实验

> 结论先行：在 4 个真实仓库任务、每组 12 次 provider-default 重复运行中，
> B 组以更少 token 得到最高的观察通过率，但样本很小且确定性终止触发的
> 3 条运行只有 1 条通过隐藏测试；C 组没有显示出优于 B 的收益。

## 冻结协议

- 正式运行：36 条，仅包含 protocol v6
- 代码版本：`189bd42542196f616072c8692a9b57036524ddb9`
- evaluator 协议：`19b8930fb4e55d2f188bde0e1a3d137a19a4b66d9e8d32e5511e9fb99b55a513`
- 每个任务每组 3 次独立 provider-default 重复；API 未提供显式随机种子
- Agent 始终看不到 hidden tests 和 gold patch；所有运行的 tracked worktree 均干净

| 组 | 机制 | Hidden pass | Wilson 95% CI | Tokens | 平均 tokens | 成本（RMB） |
|---|---|---:|---:|---:|---:|---:|
| A | single / model finish | 7/12 (58.3%) | 32.0%–80.7% | 4,214,901 | 351,242 | 8.7336 |
| B | single / verified auto-finish | 9/12 (75.0%) | 46.8%–91.1% | 2,945,377 | 245,448 | 6.1339 |
| C | Reviewer + at most one Reviser | 8/12 (66.7%) | 39.1%–86.2% | 4,084,183 | 340,349 | 8.5394 |

这是诊断性小样本，不支持显著性或因果结论。B 相比 A 的观察通过率高 16.7 个百分点，
token 变化 -30.1%；C 相比 B 的观察通过率低 8.3 个百分点，
token 变化 +38.7%。

## 逐任务结果

| Case | A pass / tokens | B pass / tokens | C pass / tokens |
|---|---:|---:|---:|
| `facebookresearch__hydra-1791` | 2/3 / 1,761,514 | 3/3 / 1,688,052 | 2/3 / 1,619,046 |
| `iterative__dvc-4124` | 2/3 / 545,754 | 3/3 / 255,267 | 2/3 / 345,932 |
| `iterative__dvc-4185` | 1/3 / 649,972 | 2/3 / 364,071 | 3/3 / 459,797 |
| `pydantic__pydantic-8977` | 2/3 / 1,257,661 | 1/3 / 637,987 | 1/3 / 1,659,408 |

## 机制分解

### B：确定性终止

- `verified_patch` 只触发 3/12 次，且都发生在 Pydantic；
  其中 hidden pass 为 1/3。
- 控制器证明的是“最新补丁之后某个可见 pytest 命令退出码为 0”，不是完整任务正确。
  两条隐藏测试失败说明终止证据仍可能过窄，不能把 `verified_patch` 命名理解为
  evaluator 已验证的补丁。
- B 的总 token 明显低于 A，但由于三组不是共享随机种子的配对实验，不能把全部
  token 差异或通过率差异都归因于控制器。

### C：Reviewer/Reviser

- Reviewer 直接通过 5 次，这些运行 hidden pass 为 5/5。
- Reviewer 给出负面结论 5 次，触发 Reviser 5 次；
  修订后 hidden pass 为 3/5。
- 另有 2 次因 Coder 没有候选补丁而跳过审查，均失败。
- C 没有超过 B，并额外消耗约 39% token；因此当前证据不支持默认开启
  Reviewer/Reviser。它更适合作为可选的补丁后检查机制，下一步应保证
  Reviser 失败时可回退到候选补丁，并分别测量 Reviewer 诊断精度与 Reviser 修复收益。

## 其他观察与限制

- 三组 pass-to-pass regression 标记均为 0；这只覆盖协议抽样的回归测试，不代表零回归。
- 有 1 条运行在一次模型响应后超过 700k 软上限；runner 在调用前检查预算，无法中断已经发出的响应。
- 基础设施中断不会写入正式 JSONL；正式 36 行均来自同一代码 revision 和三个固定协议哈希。
- 原始 trace 可能包含上游仓库源码和模型输出，但不包含 API key；账本提交前执行了凭据形态扫描。

## 可复现

```powershell
python -m benchmark.analyze_e2 --strict
```

该命令只读取已提交 JSONL，不调用模型、不启动 Docker。
