"""B 臂 Embedding 召回器测试（Step 5-B2）：真实模型（本地缓存）小规模推理。

模型 bge-small-zh-v1.5 已下载至 data/models/fastembed（不入 Git）；
fastembed 缺失时整组跳过（CI/无依赖环境）。
"""
import pytest

from backend.app.annotation.recall import REPO_MODELS_DIR

pytest.importorskip("fastembed", reason="B 臂依赖未安装时跳过（设计文档 §13）")

KP_ROWS = [
    {"kp_id": "kp_p_005", "name": "鸡兔同笼",
     "description": "两类对象总头总脚反推数量"},
    {"kp_id": "kp_p_007", "name": "植树问题",
     "description": "棵数与间隔数的线性关系"},
    {"kp_id": "kp_p_076", "name": "百分数运算",
     "description": "含百分数的运算"},
]


@pytest.fixture(scope="module")
def recaller():
    from backend.app.annotation.recall import EmbeddingRecaller
    return EmbeddingRecaller(KP_ROWS, cache_dir=REPO_MODELS_DIR, top_k=2)


def test_top1_sanity_chicken_rabbit(recaller):
    """构造题干 → top-1 命中自证（设计文档 §13 单测方案①）。"""
    assert recaller.recall("q_t1", "鸡和兔关在同一个笼子里，数头共 10 个，数脚共 28 只，鸡兔各有几只？")[0] == "kp_p_005"


def test_topk_size_and_membership(recaller):
    cands = recaller.recall("q_t2", "路的一侧植树，每隔 5 米栽一棵，两端都栽，一共栽了 20 棵")
    assert len(cands) == 2  # top_k=2
    assert "kp_p_007" in cands
    assert cands[0] == "kp_p_007"


def test_all_ids_within_pool(recaller):
    cands = recaller.recall("q_t3", "某商品打八折出售，现价 80 元，原价多少元？")
    assert len(cands) == 2
    assert all(c in {"kp_p_005", "kp_p_007", "kp_p_076"} for c in cands)


def test_empty_stem_still_returns_candidates(recaller):
    cands = recaller.recall("q_t4", "")
    assert len(cands) == 2  # 空题干仍有最近候选（B 臂 prompt 声明候选可能不全）
