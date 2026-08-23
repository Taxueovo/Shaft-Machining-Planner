"""Cross-Encoder 重排序器。

在 BM25 + Vector 混合召回后，用 Cross-Encoder 对候选文档精排。
Cross-Encoder 对 (query, document) 联合编码打分，比双塔模型更精准。

模型：BAAI/bge-reranker-v2-m3（中文优化，约 2.2GB，首次加载下载模型）
降级：模型加载失败 → 跳过 rerank，用 RRF 分数排序
"""

from __future__ import annotations

import logging
import os
import threading
from typing import Optional

# 默认离线加载本地缓存的模型，避免每次首次加载访问 HF Hub 超时（175s+）。
# 本地无缓存时 _get_model 会临时关闭离线模式做在线加载。
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

from .config import (
    RERANKER_CLOUD_API_KEY,
    RERANKER_CLOUD_MODEL,
    RERANKER_CLOUD_URL,
    RERANKER_ENABLED,
    RERANKER_MODEL,
)
from .schemas import SearchResult

logger = logging.getLogger(__name__)

# ── 云端精排（DashScope 兼容 rerank API）──
# 优先级：RERANKER_CLOUD_API_KEY → EMBEDDING_API_KEY（与 embedding 共用 DashScope key）
# 云端不可用时自动回退本地 CrossEncoder。


def cloud_rerank_available() -> bool:
    """云端精排是否可用（配置了模型名 + API key + httpx 可导入）。"""
    if not RERANKER_CLOUD_MODEL:
        return False
    key = RERANKER_CLOUD_API_KEY or os.getenv("EMBEDDING_API_KEY", "")
    if not key:
        return False
    try:
        import httpx  # noqa: F401

        return True
    except ImportError:
        return False


def _cloud_rerank(query: str, documents: list[str]) -> list[float]:
    """调用云端 rerank，返回与 documents 对齐的相关性分数列表。

    失败时抛异常，由调用方回退本地 CrossEncoder。
    """
    import httpx

    key = RERANKER_CLOUD_API_KEY or os.getenv("EMBEDDING_API_KEY", "")
    body = {
        "model": RERANKER_CLOUD_MODEL,
        "input": {"query": query, "documents": documents},
        "parameters": {"return_documents": False},
    }
    resp = httpx.post(
        RERANKER_CLOUD_URL,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json=body,
        timeout=60,
    )
    resp.raise_for_status()
    results = resp.json()["output"]["results"]
    scores = [0.0] * len(documents)
    for r in results:
        scores[r["index"]] = r["relevance_score"]
    return scores


# ── 模型全局缓存（加载一次） ──

_reranker_model: Optional[object] = None
_reranker_available: Optional[bool] = None
_model_lock = threading.Lock()


def reranker_available() -> bool:
    """检查 Cross-Encoder 重排序是否可用。"""
    global _reranker_available
    if _reranker_available is not None:
        return _reranker_available

    if not RERANKER_ENABLED:
        _reranker_available = False
        return False

    try:
        import sentence_transformers  # noqa: F401

        _reranker_available = True
    except ImportError:
        logger.warning("sentence-transformers 未安装，reranker 不可用")
        _reranker_available = False
    return _reranker_available


def _get_model() -> Optional[object]:
    """延迟加载 Cross-Encoder 模型（加锁避免并发下重复下载/加载）。"""
    global _reranker_model, _reranker_available

    if not reranker_available():
        return None

    if _reranker_model is not None:
        return _reranker_model

    with _model_lock:
        if _reranker_model is not None:
            return _reranker_model
        try:
            from sentence_transformers import CrossEncoder

            logger.info("Loading Cross-Encoder model: %s ...", RERANKER_MODEL)
            try:
                # 本地已有模型时优先离线加载，避免访问 HF Hub 超时
                _reranker_model = CrossEncoder(RERANKER_MODEL, local_files_only=True)
            except Exception as local_exc:
                # 本地无模型时临时关闭离线模式做在线下载
                logger.debug("Local reranker load failed (%s), trying online load...", local_exc)
                os.environ.pop("HF_HUB_OFFLINE", None)
                os.environ.pop("TRANSFORMERS_OFFLINE", None)
                try:
                    _reranker_model = CrossEncoder(RERANKER_MODEL)
                finally:
                    os.environ.setdefault("HF_HUB_OFFLINE", "1")
                    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
            logger.info("Cross-Encoder model loaded: %s", RERANKER_MODEL)
            return _reranker_model
        except Exception as exc:
            logger.warning(
                "Failed to load reranker model '%s': %s. Reranking disabled.", RERANKER_MODEL, exc
            )
            _reranker_available = False
            return None


# ── 特征关键词映射（查询特征词 → 候选 features 英文关键词）──
# 语义精排偏重"材料和主题相似"，常漏掉查询里的判别性特征词（如"螺纹/花键/孔"），
# 把不含该特征的近重复案例排到第一。此处做确定性特征命中检查：候选缺失查询里的
# 关键特征词时降权，弥补语义模型的判别力不足（实测 hit@1 +0.27、MRR +0.17）。
#
# 键同时覆盖中文查询词（"键槽"）与工作流构造的英文特征标签（"keyway"），
# 否则生产查询路径（英文特征名）永远触发不了该惩罚。匹配均为大小写不敏感。

FEATURE_KEYWORDS: dict[str, list[str]] = {
    "键槽": ["keyway"],
    "keyway": ["keyway"],
    "轴承位": ["bearing"],
    "bearing_seat": ["bearing"],
    "bearing": ["bearing"],
    "花键": ["spline"],
    "spline": ["spline"],
    "齿形": ["gear", "worm", "helical", "pinion"],
    "gear_teeth": ["gear", "helical", "pinion"],
    "gear": ["gear"],
    "worm": ["worm"],
    "螺纹": ["thread"],
    "thread": ["thread"],
    "孔": ["hole", "bore"],
    "hole": ["hole", "bore"],
    "bore": ["bore"],
    "锥面": ["taper"],
    "taper": ["taper"],
    "槽": ["groove"],
    "groove": ["groove"],
    "密封位": ["seal"],
    "seal_area": ["seal"],
    "seal": ["seal"],
    "扁位": ["flat"],
    "flat": ["flat"],
    "滚花": ["knurl"],
    "knurl": ["knurl"],
    "法兰": ["flange"],
    "flange": ["flange"],
    "镀铬": ["chrome", "plated"],
    "辊": ["roller", "roll"],
}
FEATURE_PENALTY: float = 0.3  # 每个缺失关键特征的扣分


def _match_feature_aliases(query: str) -> set[frozenset[str]]:
    """Return the set of matched feature keyword-sets, deduplicated across aliases."""
    q = query.lower()
    matched: set[frozenset[str]] = set()
    for alias, kws in FEATURE_KEYWORDS.items():
        if alias.lower() in q:
            matched.add(frozenset(kws))
    return matched


def _apply_feature_penalty(
    query: str,
    candidates: list[SearchResult],
) -> list[SearchResult]:
    """确定性特征降权：候选缺失查询里的关键特征词时扣分。

    只对案例库候选生效（metadata 带 features 字段）；规范候选跳过。
    无关键特征词可解析时原样返回。
    """
    matched = _match_feature_aliases(query)
    if not matched:
        return candidates

    for c in candidates:
        feats_str = c.metadata.get("features")
        if not feats_str:  # 规范 chunk 无 features 字段，不参与特征降权
            continue
        feats_lower = feats_str.lower()
        miss = sum(1 for kws in matched if not any(k in feats_lower for k in kws))
        if miss > 0:
            c.score = round(c.score - FEATURE_PENALTY * miss, 4)
            if c.rerank_score is not None:
                c.rerank_score = round(c.rerank_score - FEATURE_PENALTY * miss, 4)

    candidates.sort(key=lambda r: r.score, reverse=True)
    return candidates


def rerank(
    query: str, candidates: list[SearchResult], top_k: Optional[int] = None
) -> list[SearchResult]:
    """对候选文档用 Cross-Encoder 精排。

    Parameters
    ----------
    query : str
        查询文本。
    candidates : list of SearchResult
        候选文档列表（来自 BM25+Vector 召回）。
    top_k : int, optional
        返回前 top_k 个结果。默认返回全部。

    Returns
    -------
    list of SearchResult
        按 cross-encoder 得分降序排列的结果。
        如果 reranker 不可用，返回原始顺序（降级）。
    """
    if not candidates:
        return []

    # ── 1. 语义精排（云端优先，本地 CrossEncoder 回退）──
    scores: Optional[list[float]] = None
    if cloud_rerank_available():
        docs = [c.content for c in candidates]
        try:
            scores = _cloud_rerank(query, docs)
        except Exception as exc:
            logger.warning("Cloud rerank failed (%s), falling back to local CrossEncoder.", exc)
            scores = None

    if scores is None:
        model = _get_model()
        if model is not None:
            pairs = [(query, c.content) for c in candidates]
            logger.debug("Reranking %d candidates...", len(pairs))
            try:
                scores = [float(s) for s in model.predict(pairs)]
            except Exception as exc:
                logger.warning("Reranker prediction failed: %s. Returning as-is.", exc)
                scores = None

    if scores is not None:
        for i, score in enumerate(scores):
            candidates[i].rerank_score = round(float(score), 4)
            candidates[i].score = round(float(score), 4)

    # ── 2. 确定性特征降权（弥补语义模型的判别力不足）──
    candidates = _apply_feature_penalty(query, candidates)

    logger.debug(
        "Rerank complete: %d -> %d results",
        len(candidates),
        len(candidates[:top_k]) if top_k else len(candidates),
    )

    if top_k:
        return candidates[:top_k]
    return candidates
