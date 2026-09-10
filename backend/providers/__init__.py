# 包入口：组织当前模块命名空间；已有导出用于保持上层导入方式稳定。
"""Knowledge providers used by engineering decision nodes.

Workflow nodes depend on these provider interfaces rather than on individual
Excel sheets or hard-coded manufacturing rules.  Providers may later be
backed by a database or a reviewed knowledge service without changing the
workflow topology.
"""

from .heat_treatment import HeatTreatmentProvider

__all__ = ["HeatTreatmentProvider"]
