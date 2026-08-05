# 项目输出与机器状态

## 目录

- [项目目录](#项目目录)
- [分镜字段](#分镜字段)
- [review-state.json（schema v3）](#review-statejsonschema-v3)
- [发布 manifest](#发布-manifest)
- [v1/v2 兼容与迁移](#v1v2-兼容与迁移)
- [发布说明](#发布说明)

## 项目目录
```text
<主题目录>/
├── 创作方案.md
├── 故事脚本.md
├── N图分镜.md
├── 角色与视觉设定.md
├── 出图提示词.md
├── 发布文件说明.md
├── 自审记录.md
├── 验收记录.md
├── 返修报告.md
├── 生成阻塞报告.md
├── review-state.json
├── release-manifest.json
├── 生成请求/
├── 角色参考/
├── 地点母版/
├── 原始生成图/
├── 返修记录/
└── 最终发布版-N图/
    ├── 01.png ... NN.png
    └── 最终发布总览.jpg
```
如目录已存在，检查内容并使用版本化目录。不得覆盖其他作品。

## 分镜字段
| 图号 | 唯一证据 | 字幕 | 拍摄来源 | 拍摄原因 | 受限机位 | 人物意识 | 设备/年代 | 成像结果 | 连续性引用 | 真实性风险 |
|---|---|---|---|---|---|---|---|---|---|---|
字幕不依赖图片内文字。调整图数时同步更新所有文件。

## review-state.json（schema v3）
```json
{
  "schema_version": 3,
  "project_dir": ".",
  "phase": "scene_self_review",
  "planned_count": 24,
  "max_repairs_per_item": 1,
  "artifacts": {
    "self_review": "自审记录.md",
    "acceptance": "验收记录.md",
    "release_manifest": "release-manifest.json"
  },
  "images": [
    {
      "number": 1,
      "status": "pending",
      "candidate": null,
      "hard_failures": [],
      "photo_red_flags": [],
      "repair_count": 0,
      "repair_mode": null,
      "repair_file": null,
      "final_source": null,
      "notes": "",
      "transport": {
        "backend": "built_in_imagegen",
        "route": null,
        "attempts_total": 0,
        "consecutive_failures": 0,
        "last_error": null,
        "last_error_type": null,
        "error_fingerprint": null,
        "backend_error_key": null,
        "prompt_sha256": null,
        "reference_sha256": [],
        "circuit_open": false,
        "probe_granted": false,
        "probe_in_flight": false,
        "active_attempt": null,
        "attempt_history": [],
        "next_eligible_at": null,
        "reference_summary": {
          "count": 0,
          "total_bytes": 0,
          "images": []
        },
        "recovery": {
          "level": 0,
          "state": "idle",
          "transaction": null,
          "last_error": null
        }
      }
    }
  ],
  "reference_jobs": [
    {
      "id": "hero-main",
      "kind": "character",
      "output_dir": "角色参考",
      "status": "pending",
      "candidate": null,
      "candidate_versions": [],
      "content_repair_count": 0,
      "review_issues": [],
      "approved_candidate": null,
      "notes": "",
      "transport": "<与 images 相同的 transport 对象>"
    }
  ],
  "transport_backends": {},
  "transport_batch": null,
  "fallback_authorizations": {},
  "reference_board_policy": null,
  "reference_board_fallbacks": [],
  "blocking_reasons": []
}
```
允许阶段：`drafting`、`text_self_review`、`awaiting_plan_approval`、`reference_self_review`、`awaiting_reference_approval`、`scene_self_review`、`awaiting_repair_approval`、`repairing`、`final_self_review`、`complete`、`needs_user`。

启用延后返修时增加 `repair_policy.mode: "deferred_user_approved"`，并记录 `report_file`、`report_generated_at`、`approved_numbers` 和 `approved_at`。待返修图片使用 `repair_recommendation` 保存建议方式、问题和说明。只有全部原始候选存在并生成返修报告后才能进入 `awaiting_repair_approval`；进入 `repairing` 必须有用户批准图号。

图片 `status` 只能是 `pending`、`generating`、`transport_blocked`、`review_pending`、`pass`、`needs_user`。初始化时必须建立 `planned_count` 个连续图号。

`pending`、`generating` 和 `transport_blocked` 可以没有候选文件；`review_pending`、`pass` 和内容审查后的 `needs_user` 必须有实际候选文件。`repair_count` 只能为 0 或 1，只记录实际候选图的内容返修。网络错误、超时和无候选由 `transport` 独立记录。

`reference_jobs` 保存参考资产任务。候选必须位于登记的 `output_dir` 内；`candidate_versions` 保留每版候选和审查结论，`content_repair_count` 只能为 0 或 1，`approved_candidate` 只在 `pass` 时存在并等于当前候选。允许种类为 `character`、`location`、`prop`、`vehicle`、`wonder`。

`transport_backends` 保存滚动错误窗口和非阻断性的 `health_warning`；v3 事件统一为 `job_type`、`job_id`、`error_type`、`error_key`、`failed_at`。相同后端错误不得阻止其他任务。`fallback_authorizations` 仍只由带 `--user-approved` 的命令写入。单项传输阻塞不写入项目级 `blocking_reasons`，也不把阶段改为 `needs_user`。

`active_attempt` 保存当前唯一在途调用及其尝试前状态；`attempt_history` 最多保留最近 50 条记录。`recovery.state` 为 `idle`、`staging`、`ready` 或 `failed`，恢复事务使用尝试 ID 和下一档生成确定性事务 ID。`auto_recovery_level` 为 0–2，`auto_recovery_history` 保存事务 ID、原始请求、来源角色、来源与派生哈希、参数、请求版本和失败原因。`next_eligible_at` 只用于网络错误或没有可降级输入的延后重试。

`transport_batch` 记录当前小批次：严格串行、最多成功 3 张、最多持续 900 秒。`preflight` 自动创建或滚动批次；传输失败不结束批次。

新项目的双参考自动生成两档参考板，无需 `reference_board_policy`。标准档为 1024×1280/JPEG 88，低清档为 768×960/JPEG 80；两档都直接使用原始来源并保存 schema v1 sidecar。`reference_board_policy`、`reference_board_fallbacks` 仅用于旧项目兼容。

正式原图使用两张独立参考时 `active_attempt.runtime_budget_seconds` 为 480；0–1 张自然参考、一个双来源参考板和所有返修为 600。参考摘要保留兼容字段 `count`，并增加 `physical_attachment_count`、`logical_source_count`、`contains_reference_board` 和 `reference_kind`。

正式候选在 `record-success` 前必须已经是精确竖版 4:5。内置模型返回竖版但非 4:5 时，使用 `normalize_candidate.py` 非破坏性裁切到 4:5；横版不得作为正式候选。发布阶段的 1080×1350 处理只负责尺寸统一，不再负责纠正方向错误。

每个正式请求保存为 `生成请求/NN.json`。内置重试输入必须与该文件一致；已授权备用通道保存独立快照并记录与内置基线的差异。

使用 `review_state.py transition --state <file> --to <phase>` 更新阶段。跨越 `awaiting_plan_approval → reference_self_review` 或 `awaiting_reference_approval → scene_self_review` 时必须增加 `--user-approved`，表示已经收到用户明确批准。

工作区根目录可选创建 `.oh-my-folkstoryphoto-review.json`，内容为活动项目 `review-state.json` 的绝对或相对路径：

```json
{"state_file": "主题目录/review-state.json"}
```

可选 Stop hook 只读取这个指针；完成后保留指针也安全，因为 `complete`、`needs_user` 和两个 `awaiting_*` 阶段允许停止。

## 发布 manifest

```json
{
  "images": [
    {
      "number": 1,
      "source": "原始生成图/01.png",
      "focal_x": 0.5,
      "focal_y": 0.5
    }
  ]
}
```

制作期 manifest 可以是已通过图片的任意唯一子集，来源只能是状态中 `status: "pass"` 的 `final_source`。进入 `final_self_review` 或 `complete` 后，manifest 必须覆盖从 1 到 `planned_count` 的全部图号；焦点为可选 0–1 数值。

## v1/v2 兼容与迁移

`review_state.py validate` 继续读取 schema v1。只对用户明确指定的未完成项目运行：

```bash
python3 <skill-dir>/scripts/review_state.py migrate \
  --state /path/to/review-state.json \
  --to-version 2 \
  --planned-count N
```

先加 `--dry-run` 验证迁移。不得扫描、批量迁移或改写已完成项目。

未完成 v2 项目按同样原则升级至 v3：

```bash
python3 <skill-dir>/scripts/review_state.py migrate \
  --state /path/to/review-state.json --to-version 3 --dry-run
python3 <skill-dir>/scripts/review_state.py migrate \
  --state /path/to/review-state.json --to-version 3
```

正式迁移会先建立时间戳 v2 备份。已完成 v2 保持只读；`resume-probe`、参考板预授权和手动 `revise-request` 只允许 v2，不能写入 v3。

## 发布说明

必须包含标题、形式、尺寸、逐图字幕、简介、节奏、AI 声明，以及：

- `原典/传说来源：`
- `怪谈式解读：`
- `本篇改编：`
- `虚构与 AI 声明：`

不得把网络推测写成历史事实、学术定论或原典唯一解释。
