"""检索失败逐阶段诊断（Phase 2 缺陷定位；只读，不修复）。

对正式基线 stage_filtered 协议下 primary 未进 top-5 的 23 条样本，重放知识点
召回内部各阶段（不重跑全量评测、不改运行时代码/数据/指标）：
- FTS 关键词候选：是否命中、gold 排名/分数、命中 KP 数；
- 向量候选：gold 余弦排名/分数（向量路覆盖当前学段全部 KP）；
- 融合：α=0.4 归一化融合后 gold 排名/分数；
- 最终：名称直配 +0.05 后 gold 排名/分数；
- top-5 最终结果与 top-1 错误占位 KP；
- 7 条 6–10 样本额外做 α=0.3/0.5 与“无名称加分”反事实排名；
- 5 个错误占位 KP 的描述/关键词/召回路分析。

说明：正式基线指标评估的是**知识点召回**（search_knowledge_points），其排序
特征只有「关键词归一化 + 向量归一化 + 名称加分」；题目检索的 Jaccard/难度/FTS
重排不在此路径，故本报告不把题目重排缺陷计入这 23 条样本（另行记录）。

输出（staging，不入 Git）：
    data/staging/retrieval_failure_diagnosis.md
    data/staging/retrieval_failure_diagnosis.json

用法：
    .venv/Scripts/python.exe -m backend.scripts.diagnose_retrieval_failures
"""
import json
import sys
from collections import Counter
from pathlib import Path

from backend.app.retrieval.kp_recall import KpRetriever, RETRIEVAL_ALPHA, _minmax
from backend.app.retrieval.tokenize import fts_query, segment

REPO_ROOT = Path(__file__).resolve().parents[2]
STAGING = REPO_ROOT / "data" / "staging"
FORMAL = STAGING / "retrieval_eval_queries_formal_v1.jsonl"
BASELINE = STAGING / "retrieval_baseline_report.json"
OUT_MD = STAGING / "retrieval_failure_diagnosis.md"
OUT_JSON = STAGING / "retrieval_failure_diagnosis.json"

PLACEHOLDER_KPS = ["kp_p_062", "kp_p_061", "kp_p_010", "kp_p_007", "kp_p_059"]
UNIT_TOKENS = ["吨", "千克", "克", "升", "毫升", "立方米", "千米", "米", "分米", "厘米", "毫米"]


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def rank_of(scores: dict, kp_id: str) -> tuple[int | None, float | None, int]:
    """返回 (rank, score, total)：按分数降序。"""
    ordered = sorted(scores.items(), key=lambda x: -x[1])
    total = len(ordered)
    for i, (kid, score) in enumerate(ordered, 1):
        if kid == kp_id:
            return i, round(float(score), 6), total
    return None, None, total


def top_n(scores: dict, name_map: dict, n: int = 5) -> list[dict]:
    ordered = sorted(scores.items(), key=lambda x: -x[1])[:n]
    return [{"kp_id": kid, "name": name_map.get(kid, ""), "score": round(float(s), 4)}
            for kid, s in ordered]


def final_scores(kw_norm: dict, vec_norm: dict, alpha: float, name_map: dict,
                 tokens: list[str], apply_bonus: bool) -> dict[str, float]:
    union = set(kw_norm) | set(vec_norm)
    fused = {kid: alpha * kw_norm.get(kid, 0.0) + (1 - alpha) * vec_norm.get(kid, 0.0)
             for kid in union}
    if apply_bonus:
        for kid in list(fused):
            name = name_map.get(kid, "")
            if any(t and t in name for t in tokens):
                fused[kid] += 0.05
    return fused


def main() -> int:
    formal = {q["query_id"]: q for q in load_jsonl(FORMAL)}
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    rows = baseline["per_query"]["stage_filtered"]
    failures = [r for r in rows if not r["recall5_primary"]]
    if len(failures) != 23:
        raise SystemExit(f"失败样本数异常：{len(failures)}（期望 23）")

    ret = KpRetriever()
    name_map = {kid: meta["name"] for kid, meta in ret._kp_meta.items()}
    samples = []
    try:
        for row in failures:
            q = formal[row["query_id"]]
            text = q["query_text"]
            stage = q["stage"]
            gold = q["gold_primary"]
            tokens = segment(text)
            fts_q = fts_query(text)

            kw_raw = ret._keyword_scores(text, stage)
            vec_raw = ret._vector_scores(text, stage)
            kw_norm = _minmax(kw_raw)
            vec_norm = _minmax(vec_raw)

            kw_rank, kw_score, kw_total = rank_of(kw_raw, gold)
            vec_rank, vec_score, vec_total = rank_of(vec_raw, gold)
            fused_pre = final_scores(kw_norm, vec_norm, RETRIEVAL_ALPHA, name_map,
                                     tokens, apply_bonus=False)
            fused_rank, fused_score, _ = rank_of(fused_pre, gold)
            final = final_scores(kw_norm, vec_norm, RETRIEVAL_ALPHA, name_map,
                                 tokens, apply_bonus=True)
            final_rank, final_score, _ = rank_of(final, gold)
            bonus_applied = final.get(gold, 0) - fused_pre.get(gold, 0) > 1e-9

            counterfactual = {}
            for alpha in (0.3, 0.5):
                cf = final_scores(kw_norm, vec_norm, alpha, name_map, tokens, True)
                counterfactual[f"alpha_{alpha}"] = rank_of(cf, gold)[0]
            no_bonus = final_scores(kw_norm, vec_norm, RETRIEVAL_ALPHA, name_map,
                                    tokens, False)
            counterfactual["no_bonus"] = rank_of(no_bonus, gold)[0]

            top5 = []
            for kid, score in sorted(final.items(), key=lambda x: -x[1])[:5]:
                via = ("both" if kid in kw_norm and kid in vec_norm
                       else "keyword" if kid in kw_norm else "vector")
                top5.append({
                    "kp_id": kid, "name": name_map.get(kid, ""),
                    "score": round(float(score), 4), "via": via,
                    "kw_score": round(float(kw_raw.get(kid, 0.0)), 4),
                    "vec_score": round(float(vec_raw.get(kid, 0.0)), 4),
                    "bonus_tokens": [t for t in tokens
                                     if t and t in name_map.get(kid, "")],
                })

            # 失败归因（基于证据的粗分类）
            if kw_total == 0:
                if vec_rank and vec_rank > 10:
                    failure_stage = "关键词路未激活（FTS 0 命中）+ 向量排名>10"
                else:
                    failure_stage = "关键词路未激活（FTS 0 命中）+ 向量排名6-10（临界）"
            elif kw_rank is None and vec_rank and vec_rank > 10:
                failure_stage = "关键词未命中 + 向量排名>10"
            elif kw_rank is not None and final_rank and final_rank > 10:
                failure_stage = "融合权重被关键词噪声拉低"
            elif vec_rank and vec_rank <= 10 and final_rank and final_rank > 5:
                failure_stage = "融合/排序临界"
            elif vec_rank and vec_rank > 10:
                failure_stage = "向量排名不足"
            else:
                failure_stage = "其他/临界"
            bonus_downgrade = bool(vec_rank and final_rank and final_rank > vec_rank)

            samples.append({
                "query_id": row["query_id"], "query_text": text, "stage": stage,
                "gold_primary": gold, "gold_name": name_map.get(gold, ""),
                "gold_kp_ids": q["gold_kp_ids"],
                "query_len": len(text), "token_count": len(tokens),
                "fts_query": fts_q,
                "kw_total": kw_total, "kw_gold_rank": kw_rank,
                "kw_gold_score": kw_score,
                "vec_total": vec_total, "vec_gold_rank": vec_rank,
                "vec_gold_score": vec_score,
                "fused_gold_rank": fused_rank, "fused_gold_score": fused_score,
                "final_gold_rank": final_rank, "final_gold_score": final_score,
                "baseline_rank": row["rank_primary"],
                "name_bonus_applied": bonus_applied,
                "counterfactual": counterfactual,
                "top5": top5,
                "failure_stage": failure_stage,
                "bonus_downgrade": bonus_downgrade,
                "gold_bonus_tokens": [t for t in tokens
                                      if t and t in name_map.get(gold, "")],
            })
    finally:
        ret.close()

    # 占位 KP 证据
    placeholder = {kid: {"kp_id": kid, "name": name_map.get(kid, ""),
                         "description": ret._kp_meta.get(kid, {}).get("description", ""),
                         "occurrences": []}
                   for kid in PLACEHOLDER_KPS}
    for s in samples:
        for pos, item in enumerate(s["top5"], 1):
            if item["kp_id"] in placeholder:
                placeholder[item["kp_id"]]["occurrences"].append({
                    "query_id": s["query_id"], "position": pos, "score": item["score"],
                    "via": item["via"], "kw_score": item["kw_score"],
                    "vec_score": item["vec_score"],
                    "query_has_unit_token": any(t in s["query_text"] for t in UNIT_TOKENS),
                })

    taxonomy = Counter(s["failure_stage"] for s in samples)
    bonus_downgrades = sum(1 for s in samples if s["bonus_downgrade"])
    report = {
        "protocol": "stage_filtered",
        "alpha": RETRIEVAL_ALPHA,
        "failure_count": len(samples),
        "taxonomy": dict(taxonomy),
        "bonus_downgrades": bonus_downgrades,
        "samples": samples,
        "placeholders": placeholder,
    }
    OUT_JSON.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    # ---------- Markdown ----------
    L = [
        "# 检索失败逐阶段诊断（Phase 2 缺陷定位，2026-11-15）",
        "",
        "> 只读诊断：未修改运行时代码 / 题库 / 知识图谱 / Gold / 评测集 / 指标口径；",
        "> 未重跑全量评测；未 commit/push。数据来源：正式基线 stage_filtered 协议的 23 条",
        "> primary 未进 top-5 样本（16 条未入 top-10 + 7 条 6–10）。",
        "",
        "## 0. 口径说明（重要）",
        "",
        "- 正式基线评估的是**知识点召回**：阶段 = FTS 关键词 → 向量 → min-max 归一化融合",
        "  （α=0.4）→ 名称直配 +0.05。**不存在题目检索的 Jaccard/难度/FTS 重排**；",
        "  题目重排的弱标签平局是另一条路径的已知缺陷，不归因于这 23 条样本。",
        "- FTS 候选：`kp_fts` MATCH（jieba 分词后逐词引号、隐式 AND）；返回的是**实际命中**的 KP。",
        "- 向量候选：当前学段全部 82 个小学 KP 都有余弦分，因此「是否进入向量候选集」恒为是；",
        "  有意义的是**向量排名**。融合候选同理（并集=82 KP）。",
        "",
        f"## 1. 失败分类汇总（共 {len(samples)} 条）",
        "",
        "| 失败阶段 | 数量 | 含义 |",
        "|---|---|---|",
    ]
    meanings = {
        "关键词路未激活（FTS 0 命中）+ 向量排名>10":
            "FTS 全 token AND 无命中；融合退化为纯向量；gold 向量排名 >10",
        "关键词路未激活（FTS 0 命中）+ 向量排名6-10（临界）":
            "同上；gold 向量排名 6–10，距 top-5 很近",
    }
    for stage, count in taxonomy.most_common():
        L.append(f"| {stage} | {count} | {meanings.get(stage, '—')} |")
    L.append(f"| 其中：名称加分导致最终排名下降 | {bonus_downgrades} | "
             "其他 KP 因单字/常用 token 获得 +0.05，gold 被推后（次生但可修复） |")

    zero_kw = sum(1 for x in samples if x["kw_total"] == 0)
    tok_counts = sorted(x["token_count"] for x in samples)
    example = samples[0] if samples else {}
    L += [
        "",
        "### 1.1 为什么 23/23 都是关键词路未激活",
        "",
        f"- **23 条中有 {zero_kw} 条 `kw_total=0`**（FTS 一个 KP 都没命中）；",
        f"- 题干 token 数范围 {tok_counts[0]}–{tok_counts[-1]}（中位 {tok_counts[len(tok_counts)//2]}）；",
        "- FTS 查询构造 = jieba 全 token 逐词引号 + 隐式 AND，即要求 KP 描述同时包含查询的**全部** token；",
        "  长应用题几乎不可能满足，故关键词路整体失效，融合（α=0.4）实际退化为 `0.6 × 向量归一化分`。",
        f"- 示例 FTS 查询串（`{example.get('query_id', '')}`）：`{example.get('fts_query', '')}`",
        "",
    ]

    L += ["", "## 2. 16 条 Gold 未入 Top-10 逐条", ""]
    for s in [x for x in samples if not x["baseline_rank"]]:
        L += [
            f"### `{s['query_id']}`　rank=未入 top10｜{s['failure_stage']}",
            "",
            f"- 查询：{s['query_text']}",
            f"- Gold：`{s['gold_primary']}`（{s['gold_name']}）；题干 {s['query_len']} 字 / {s['token_count']} tokens",
            f"- FTS：命中 KP **{s['kw_total']}** 个；gold "
            f"{'命中，rank=%s，score=%s' % (s['kw_gold_rank'], s['kw_gold_score']) if s['kw_gold_rank'] else '**未命中**'}",
            f"- 向量：gold rank **{s['vec_gold_rank']}/{s['vec_total']}**，余弦 **{s['vec_gold_score']}**",
            f"- 融合（α=0.4）：gold rank **{s['fused_gold_rank']}**，score **{s['fused_gold_score']}**",
            f"- 最终（+名称加分：{'是' if s['name_bonus_applied'] else '否'}）：gold rank **{s['final_gold_rank']}**，score **{s['final_gold_score']}**",
            f"- 反事实排名：α=0.3 → {s['counterfactual']['alpha_0.3']}；α=0.5 → {s['counterfactual']['alpha_0.5']}；无名称加分 → {s['counterfactual']['no_bonus']}",
            f"- gold 名称加分触发 token：{s['gold_bonus_tokens'] or '无'}",
            "",
            "| Top-5 | KP | 名称 | 分数 | 召回路 | kw 分 | vec 分 | 加分 token |",
            "|---|---|---|---|---|---|---|---|",
        ]
        for i, item in enumerate(s["top5"], 1):
            L.append(f"| {i} | {item['kp_id']} | {item['name']} | {item['score']} | "
                     f"{item['via']} | {item['kw_score']} | {item['vec_score']} | "
                     f"{item.get('bonus_tokens') or '—'} |")
        L.append("")

    L += ["## 3. 7 条 Gold 排名 6–10 逐条", ""]
    for s in [x for x in samples if x["baseline_rank"]]:
        gap = None
        if s["top5"]:
            gap = round(s["top5"][4]["score"] - s["final_gold_score"], 4)
        L += [
            f"### `{s['query_id']}`　rank={s['final_gold_rank']}｜{s['failure_stage']}",
            "",
            f"- 查询：{s['query_text']}",
            f"- Gold：`{s['gold_primary']}`（{s['gold_name']}）",
            f"- FTS：命中 {s['kw_total']} 个；gold "
            f"{'rank=%s score=%s' % (s['kw_gold_rank'], s['kw_gold_score']) if s['kw_gold_rank'] else '未命中'}",
            f"- 向量：rank **{s['vec_gold_rank']}/{s['vec_total']}**，余弦 **{s['vec_gold_score']}**",
            f"- 融合：rank **{s['fused_gold_rank']}**，score **{s['fused_gold_score']}**；"
            f"最终 rank **{s['final_gold_rank']}**，score **{s['final_gold_score']}**"
            f"（名称加分：{'是' if s['name_bonus_applied'] else '否'}）",
            f"- 与第 5 名分差：**{gap}**；反事实：α=0.3 → {s['counterfactual']['alpha_0.3']}，"
            f"α=0.5 → {s['counterfactual']['alpha_0.5']}，无加分 → {s['counterfactual']['no_bonus']}",
            f"- gold 名称加分触发 token：{s['gold_bonus_tokens'] or '无'}；"
            f"名称加分是否导致降位：{'是' if s['bonus_downgrade'] else '否'}",
            "- 说明：知识产权点召回路径**没有 Jaccard/难度/FTS 题目重排特征**；这里的“重排”只有",
            "  关键词归一化、向量归一化、名称加分三项。Jaccard 平局属于题目检索路径（另行记录）。",
            "",
            "| Top-5 | KP | 名称 | 分数 | 召回路 | kw 分 | vec 分 | 加分 token |",
            "|---|---|---|---|---|---|---|---|",
        ]
        for i, item in enumerate(s["top5"], 1):
            L.append(f"| {i} | {item['kp_id']} | {item['name']} | {item['score']} | "
                     f"{item['via']} | {item['kw_score']} | {item['vec_score']} | "
                     f"{item.get('bonus_tokens') or '—'} |")
        L.append("")

    L += ["## 4. 错误占位知识点分析", "",
          "| KP | 名称 | 描述 | 失败样本中出现次数 | 关键词出现 | 向量出现 | 查询含单位词 |",
          "|---|---|---|---|---|---|---|"]
    for kid in PLACEHOLDER_KPS:
        p = placeholder[kid]
        occ = p["occurrences"]
        L.append(f"| {kid} | {p['name']} | {p['description']} | {len(occ)} | "
                 f"{sum(1 for o in occ if o['via'] in ('keyword', 'both'))} | "
                 f"{sum(1 for o in occ if o['via'] in ('vector', 'both'))} | "
                 f"{sum(1 for o in occ if o['query_has_unit_token'])} |")
    L += ["",
          "**结论**：上表「关键词出现」全部为 0、「向量出现」等于出现次数——5 个占位 KP",
          "全部由**向量路**推高，关键词路未参与。其描述仅 6–14 字且以单位词列表为主",
          "（如「吨/千克/克」「千米/米/分米/厘米/毫米」），语义泛化导致与大量带单位应用题",
          "在向量空间接近；这是**描述不足/过度泛化**，不是关键词过宽。",
          "",
          "### 逐次出现证据", "",
          "| 占位 KP | 样本 | 位置 | 分数 | 召回路 | kw 分 | vec 分 | 查询含单位词 |",
          "|---|---|---|---|---|---|---|---|"]
    for kid in PLACEHOLDER_KPS:
        for o in placeholder[kid]["occurrences"]:
            L.append(f"| {kid} | {o['query_id']} | {o['position']} | {o['score']} | "
                     f"{o['via']} | {o['kw_score']} | {o['vec_score']} | "
                     f"{'是' if o['query_has_unit_token'] else '否'} |")

    # 参数敏感性统计
    fixed_by_alpha = sum(1 for s in samples if (s["counterfactual"]["alpha_0.3"] or 99) <= 5)
    fixed_by_alpha5 = sum(1 for s in samples if (s["counterfactual"]["alpha_0.5"] or 99) <= 5)
    fixed_by_nobonus = sum(1 for s in samples if (s["counterfactual"]["no_bonus"] or 99) <= 5)
    downgraded = [x for x in samples if x["bonus_downgrade"]]
    L += ["", "### 4.1 名称加分降位清单", "",
          "| 样本 | 向量排名 | 最终排名 | gold 加分 | top-5 中获加分的 KP（示例 token） |",
          "|---|---|---|---|---|"]
    for x in downgraded:
        examples = []
        for it in x["top5"]:
            if it["bonus_tokens"]:
                examples.append(f"{it['kp_id']}({','.join(it['bonus_tokens'][:3])})")
        L.append(f"| {x['query_id']} | {x['vec_gold_rank']} | {x['final_gold_rank']} | "
                 f"{'有' if x['gold_bonus_tokens'] else '无'} | {'；'.join(examples) or '—'} |")
    L += ["", f"> 共 **{len(downgraded)}** 条；加分规则为「查询任一 token 是 KP 名称子串」+0.05，"
              "单字/常用词（积/行/位/数/和/倍/问/则/与）即可触发。", ""]

    L += [
        "",
        "## 5. 参数敏感性与根因判断",
        "",
        f"- 仅把 α 调到 0.3，23 条中进入 top-5 的样本数：**{fixed_by_alpha}**；",
        f"- 仅把 α 调到 0.5，进入 top-5：**{fixed_by_alpha5}**；",
        f"- 仅去掉名称加分，进入 top-5：**{fixed_by_nobonus}**。",
        "",
        "**根因证据（按影响面）**：",
        "",
        "1. **关键词路 23/23 完全未激活**：FTS 查询为「全 token 隐式 AND」，23 条样本",
        "   `kw_total` 全部为 0（一个 KP 都没命中）；融合（α=0.4）实际退化为 `0.6 × 向量",
        "   归一化分`。证据：§1.1 的 token 数范围与 FTS 查询串；JSON 中每条 `kw_total=0`。",
        "2. **向量排名不足是主因**：16 条 gold 向量排名 >10，7 条排名 6–10（临界）；",
        "   长题干（平均 41 字）主题词被数字/单位/背景描述稀释，gold 常被泛化 KP 挤出 top-5。",
        "   证据：各样本 `vec_gold_rank`（49/48/53/54 等）。",
        "3. **占位 KP 全部由向量路推高**：5 个占位 KP 在 23 条样本中累计出现 39 次，",
        "   **关键词出现 0 次、向量出现 39 次**；其描述仅 6–14 字且是单位词列表",
        "   （「吨/千克/克」「千米/米/分米/厘米/毫米」等），语义过度泛化，与带单位应用题",
        "   在向量空间普遍接近。证据：§4 的出现次数与召回路表。",
        f"4. **名称加分存在系统性误触发/降位**：23 条中 **{bonus_downgrades} 条**因其他 KP 获得",
        "   名称加分而最终排名下降（如 599384 21→31、301084 11→13、278113 6→8、822661 5→6）；",
        "   加分规则是「查询任一 token 是 KP 名称子串」+0.05，单字/常用词（积/行/位/数/和/倍/问/",
        "   则/与）即可触发，gold 本身全部未获加分。证据：§4.1 降位清单与各样本加分 token 列。",
        "5. **α 调参在本批样本上无效**：α=0.3 与 α=0.5 均修复 **0** 条；去掉名称加分",
        "   仅修复 **1** 条（822661）。原因是关键词路为 0，α 只改变一个常数缩放，不改变排序。",
        "",
        "**无法仅靠调参解决的问题**：",
        "",
        "- FTS 的「全 token AND」导致长查询关键词路完全失效——需要改查询构造/召回策略",
        "  （OR/加权/截断），不是 α 能解决；",
        "- 小学规则 KP 描述过短（6–15 字）且泛化，需要补充/改写 KP 描述或引入题干侧特征；",
        "- 长题干的向量稀释需要查询改写/分句/关键词抽取，不是简单换权重；",
        "- 名称加分规则本身需要收紧（token 长度/匹配方式），属于规则修改而非参数调整；",
        "- 单位词泛化需要「KP 描述去泛化 + 关键词停用」组合治理，单改 α 无效。",
        "",
        "**修复优先级（建议，未执行）**：",
        "",
        "| 优先级 | 修复项 | 预期受影响样本 | 类型 |",
        "|---|---|---|---|",
        "| P0 | FTS 查询构造：长查询 OR/加权/截断，替代全 token AND | 23/23（关键词路未激活） | 结构性 |",
        "| P0 | 名称加分规则收紧（token 长度≥2 / 整词匹配 / 停用常用词） | 11 条降位样本（§4.1） | 规则 |",
        "| P0 | 小学规则 KP 描述扩充/去泛化（单位换算类加边界与场景） | 16 条向量排名>10 + 7 条临界 | 数据/描述 |",
        "| P1 | 单位/通用词停用或降权（与 OR 改造配对，防新噪声） | 39 次占位 KP 出现 | 结构性/规则 |",
        "| P1 | 长查询改写/分句/关键词抽取 | 长题干向量稀释样本 | 结构性 |",
        "| P2 | α / 融合权重调参 | 本批 0 收益，仅临界样本可能受益 | 参数 |",
        "",
        "### 5.1 修复项与预期影响样本（q_id）",
        "",
        "| 修复项 | 预期直接影响样本 |",
        "|---|---|",
        f"| 名称加分规则收紧 | {', '.join(x['query_id'] for x in samples if x['bonus_downgrade'])} |",
        f"| KP 描述扩充/去泛化 | 16 条 >10：{', '.join(x['query_id'] for x in samples if not x['baseline_rank'])}；"
        f"7 条 6–10：{', '.join(x['query_id'] for x in samples if x['baseline_rank'])} |",
        f"| FTS 查询构造（OR/加权/截断） | 全部 23 条（关键词路 0 命中） |",
        f"| 单位/通用词停用或降权 | {', '.join(sorted({o['query_id'] for p in placeholder.values() for o in p['occurrences']}))} |",
        f"| 长查询改写/分句/关键词抽取 | 全部 23 条（题干 28–101 字） |",
        "| α / 融合权重调参 | 本批 0 条（仅 822661 在去掉名称加分后进入 top-5） |",
        "",
        "> 边界：本报告仅诊断，未修改任何运行时代码、题库、图谱、Gold、评测集或指标口径；",
        "> 未重跑全量评测；未 commit/push。",
        "",
    ]
    OUT_MD.write_text("\n".join(L), encoding="utf-8")

    print(f"诊断完成：{len(samples)} 条样本")
    print("分类：", dict(taxonomy))
    print(f"参数反事实：α=0.3 修复 {fixed_by_alpha} / α=0.5 修复 {fixed_by_alpha5} / 无加分修复 {fixed_by_nobonus}")
    print(f"报告：{OUT_MD}\n原始：{OUT_JSON}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
