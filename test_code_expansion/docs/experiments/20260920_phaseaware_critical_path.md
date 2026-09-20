# 关键路径阶段感知借核迭代

## 依据

批次 `mesh_algorithms_20260915-125527` 的 p256 关键路径报告显示：

- `node_fixed` 与 `node_elastic` 的最慢计算进程连续 5 次均为 rank 155。
- `node_elastic` 相对 `node_fixed`：关键进程计算 1.842s→1.878s；final optimization 0.210s→0.184s，但 generation 1.436s→1.482s、domain repair 0.100s→0.146s。
- `front` 约 0.52s，源码为串行 advancing-front 循环，没有可由现有 TaskManager 扩线程利用的并行区。
- 因此继续向 generation/front 泛化借核缺乏代码依据；高进程数应避免在无收益阶段重建线程组。

## 新策略 node_phaseaware

保持网格归属、通信、拓扑操作顺序和基础 4 核不变。

- phase 1（Delaunay optimization）、phase 7（grouped repair）等 generation/repair 阶段固定使用基础核，不借核。
- phase 5（SwapImprove2）保持原有单线程约束。
- 仅 phase 6（final optimization）允许使用已经完成的同节点 rank 释放的核。
- phase 6 入口可直接取得已释放核；运行中的后续增核使用既有 remaining-work / restart-cost 门槛。
- 二次增核沿用节点内剩余工作优先选择，每进程最多一次，预留后再重建线程组。
- 不抢占未完成 rank 的保底核，不迁移 mesh，不新增 MPI collective。

该策略是 `node_elastic` 的受限版本；若无可用核或收益不足，会保持基础核执行。

## 本轮实验

统一入口默认比较：

`node_fixed node_elastic node_phaseaware`

规模：

`64 128 256`

natural/split，各 1 次预热 + 5 次正式，共 108 次运行。natural 预热继续执行质量审计。

主要判据：

1. p256 `node_phaseaware` 是否消除 `node_elastic` 的退化；
2. phase 0–5、7 的 borrowed core seconds 必须为 0；
3. p64/p128 不应出现明显回退；
4. 同规模质量审计和网格指纹必须通过；
5. 以 natural 同重复编号胜出次数和中位数为主要性能依据，split 只解释等待机制。
