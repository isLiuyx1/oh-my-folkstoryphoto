# 完整工作流与状态

## 1. 输入与资料定位

接受原典、传说名称、网址、文章、PDF、截图、视频、本地素材、口述设定或视觉参考。为每份材料标记用途：

- **传统或事实来源**：用于文化背景和原典边界。
- **怪谈式解读**：地方口述、网络文章或现代推测，不当作学术定论。
- **视觉参考**：只提取拍摄行为、节奏、色调或构图，不照搬具体故事。
- **强制设定**：用户要求保留的角色、结局、场景与禁区。

来源足够时直接开展；只有缺失选择会显著改变故事时才提问。

## 2. 项目初始化

选择未被占用的主题目录，创建 [output-spec.md](output-spec.md) 规定的目录、`生成请求/` 和 schema v2 `review-state.json`。初始化时登记全部计划图号，未生成项使用 `status: "pending"`。状态必须按以下顺序推进：

`transport_guard.py` 与 `package_release.py` 需要 Pillow。若系统 `python3` 缺少 Pillow，在 Codex Desktop 先加载工作区依赖，再使用返回的捆绑 Python 路径；不要因依赖错误跳过预检。

```text
drafting
→ text_self_review
→ awaiting_plan_approval
→ reference_self_review
→ awaiting_reference_approval
→ scene_self_review
→ repairing
→ final_self_review
→ complete / needs_user
```

不得从 `drafting` 跳过文字确认直接生图。用户要求修改时退回当前门槛对应的审查阶段。

## 3. 文字方案

生图前完成：

1. 3–5 个标题备选和推荐标题。
2. 一句话核心矛盾。
3. 主角身份、动机、同行者和见证者。
4. 现实基线、异常入口、渐进发现、撤离、验证和最终证据。
5. 24–27 图证据链，每张只承担一个主要信息点。
6. 每图一句可后期排版的第一人称字幕。
7. 第一人称完整故事。
8. 人物、服装、道具、地点、季节和设备连续性。
9. 每张的可信拍摄事件。
10. 每张的最终提示词与参考图计划。
11. 发布简介、AI 声明、节奏和灵感来源说明。

进入 `text_self_review`，执行 [self-review.md](self-review.md)。通过后设为 `awaiting_plan_approval` 并停止。只有用户明确表达“方案确认”“可以出图”或同等含义才继续。

## 4. 参考图

方案确认后先读取 `$imagegen`：

- 生成主要角色单人/双人参考、固定服装与背包。
- 结构易漂移时生成车辆、船只或测绘装备参考。
- 为重复异常地点与最终奇观生成空间母版。
- 明确每张输入图是人物参考、地点母版、上一镜连续性参考还是编辑目标。

进入 `reference_self_review`。身份或地点失败时自动修正一次；仍失败设为 `needs_user`。通过后设为 `awaiting_reference_approval`，展示参考图并停止。

## 5. 正式生图

- 每个独立场景单独调用一次内置 imagegen。
- 调用前按下列三种范式构造参数，省略所有无值字段：

  **全新生图**

  ```json
  {"prompt": "<完整提示词>"}
  ```

  **参考生成**

  ```json
  {
    "prompt": "<完整提示词；逐一说明每张输入图的角色>",
    "referenced_image_paths": ["/absolute/existing/reference.png"]
  }
  ```

  **编辑现有图**

  先用 `view_image` 让编辑目标进入当前对话视觉上下文，再按 `$imagegen` 的内置编辑流程调用。若所有目标都有本地绝对路径，使用 `referenced_image_paths`；若目标只能来自最近对话图片，才使用最小必要值的 `num_last_images_to_include`。两种机制不得同时出现。

- 不得传 `referenced_image_paths: null`、`num_last_images_to_include: null`、空数组、空字符串或不存在的路径。
- 必需参考图调用前逐一确认文件存在。重试时不得为了让调用成功而删除、替换或降级必需参考图。
- 每次调用前把完整提示词保存到临时纯文本文件，并运行：

  ```bash
  python3 <skill-dir>/scripts/transport_guard.py preflight \
    --state /path/to/review-state.json \
    --number NN \
    --prompt-file /path/to/prompt.txt \
    --reference /absolute/reference-1.png
  ```

  无参考图时省略 `--reference`。该命令校验 PNG/JPEG 文件头、可解码性、尺寸、色彩模式与 SHA-256，并在 `生成请求/NN.json` 固化正式请求。内置重试若提示词或参考图发生变化，预检必须拒绝。
- 先生成普通现实场景，再生成异常入口与终局，尽早暴露身份或手机感问题。
- 有正脸时优先锁定已确认参考；多人优先侧脸、背影和中远景。
- 第一视角不得出现拍摄者本人，可出现结构正常的手、袖口、背包或工具。
- 不让模型生成承担语义的地图、文档、界面和精确日期。
- 将选中结果复制到 `原始生成图/NN.png`；不得只保留生成缓存路径。

## 6. 传输失败处理

将下列情况视为传输失败，而不是内容失败：

- 返回 `network error` 或等价网络/路由错误；
- 请求超时；
- 调用结束但没有返回可检查的候选文件。

按以下顺序处理：

1. 每次调用前都运行 `preflight`；它会累加持久化的 `attempts_total`，跨对话也不会重置。
2. 失败后立即运行：

   ```bash
   python3 <skill-dir>/scripts/transport_guard.py record-failure \
     --state /path/to/review-state.json \
     --number NN \
     --error-type network_error \
     --message "原始错误摘要"
   ```

   `--error-type` 只能是 `network_error`、`timeout` 或 `no_candidate`。
3. 前两次失败后该图回到 `pending`，保持同一请求继续；第三次后设为 `transport_blocked` 并打开单图熔断器。不要把项目阶段改为 `needs_user`，继续其他独立 `pending` 分镜。
4. 若两个不同分镜出现相同接口与错误类型，打开后端级熔断器，停止该后端的新批量调用并报告受影响图号。
5. 用户明确要求重试被熔断分镜时运行：

   ```bash
   python3 <skill-dir>/scripts/transport_guard.py resume-probe \
     --state /path/to/review-state.json --number NN --user-approved
   ```

   随后只允许一次 `preflight` 和一次内置调用。探测再失败就立即恢复熔断，不重新给予三次额度。
6. 返回候选图后先复制进项目，再运行 `record-success --candidate /project/原始生成图/NN.png`。该图进入 `review_pending`，单图熔断清除；成功探测同时清除对应后端熔断。

传输失败不得增加 `repair_count`、创建伪造返修文件、改变既有候选图的内容判定，或写入项目级 `blocking_reasons`。错误指纹由接口、错误类型、提示词哈希和有序参考图哈希组成；后端熔断另用接口与错误类型聚合。

### 6.1 备用通道

不得自行切换 CLI、API 或其他模型。只有用户明确授权后才运行：

```bash
python3 <skill-dir>/scripts/transport_guard.py authorize-fallback \
  --state /path/to/review-state.json \
  --backend cli_api \
  --model MODEL_ID \
  --user-approved
```

备用调用前仍运行 `preflight --backend cli_api --model MODEL_ID`。必须已有 `生成请求/NN.json` 作为内置请求基线；脚本将备用请求保存为独立快照，并明确记录提示词与参考图是否变化。不得以删参考图或弱化提示词作为规避传输错误的手段。

## 7. 自审与返修

生成联系表，并逐张查看原图。按 [quality-checklist.md](quality-checklist.md) 分类问题：

- 影响拍摄事件或整体摄影语言：整图重生成。
- 身份、空间和机位正确的局部瑕疵：编辑返修。
- 严重肢体、车辆或建筑结构错误：改变机位或重生成，不用涂抹遮掩。

原候选移入或复制到 `返修记录/NN-v1-问题.png`，修正版用 `NN-v2-修正版.png`。每个失败项只能自动修复一次。复审仍失败时设为 `needs_user` 并停止，不得静默选用旧图。

只有已经返回并可实际查看的候选图，其内容失败才算一次返修。网络错误、超时和无候选文件不属于返修。

## 8. 发布

所有图片通过后进入 `final_self_review`：

1. 检查普通相册测试、证据链比例、设备和角色连续性。
2. 制作期 manifest 可以只引用已通过图片；最终验收 manifest 必须包含全部计划图号。
3. 用 `review_state.py` 校验状态。
4. 用 `package_release.py --state /path/to/review-state.json` 生成 1080×1350 PNG 与联系表；状态不在 `final_self_review`/`complete`、存在缺图或 manifest 不完整时脚本必须拒绝。
5. 完成 `验收记录.md` 和 `自审记录.md`。
6. 将状态设为 `complete` 后再次校验。

内置工具持续失败时保留所有完成内容并报告；单图阻塞不妨碍其他独立分镜推进，但任何缺图都会阻止进入 `final_self_review` 和发布打包。
