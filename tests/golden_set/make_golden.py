#!/usr/bin/env python3
"""Generate golden annotations for 40 samples based on v0.3 decision tree.

Each annotation is hand-crafted by applying the decision tree rules to the
actual content. The script reads samples.json and writes golden.json.
"""

import json
from pathlib import Path
from collections import Counter

SAMPLES_PATH = Path(__file__).parent / "samples.json"
GOLDEN_PATH = Path(__file__).parent / "golden.json"

# Each entry: (verdict, category, trigger, reason, confidence, priority)
# Decision tree: Step1(dog) > Step2(anti-seed) > Step3(tool) > Step4(focus) > Step5(tutorial) > Step6(archive)

GOLDEN = [
     # 0 - Pet aging care market. User's pet interest is Douyin pet account, not pet care industry.
     ("archive", "", "",
      "宠物护理行业市场分析,用户宠物关注点是账号运营而非产业链", "medium", "normal"),

    # 1 - SEO keyword research via word-root method. Step4: directly overlaps SEO focus.
    ("seed", "共鸣补充",
     "词根法挖词思路对,可以补充用Agent自动化批量挖掘和监控飙升词的流程",
     "SEO方法论与用户关注点直接重合,有具体可补充角度",
     "high", "normal"),

    # 2 - ByteDance overseas short drama distribution. Not AI/Agent core focus.
    ("archive", "", "", "海外短剧分销资讯,非用户AI/Agent核心关注方向", "medium", "normal"),

    # 3 - Pet economy: "靠人赚钱不靠规模". Step4: deep original insight on pets+business.
    ("seed", "共鸣补充",
     "宠物经济靠人赚钱的洞察很准,我的宠物账号走IP路线恰好验证了不靠规模靠人的逻辑",
     "宠物经济系统性分析撞用户宠物+商业模型双关注点", "high", "normal"),

     # 4 - AI reselling phenomenon description, not deep analysis. Step5: case study.
     ("asset", "案例", "",
      "AI倒爷现象描述属案例,无原创系统性观点", "medium", "normal"),

     # 5 - Critical pet aging market analysis. Same focus mismatch as #0.
     ("archive", "", "",
      "宠物老龄化市场批判分析,与用户宠物账号运营关注点不直接重合", "medium", "normal"),

    # 6 - Codex/Claude Code project structure guide. Step3: tool meta-topic, no original viewpoint.
    ("asset", "工具", "",
     "讨论Codex/Claude Code使用方法但属教程性质无原创观点,工具元话题标记高优先级",
     "high", "high"),

    # 7 - Brief resource share (empireflippers). Not enough substance for seed.
    ("archive", "", "", "分享副业平台链接,无原创观点展开,属资源资讯", "medium", "normal"),

    # 8 - Feishu + Gemini3 tutorial. Step3: 飞书 meta-topic, pure tutorial.
    ("asset", "教程", "",
     "飞书自动化工作流教程,涉及在用工具但属纯操作指南无观点", "high", "high"),

    # 9 - WeChat public account writing tips. Step5: pure tutorial.
    ("asset", "教程", "", "公众号写作经验分享,属纯教程无原创系统性观点", "high", "normal"),

     # 10 - 16-step AI entrepreneurship checklist. Step2(b): mostly common sense, generic advice.
     ("asset", "方法论", "",
      "16步创业清单多为常识堆叠,缺具体反共识角度", "medium", "normal"),

    # 11 - Hermes auto-update workflow. Step3: tool meta-topic, technical SOP.
    ("asset", "工具", "",
     "Hermes自动更新工作流描述,涉及在用工具但属技术SOP无观点", "high", "high"),

     # 12 - New media account analysis tutorial. Step5: pure tutorial, no original viewpoint.
     ("asset", "教程", "",
      "新媒体账号拆解方法论属纯教程,无原创系统性观点", "medium", "normal"),

    # 13 - Productivity tool list (Notion/Obsidian/Syncthing). Step3: Obsidian, pure list.
    ("asset", "工具", "",
     "生产力工具推荐清单含Obsidian,但属纯罗列无原创批判观点", "high", "high"),

    # 14 - Title asks about AI courses (help-seeking), content is Claude Code guide. Step3.
    ("asset", "工具", "",
     "内容实际讨论Claude Code项目结构,属工具教程无观点,标记高优先级", "medium", "high"),

    # 15 - 6 principles for building with AI. NOT vague list (detailed examples). Step4.
    ("seed", "共鸣补充",
     "AI做产品六原则中减法思维很关键,我可以补充Agent工作流中如何系统性做减法的实践",
     "AI产品开发原则撞用户AI+产品化关注点,每条都有具体案例可补充", "high", "normal"),

    # 16 - Book reflection on Guns Germs Steel. Not aligned with AI/business core focus.
    ("archive", "", "",
     "读书感想讨论历史决定论,与用户AI/Agent核心方向无直接重合", "medium", "normal"),

    # 17 - Three communication rules. Step2(b): vague list, common sense, no specific action.
    ("asset", "方法论", "",
     "三条表达法则均为常识堆叠,无展开无证据,抓不出具体可补充角度", "high", "normal"),

    # 18 - 15 AI money-making paths with real cases. Step5: case collection.
    ("asset", "案例", "",
     "15条AI搞钱路径属案例汇编,有具体数据但无原创系统性观点", "high", "normal"),

    # 19 - Greg Isenberg 20 AI business insights. Step4: directly overlaps, strong original insights.
    ("seed", "共鸣补充",
     "SaaS崩塌和结果导向定价的判断准确,我可以补充一人公司用Agent栈替代30人团队的实操路径",
     "AI商业洞察直接撞用户AI+一人公司+商业模式三重关注点", "high", "normal"),

     # 20 - Energy management flywheel. Personal growth, not AI/business focus.
     ("archive", "", "",
      "精力管理属个人成长,与用户AI/Agent核心方向无直接重合", "medium", "normal"),

    # 21 - 7 vertical AI products analysis. Step4: overlaps AI/Agent + product analysis.
    ("seed", "共鸣补充",
     "垂直AI产品选品思路对,我可以补充用toolify数据自动挖掘和验证细分赛道的Agent流程",
     "垂直AI产品分析撞用户AI+独立开发关注点,含产品化可补充角度", "high", "normal"),

    # 22 - Switching OpenClaw to Hermes. Step3: tool meta-topic, has original viewpoint -> seed.
    ("seed", "共鸣补充",
     "从OpenClaw换Hermes的实践跟我一致,Claude做SSOT单向同步Hermes的架构可以补充细节",
     "直接讨论在用工具OpenClaw/Hermes的切换决策,含原创实践观点", "high", "high"),

    # 23 - TS vs Python for Agent projects. Step4: overlaps AI/Agent, strong original analysis.
    ("seed", "共鸣补充",
     "TS适合Agent的论证有道理,我可以补充类型系统对tool schema安全性的具体实践",
     "Agent技术选型分析撞用户AI/Agent核心关注点,含系统性论证可补充", "high", "normal"),

    # 24 - DesignMD tool for cloning UI. Step5: pure tool recommendation.
    ("asset", "工具", "", "designmd.me工具推荐,属纯工具资讯无原创系统性观点", "medium", "normal"),

    # 25 - Spring Festival short video templates. Step5: pure tutorial/template.
    ("asset", "教程", "", "春节短视频选题和文案模板,属纯教程无原创观点", "high", "normal"),

    # 26 - Title about Claude Skill guide, content is book reflection. Mismatched content.
    ("archive", "", "",
     "内容为读书感想,标题与内容不匹配,与用户核心关注点无直接重合", "medium", "normal"),

    # 27 - Cosplay poster prompt template. Step5: pure prompt/template.
    ("asset", "工具", "", "Cosplay海报提示词模板,属Prompt工具素材无原创观点", "high", "normal"),

    # 28 - 30yo AI opportunity. Borderline, has opinion but generic advice.
    ("archive", "", "",
     "AI机会论属泛泛观点,建议过于笼统,与用户深度关注点不匹配", "medium", "normal"),

    # 29 - Overseas TikTok CPS. Step5: pure tutorial/SOP.
    ("asset", "教程", "", "海外版抖音CPS赚钱教程,属纯操作指南无原创系统性观点", "high", "normal"),

    # 30 - 10w+ article writing methodology. Step5: pure tutorial/methodology.
    ("asset", "方法论", "", "公众号爆文拆解方法论,属纯写作教程无原创系统性观点", "high", "normal"),

    # 31 - AI knowledge management system (CARD). Step4: overlaps knowledge base + AI.
    ("seed", "共鸣补充",
     "CARD系统思路跟我正在做的知识库改造重合,我可以补充判别器作为Capture和Deploy之间的AI筛选层",
     "知识管理系统撞用户知识库+AI关注点,含可补充的具体架构角度", "high", "normal"),

    # 32 - AI mini-program dev tutorial. Step5: pure tutorial.
    ("asset", "教程", "", "AI开发微信小程序实操教程,属纯操作指南无原创观点", "high", "normal"),

    # 33 - Traffic conclusions from 3 rounds of testing. Step2(b): vague list.
    ("asset", "方法论", "",
     "流量结论六条均为经验堆叠无展开,抓不出具体可补充角度", "medium", "normal"),

    # 34 - AI writing framework tips. Step5: pure tutorial.
    ("asset", "教程", "", "AI自媒体写作框架教程,属纯方法论无原创系统性观点", "high", "normal"),

    # 35 - Title about AI podcast, content is DesignMD tool. Mismatched, pure tool share.
    ("asset", "工具", "",
     "内容实际为designmd.me工具推荐,标题与内容不匹配,属工具资讯", "medium", "normal"),

    # 36 - 107 AI Twitter accounts list. Step5: pure resource list.
    ("asset", "工具", "", "AI领域Twitter关注清单,属纯资源列表无原创观点", "high", "normal"),

    # 37 - Learning framework (3 steps). Step2(b): vague list, common sense.
    ("asset", "方法论", "",
     "学习框架三条均为常识堆叠,无展开无证据,抓不出具体可补充角度", "high", "normal"),

    # 38 - Ezra Klein on AI unemployment. Step4: overlaps AI, strong original analysis.
    ("seed", "共鸣补充",
     "稀缺性转移的论证深刻,我可以补充AI时代'人味即稀缺'如何转化为产品定价策略",
     "AI就业影响深度分析撞用户AI关注点,含反直觉论证和可补充角度", "high", "normal"),

    # 39 - Open-source diagram tool. Step5: pure tool info.
    ("asset", "工具", "", "开源架构图工具推荐,属纯工具资讯无原创观点", "high", "normal"),
]


def _infer_step(verdict, category, priority, sample_tag):
    if verdict == "seed" and category == "养狗对立":
        return "step1_dog"
    if verdict == "asset" and category in ("案例", "方法论"):
        if "step2" in sample_tag:
            return "step2_anti_seed"
        return "step5_tutorial"
    if priority == "high" and verdict in ("seed", "asset"):
        return "step3_tool"
    if verdict == "seed" and category in ("对立", "共鸣补充"):
        return "step4_focus"
    if verdict == "asset" and category in ("教程", "工具"):
        return "step5_tutorial"
    if verdict == "archive":
        return "step6_archive"
    return "unknown"


def main():
    samples = json.loads(SAMPLES_PATH.read_text(encoding="utf-8"))
    assert len(samples) == len(GOLDEN), f"count mismatch: {len(samples)} vs {len(GOLDEN)}"

    output = []
    for i, (sample, gold) in enumerate(zip(samples, GOLDEN)):
        verdict, category, trigger, reason, confidence, priority = gold
        output.append({
            "unit_id": sample["unit_id"],
            "source": sample["source"],
            "title": sample["title"],
            "sample_tag": sample["sample_tag"],
            "current_verdict": sample["current_verdict"],
            "current_category": sample["current_category"],
            "golden": {
                "verdict": verdict,
                "category": category,
                "trigger": trigger,
                "reason": reason,
                "confidence": confidence,
                "priority": priority,
                "decision_step": _infer_step(verdict, category, priority, sample["sample_tag"]),
            },
        })

    GOLDEN_PATH.write_text(
        json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"Wrote {len(output)} golden annotations to {GOLDEN_PATH}")

    vc = Counter(x["golden"]["verdict"] for x in output)
    print(f"Distribution: {dict(vc)}")
    for v in ("seed", "asset", "archive"):
        items = [x for x in output if x["golden"]["verdict"] == v]
        cats = Counter(x["golden"]["category"] for x in items)
        print(f"  {v}: {dict(cats)}")

    # Show verdict changes vs current
    changes = []
    for x in output:
        if x["golden"]["verdict"] != x["current_verdict"]:
            changes.append(f"  {x['unit_id']}: {x['current_verdict']} -> {x['golden']['verdict']}")
    if changes:
        print(f"\nVerdict changes ({len(changes)}):")
        for c in changes:
            print(c)
    else:
        print("\nNo verdict changes from current.")


if __name__ == "__main__":
    main()
