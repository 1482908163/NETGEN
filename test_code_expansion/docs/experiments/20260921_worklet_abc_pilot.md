# 固定归属 A1 / 关键性 B1 / 延迟编号 C1：首轮实现

基于 `agent/load-balance-research` 的 `6e7addd498d60019ec6462c184f1f7891c147b9b`，按交接文档继续负载均衡主线。
本次不修改 Netgen 内核 API，不恢复 node_elastic 的细粒度启发式，也不引入离线训练。

## 一次提交的实验

先在集群按原有构建流程重编译 `test_code_expansion`（链接当前分支已有的 repair_v2 nglib），然后：

```bash
cd test_code_expansion/strong_scaling
EXPERIMENT_PRESET=worklets bash run_experiments.sh
```

默认预设已切换为 worklets。128/256 ranks 两个作业并行提交；每节点 4 ranks，每 rank 4 物理核。
每个作业内部按重复轮交错、轮换六组顺序，L2/R2，natural/split，一轮预热和三轮正式测量。
每组 natural repeat_0 做全量体网格审计、稀疏面参考校验、全局编号指纹检查。
单组失败继续其它组，结果保留并支持原配置续跑。正式计时不包含质量审计。
可先用 `DRY_RUN=1` 检查资源申请。默认不启动十亿级生产实验；先确认正确性、内存和回传成本。

| 结果目录 | 变化 | 对照目的 |
| --- | --- | --- |
| route_reference | sparse + parallel repair，原分区 | 已验证基础配置 |
| route_a_static | 每个原分区最多 4 个封闭任务，固定执行 | 量化任务分解及保留派发进程的成本 |
| route_a_dynamic | 同一任务分解，可改派执行，结果回原属主 | 隔离执行负载均衡收益 |
| route_b_remaining | 完成时间在线更新剩余工作量估计 | 对照仅按任务重量选择 |
| route_b_critical | 剩余工作量加邻居同步负裕量估计 | 对照剩余工作量策略 |
| route_c_deferred | 原分区，不启用任务；合并并重叠编号计数 | 独立量化同步依赖变化 |

统一资源布局的 route_reference 不等于历史不同 ranks/node 的结果；只做同一矩阵内的配对比较。
每个 `pN/ROUTE_SUMMARY.txt`、`route_comparisons.csv`、`route_issues.txt` 给出跨路线结果；
每条路线自身保留原有 analysis、原始压缩 profile、日志、quality_summary。
手动重分析：`python3 analyze_worklet_routes.py <结果根目录> --ranks 128`。
不要用原通用分析器直接对整个 route 根目录汇总，否则同名 sparse 会混合不同候选。

## A1：归属与执行分离

先生成与 reference 相同的 P 分区，再在每个分区内确定性 BFS 切分粗单元。
每个原分区生成 `min(4, 粗单元数)` 个任务，因此不会把一个粗单元改归其它 rank。
分解、生成、回传、合并均在核心计时区间内。任务调用已有 `Ng_GenerateVolumeMeshRepair(...,2,...)`。

属主包含 rank 0，所有原分区都保留；但 A1/B1 仍保留 rank 0 做集中派发，只有 P−1 个生成工作进程。
静态对照中，属主 0 的任务由工作进程 1 生成后回传。动态候选共用同节点、相邻属主、全局三级候选次序。
这不是去中心化工作窃取，不能用它声称完成 A2 或已经具备 8192 ranks 扩展能力。

结果使用 MPI_INT/MPI_DOUBLE 的显式字段传输，单次消息按 2^20 元素分块，避免总字节数溢出 MPI int。
回传重建点、四面体、域、面描述符、粗顶点映射、重心坐标及表面三角形。
核对数量和覆盖这些字段的任务指纹，按任务 ID 排序合并，删除同属主同域的任务内部面并合并界面顶点。
原分区归属签名、执行任务总数、属主接收任务总数和通信图不变性都进入分析器校验。

当前结果在生成完后集中回传；占用内存包含未回传任务及一个序列化缓冲区，需看峰值而不能只看最终网格规模。
ordered return 不是流式回收；下一轮是否推进 A2，要根据派发、回传、合并的实测占比决定。

## B1：相同分解上的调度消融

dynamic 使用粗单元数代理任务工作量；remaining 使用完成反馈的指数滑动时间/工作量估计，
包含 pending 和 in-flight 的剩余时间；critical 加上相对邻居最早预计就绪时间的负裕量。
这只是下一同步点阻塞风险的代理模型，不是已经测得的流水线 deadline，也没有宣称训练好了精确耗时模型。
四种策略的任务边界、最终归属、线程数和候选层级相同。
分析器要求调度对照的任务网格签名一致；若内核或执行顺序导致不确定性，标记为未验证，不把网格变化当加速。

## C1：owner/local ID 先交换，最终编号后提交

旧路径先分别 Allgather 顶点数、单元数，之后才准备和交换邻居顶点。
新路径启动一次包含两个 int64 计数的 Iallgather，准备并交换原本已有的 owner-local ID，
然后 Wait、检查 64 位前缀偏移溢出、生成连续最终编号，再返回。
owner 由消息源给出，local ID 保持 int64，不把 rank 与 local ID 强行打包到有限位宽。
最终 vertex/element ID 的语义和输出格式不变；尚未移除体单元邻接阶段的其它全局通信。

新增 `id_count_begin`、`id_count_commit_wait`、`id_compaction`；全部在原核心时间边界内。
split 模式在启动前插入诊断 barrier，natural 不插入；split 不用于正式性能结论。
不以 Wait 很短作为加速证据，只比较 natural 的 core_seconds，并检查编号/网格指纹。

## 正确性与后续门槛

- 全量质量预热覆盖索引、方向、面连接、边界指纹、形状分布及编号；不证明任意非相邻单元不相交或 CAD 距离。
- A 的分解可能改变内部四面体数量和质量分布。保守 compare_quality 会标记需要复核，不能自动豁免。
- A/B 同分解调度必须通过数量、原归属、任务/边界指纹及质量对照；C 必须保持原网格和最终编号。
- 首轮通过后，再扩大为 1024/2048，最后 4096/8192；不要只凭纯调度测试跳过 MPI/OCC 真实网格验证。
- 参数如 `WORKLET_FACTOR=2` 或更高精细度是下一轮消融，不与本轮结果混在同一续跑目录。

## 本地验证边界

交付时执行纯 C++ 单元测试、调度随机顺序测试、shell/Python 检查及 MPI/OCC 声明替身下的源文件语法检查。
声明替身不模拟真实 MPI 进展、分布式死锁、Netgen 几何行为或 ABI；不等价于集群完整链接与实验。
正式性能、回传内存峰值和三条路线的真实收益必须由上述实验给出。
