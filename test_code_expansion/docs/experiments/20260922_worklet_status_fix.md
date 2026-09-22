# A1 体生成状态判据修复

## 实验依据

`mesh_algorithms_20260922-163623` 的 16 次静态任务运行均在 volume_generation 阶段中止。
异常报告中 ng_status=0（NG_OK），final_illegal 为正；例如 p256 的 task560 为 187。
直接触发点是任务路径额外加入的 `details[11]!=0`，并不是这些报告中的内核失败返回值。

对照 `mesh_algorithms_20260922-154026/route_reference/p256`：
analysis/runs.csv 的六次正式运行 kernel_final_illegal 均为 51809；
sparse_natural/repeat_0/quality_summary.json 检查了 50393664 个四面体，hard_errors=0。
注意：正式计数与预热审计来自不同运行，不能据此证明任务网格正确。

内核 GenerateVolumeKernelImpl 在 MeshVolume 或 OptimizeVolume 失败时返回 NG_VOLUME_FAILURE，
将 MarkIllegalElements 的结果写入 details[11] 后返回 NG_OK。
参考路径保留该计数作为诊断，并使用独立完整网格审计。因此任务路径不能单独将该计数当成 API 失败。

## 修改及正确性边界

任务路径只根据非 NG_OK 返回立即抛出生成失败；仍记录全部 12 项诊断及异常现场。
完整 volume_audit、任务覆盖、回传指纹、数量与所有权检查保留。
新增分析回归覆盖非零诊断计数正常汇总、有效审计可完成，以及缺失表面导致完成检查失败且不写 SUCCESS。
这修复已确认的中止条件；不声称已验证任务合并后网格或取得加速。

## 下一次运行

更新分支后，按原流程运行 build_project.sh，再运行 strong_scaling/run_experiments.sh。
继续默认 a_static、128/256 ranks、原资源与细化参数、质量预热及三次正式重复。
不复用旧二进制。先看 repeat_0/quality_summary.json 和 route_issues.txt；失败时保留 failure_reason.txt。
静态路径通过后，再恢复动态 A 与 B 对照；C 的现有实现不变。

本地通过任务源码语法检查（真实 nglib 头文件、MPI/OCC 声明替身）及 A/B/C 分析回归。
当前环境没有集群 MPI/OCC 运行条件；上述检查不等价于完整构建及真实网格实验。
