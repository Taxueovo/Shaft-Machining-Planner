"""RAG 模块配置中心。

所有路径、collection 名称、分块参数集中管理。
Embedding 模型使用独立的环境变量，与主 LLM 分离。
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# ── 模块根目录 ──

MODULE_DIR = Path(__file__).resolve().parent
DATA_DIR = MODULE_DIR / "data"
SPECS_DIR = DATA_DIR / "specs"
CASES_DIR = DATA_DIR / "cases"
CHROMA_DIR = DATA_DIR / "chroma"

# ── Embedding 模型配置（独立于主 LLM） ──

EMBEDDING_BASE_URL: str = os.getenv("EMBEDDING_BASE_URL", "")
EMBEDDING_API_KEY: str = os.getenv("EMBEDDING_API_KEY", "")
EMBEDDING_MODEL: str = os.getenv("EMBEDDING_MODEL", "text-embedding-3-small")
# 向量维度（可选）：0 = 用模型默认。text-embedding-v4 支持 2048/1536/1024/768 等。
EMBEDDING_DIMENSIONS: int = int(os.getenv("EMBEDDING_DIMENSIONS", "0")) or 0

# ── ChromaDB 配置 ──

COLLECTION_SPECS: str = "costpilot_specs"
COLLECTION_CASES: str = "costpilot_cases"

# ── 支持的文件扩展名 ──

SPEC_EXTENSIONS: set[str] = {".md", ".txt", ".rst"}
CASE_EXTENSIONS: set[str] = {".json"}

# ── 规范库分块参数 ──

SPEC_CHUNK_SIZE: int = 1200  # 每个 chunk 最大字符数
SPEC_CHUNK_OVERLAP: int = 150  # 相邻 chunk 重叠字符数
SPEC_MIN_CHUNK_SIZE: int = 80  # 低于此大小的 chunk 合并到上一个

# ── 案例库分块参数 ──

CASE_CHUNK_AS_WHOLE: bool = True  # 每个案例作为一个完整 Chunk（保持结构完整性）

# ── 混合检索参数 ──

HYBRID_TOP_K_RECALL: int = 10  # 每路（BM25/Vector）召回候选数
HYBRID_TOP_K_FINAL: int = 5  # 最终返回结果数
RRF_K: int = 60  # RRF 平滑参数（越大越平滑，默认 60）

# ── Reranker 配置 ──

RERANKER_ENABLED: bool = True
RERANKER_MODEL: str = os.getenv("RERANKER_MODEL", "BAAI/bge-reranker-v2-m3")
# 云端精排（DashScope 兼容接口）。设置模型名即启用，走 HTTP 不下载；
# 为空则使用本地 CrossEncoder（RERANKER_MODEL）。
RERANKER_CLOUD_MODEL: str = os.getenv("RERANKER_CLOUD_MODEL", "")
RERANKER_CLOUD_URL: str = os.getenv(
    "RERANKER_CLOUD_URL",
    "https://dashscope.aliyuncs.com/api/v1/services/rerank/text-rerank/text-rerank",
)
RERANKER_CLOUD_API_KEY: str = os.getenv("RERANKER_CLOUD_API_KEY", "")


def embedding_available() -> bool:
    """检查 embedding 模型是否已配置。"""
    return bool(EMBEDDING_API_KEY)
