# 旋转链实验：先独立编译，再提交测试

本页记录上一版流程；当前已接回原来的 `build_project.sh` 与 `run_experiments.sh`，请使用[现行动态整规则链说明](20261105_dynamic_rule_chains.md)中的命令。

此前两个批次都在计算节点找不到 CMake 时退出，未执行实验。将编译搬进测试作业改变了原有流程；现在恢复为用户自行编译、随后提交测试。

## 执行顺序

在你平时使用的超算编译环境中，进入更新后的仓库根目录：

```bash
bash test_code_expansion/tests/build_rotation_chain_suite.sh
```

看到“编译完成”后，再提交测试：

```bash
bash test_code_expansion/tests/run_rotation_chain_suite.sh --submit
```

编译脚本只生成独立实验内核和三个实验程序，不执行测试。它保留当前已加载模块，使用 GCC 12；不会执行模块清空操作。CMake 只需在编译环境可用。如果该环境也找不到工具，请在编译命令后加 `--cmake /实际安装路径/bin/cmake`，不要再次提交空作业。

提交前检查源码、三个程序及实验运行库摘要，未编译或源码更新时立即提示重新编译。计算作业只运行已有程序，不查找或调用 CMake、make 和编译器。两条命令若使用 `--build-dir`，须指定相同目录。

## 测量与结果

仍为单节点四核，测试任务分配、旋转间汇合及数据准备成本。四路线、配对计时和分项探针保持；普通计时与插桩测量分别使用预编译程序，不属于多节点扩展实验。

- 分配：队列构造、领任务累计耗时与次数、任务启动延迟。
- 汇合：任务结束到主线程汇合返回的间隔，不能当作 CPU 实际空闲时间。
- 准备：输入准备、槽位复制、规则准备；重叠区间不相加。

成功编译清单位于 `build/rotation_chain_costs_v2/rotation_chain_build.json`，编译日志在同目录 `compile_logs/`。为避免编译主机与计算节点指令集差异，关闭本机专用指令优化；性能必须在本批次内部配对比较。

作业日志在 `test_code_expansion/strong_scaling_results/rotation_chain_jobs/`。结果目录生成 `measurement_report.md`（成本分解报告）、逐次记录和 `summary.json`；只有 `complete=true` 才表示整套测试完成。

此次仅修改代码并阅读复核，未在助手环境编译、运行测试或提交超算作业。后续默认不在助手环境验证，由用户编译和提交集群运行；不使用旧版结果宣称新版通过。
