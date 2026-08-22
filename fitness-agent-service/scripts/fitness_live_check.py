"""执行 Fitness Agent 训练计划草案真实确认流程和可选写入联调。

默认只验证训练计划创建请求能进入 Fitness 路由并生成待确认单，不会写入训练服务。
显式 ``--execute`` 后才会批准并执行创建草案；发布、审核和学员执行应使用不同角色
的专门验收用例，不能把组织管理员的一次写入误认为完整业务闭环。
"""

from __future__ import annotations

import argparse

from business_live_check_support import add_common_arguments, run_main


def _arguments() -> tuple[argparse.Namespace, str, str, str]:
    parser = argparse.ArgumentParser()
    add_common_arguments(parser, description="Fitness Agent 训练计划真实联调")
    return (
        parser.parse_args(),
        "AGENT_FITNESS_LIVE_MESSAGE",
        "FITNESS_COACHING",
        ("CREATE_TRAINING_DRAFT",),
    )


if __name__ == "__main__":
    raise SystemExit(run_main(_arguments, label="Fitness"))
