# A1 静态路径故障定位

输入：用户提供的 `mesh_algorithms_20260922-154026` 分析。
A static、A dynamic、B remaining、B critical 在两个规模均失败；reference 和 C 完成。
上传内容缺少原始 run.log，退出码本身不足以确定是分解、体生成、回传还是合并错误。
本提交只补定位和实验隔离，不声称已修复 A1 根因；不修改任务划分、调度评分、修复判据或 C 实现。

## 本轮运行

仍用 `build_project.sh` 编译，成功后运行 `strong_scaling/run_experiments.sh`。
默认 `WORKLET_ROUTES=a_static`，128/256 ranks 并行提交，资源布局、任务因子、细化参数不变。
每个规模保留 natural/split、一次质量预热和三次正式重复；单次失败继续后续轮次。
静态路径通过完整质量审计后再恢复动态和 B；本轮不需要重复运行 C。
原完整矩阵仍可在统一配置区显式设置：
`WORKLET_ROUTES="reference a_static a_dynamic b_remaining b_critical c_deferred"`。

## 失败记录

运行脚本将当前运行目录作为 `MESH_FAILURE_DIR` 传给进程。
任务异常在 MPI_Abort 前写入：

- `failure_rank_N.txt`：该 rank 捕获的原始异常和现场；
- `failure_reason.txt`：原子排他创建选出的一个异常报告，不宣称一定是全局因果上的首错；
- `route_a_static/pN/FAILURE_SUMMARY.txt`：运行级报告汇总。

字段为 rank、task、owner、executor、stage、ng_status、final_illegal、reason。
stage 覆盖 partition、split_worklets、task_geometry、task_surface、surface_refine、volume_generation、
task_fingerprint、worklet_return、return_verification、owner_face_map、owner_merge 等。
上下文保存在内存中；成功运行不逐任务写文件。诊断文件在异常路径写出并同步，不使用任何 MPI 集合通信。
task/owner/executor 为 -1 表示当前阶段尚未确定；生成状态字段仅在 volume_generation 阶段有判因意义。

若底层 MPI/内核直接终止而没有 C++ 异常，脚本保留退出码、分析错误及最多 8 KiB 日志尾部，
明确标为 `launcher_or_uninstrumented_failure`，不伪造阶段。
原 run.log 保留；诊断使用 .txt 文件，避免上传规则排除 .log 后再次丢失所有线索。
续跑失败项前，将上一份首报保存为 `previous_failure_reason.txt`，避免把旧错误当成新错误。
分析器允许只分析所选路线，并将失败原因带入 `route_issues.txt`；缺失其它路线不再产生无关告警。

## 判读顺序

1. 看 failure_reason 的 stage 和原始 reason，而不是根据 exit 15 推测。
2. volume_generation：区分非 NG_OK 返回和 final_illegal 非零，保留现有拒绝条件。
3. return_verification：检查序列化字段/指纹；owner_merge：检查界面及粗面父级。
4. 修复具体错误后，先要求静态预热审计和各正式轮完成，再恢复动态及关键性策略。

本地验证包括并发异常文件写入、不覆盖首报、单路线分析、失败原因传播、失败后继续运行、
原脚本回归及任务源码语法检查。仍不等价于集群 MPI/OCC 完整构建和真实网格验证。
