"""pytest 配置：把仓库根加入 sys.path，使测试能以 backend.* 顶层包导入（Phase 0/I1 统一导入路径）。"""
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


# ===== Phase 2/S3：检索层测试替身 =====
# workflow / citations 等消费方测试必须离线（不加载真实 embedding 模型、
# 不依赖真实库数据）——统一通过本 fixture 把工具层的检索单例替换为 Fake。

import pytest


class FakeEmbedder:
    """确定性 embedding 替身（hash 向量，形状与 bge 一致：512d float32 L2 归一化）。"""

    dimension = 512

    def _vec(self, text: str):
        import hashlib

        import numpy as np
        seed = int(hashlib.md5(str(text).encode("utf-8")).hexdigest(), 16)
        rng = np.random.default_rng(seed % (2**32))
        v = rng.standard_normal(self.dimension).astype("float32")
        return v / max(float(np.linalg.norm(v)), 1e-12)

    def embed_query(self, text: str):
        return self._vec(f"Q::{text}")

    def embed_documents(self, texts):
        import numpy as np
        return np.stack([self._vec(f"D::{t}") for t in texts])


class FakeKpService:
    """检索服务替身：固定返回 2 个带 ref 的 KP（消费方契约：ref 必在）。"""

    _KPS = [
        {"kp_id": "kp_j7_0001", "ref": "kp_j7_0001", "name": "一元二次方程",
         "stage": "junior", "grade": 7, "chapter": "一元二次方程",
         "description": "形如 ax^2+bx+c=0 的方程", "score": 0.9, "via": "both"},
        {"kp_id": "kp_j7_0002", "ref": "kp_j7_0002", "name": "一元二次方程的判别式",
         "stage": "junior", "grade": 7, "chapter": "一元二次方程",
         "description": "用判别式判断根的情况", "score": 0.7, "via": "vector"},
    ]

    def search_knowledge_points(self, query, stage=None, top_k=10):
        return self._KPS[:top_k]

    def search_questions(self, query, stage=None, filters=None, **kwargs):
        return {"kps": self._KPS[:3], "questions": [], "meta": {}}


@pytest.fixture()
def fake_retrieval(monkeypatch):
    """把工具层的检索单例替换为 FakeKpService（离线；按需显式使用或模块级 usefixtures）。"""
    import backend.app.tools.knowledge_tools as kt

    monkeypatch.setattr(kt, "get_retrieval_service", lambda: FakeKpService())
    return FakeKpService()


@pytest.fixture(autouse=True)
def _isolated_task_store(tmp_path, monkeypatch):
    """S5 起任务/会话持久化到 SQLite——所有测试隔离到临时库
    （不写真实 data/db/edumate.sqlite；settings.DB_PATH 注入 + task_store.reset）。"""
    from backend.app.agent.context import task_store
    from backend.config import settings

    monkeypatch.setattr(settings, "DB_PATH", str(tmp_path / "tasks.sqlite"))
    task_store.reset()
    yield
    task_store.reset()


@pytest.fixture(autouse=True)
def _fake_api_key(monkeypatch):
    """CI/无 .env 环境：注入测试用 Key（LLM 全部 Mock，不发起真实调用）。"""
    from backend.config import settings
    monkeypatch.setattr(settings, "DEEPSEEK_API_KEY", "test-key", raising=False)


@pytest.fixture(autouse=True)
def _auth_token(monkeypatch):
    """S6 起 API fail-closed——测试统一注入 test token（配合各 TestClient 的 AUTH 头）。"""
    from backend.app.api.security import limiter
    from backend.config import settings
    monkeypatch.setattr(settings, "EDUMATE_API_TOKEN", "test-token")
    limiter._hits.clear()  # 限流桶按测试隔离（模块级单例会跨用例累积）


# 测试客户端统一携带的认证头（与 _auth_token 注入值一致）
AUTH_HEADERS = {"X-API-Token": "test-token"}
