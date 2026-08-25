---
name: oh-my-folkstoryphoto
description: Create and organize Chinese folk-story, legend, mystery, horror, wonder, phone mockumentary, old-DV found-footage, AI photorealistic, Douyin or Xiaohongshu photo-carousels. Use when Codex needs source adaptation, user-approved capture realism, story-first planning, 30–39-frame evidence chains, three-shot calibration, built-in image generation, a user-chosen full/selected/skipped first review, one approved repair round, and release packaging.
---

# oh-my-FolkStoryPhoto

把用户的文字、图片、文件、网址或传说灵感制作成可发布的中文伪纪录图文。默认30–39张竖版4:5，无发布字幕叠图；发布时保留AI生成声明。

## 不可违反

- 先区分原典、现代解读、视觉参考、用户强制设定和本篇虚构。视觉参考只学习节奏、证据、拍摄行为与采集质感，不复制人物、地点、道具、字幕或故事。
- 新项目使用 `workspace_layout.py init-project` 创建schema v6独立目录，不覆盖同名项目；旧schema不自动迁移。
- 保留五个停止门槛：真实性方向、故事、专业分镜、参考图、三张真实性校准。每次必须收到明确批准并保存文件哈希；沉默、含糊肯定和“先看看”不算批准。
- 返修报告另需明确批准。先完成全部原图并展示编号总览，再由用户选择全审、指定图号审查或跳过；每图最多一次内容返修，返修后仍有硬失败进入 `needs_user`。
- 当前采集设备作为第一视角时不得完整入镜；需要设备入镜必须登记另一采集源。拍摄者完整入镜必须有镜面、定时、固定跌落、接拍或第二设备等物理解释。
- 图片不得因为奇观升级成电影剧照、游戏截图、概念图或商业摄影。机位不可能、计划外设备、电影化、人物错误、关键道具错误中的任一项都是硬失败。
- 正式图使用 `$imagegen`，每个场景独立串行调用。执行图片生成或编辑前完整读取 `$imagegen` 的 `SKILL.md`。
- 正式图必须精确竖版4:5。横图重生成；竖版2:3只可在安全区小幅规范化。横向监控、旧照片或DV画面作为场景内实体被竖拍，不加黑边填充。
- 画面只允许剧情成立的场景原生文字；禁止发布字幕、标题、水印、平台UI浮层和假相机HUD。真实品牌与Logo替换为虚构品牌。
- 内置生图调用省略空字段。全新图只传 `prompt`；参考图只传 `prompt` 与真实存在的 `referenced_image_paths`；不得传null、空数组或两种图片上下文机制。
- 调用前用 `transport_guard.py preflight` 固化提示词、采集配置、参考类型与哈希。所有调用串行；每批最多成功3张或15分钟。
- 正式请求以0–1张参考为常态、最多2张。每张参考登记同序 `--reference-role` 与 `--reference-kind identity|prop|location|capture_style`。三张以上必须说明不可替代性并取得批准。
- 网络错误、超时和无候选不算内容返修。按现有恢复阶梯处理；不得为重试删除身份参考、改提示词、降低连续性或擅自切换模型/API。
- 项目图片复制回项目目录。原图、返修和发布版分开保存，返修不得覆盖原图。

## 开始前读取

必须完整读取：

- [workflow.md](references/workflow.md)：状态、审批、生成、恢复与发布。
- [realism-profiles.md](references/realism-profiles.md)：八类设备、组合规则与质感锚点。
- [capture-authenticity.md](references/capture-authenticity.md)：可信拍摄事件、视角物理与三张校准。
- [visual-language.md](references/visual-language.md)：长篇节拍、字幕与短提示词。
- [human-writing.md](references/human-writing.md)：声音卡、知识边界、连续三图与去公式化文字规则。
- [self-review.md](references/self-review.md)：各阶段自审与返修闭环。
- [output-spec.md](references/output-spec.md)：schema v6目录、字段、用户审查选择和批准哈希。
- [quality-checklist.md](references/quality-checklist.md)：硬失败、结构化审查与整篇验收。

## 1. 初始化与真实性确认

运行：

```bash
python3 <skill-dir>/scripts/workspace_layout.py init-project \
  --workspace <工作区> --project-name <主题>
```

初始化只创建 `00-真实性方案.md` 和状态文件。根据题材向用户展示建议方案，登记：

- 一种主采集设备与最多两种有剧情来源的辅助设备；
- 年代、持有者、携带/拍摄原因、原始比例与横竖拍规则；
- 稳定、受限、失控三种状态及其因果画质；
- 年代合理的时间码、`PLAY`、电量等原生叠字策略；
- 非摄影例外与本地质感锚点。

真实性JSON中主设备 `role` 必须写 `primary`，辅助设备必须写 `secondary`；`device_profile` 必须逐字使用 [realism-profiles.md](references/realism-profiles.md) 表内八个档案名称。进入 `awaiting_realism_approval` 前先运行状态验证，失败时修正方案，不得把无效状态留给用户。

主设备必须承担至少一半正式图。真实性自审后进入 `awaiting_realism_approval` 并停止。用户批准后运行 `approve-realism --user-approved`；命令保存哈希并创建 `01-故事脚本.md`。不得静默采用默认手机风格继续。

## 2. 故事与第一次主创作确认

只完成故事脚本：推荐标题、核心矛盾、主角动机、现实基线、异常入口、升级、撤离、验证、结局和第一人称完整故事。不得提前创建分镜、提示词或参考任务。

自审后进入 `awaiting_story_approval` 并停止；批准后运行 `approve-story --user-approved`。

## 3. 分镜与第二次主创作确认

故事批准后创建：

- 根目录五列专业分镜；
- 发布文件说明；
- 制作资料中的八段式创作方案、schema v6 AI分镜、视觉设定、短提示词和参考说明。

默认30–39图，每3–5图至少一个新证据、因果转折或认知升级。每图只承担一个主要信息点；删除后不影响因果、可信度、悬念或空间过渡的镜头合并或删除。

AI分镜必须登记 `采集配置ID`、`拍摄者`、`拍摄者入镜范围`、`设备可见性` 和 `校准角色`。设备可见性只能为：

- `不可见`
- `仅自然边缘/固定支架`
- `由另一采集源拍到`，并写 `第二拍摄源:配置ID`

普通基线、最差拍摄条件、首次重大异常各且仅指定一张校准图。运行 `register-storyboard --planned-count N`；自审后进入 `awaiting_storyboard_approval` 并停止，批准后运行 `approve-storyboard --user-approved`。

## 4. 参考图与第三次主创作确认

进入 `reference_self_review` 后登记并生成必需的角色身份、固定服装/道具、重复地点和关键空间母版。干净母版只锁定身份、结构或空间，不承担最终摄影风格。

每项参考使用 `register-reference-job`、`reference-preflight`、`record-reference-success|failure` 和 `record-reference-review`。首版内容失败可从原始来源重生一次；第二版仍失败进入 `needs_user`。全部通过后展示参考图并停止；用户批准后运行 `approve-references --user-approved`，进入校准阶段。

## 5. 三张真实性校准

参考批准后只生成三张登记的校准图，使用正式提示词、正式参考、正式输出路径和正式结构化审查。其他图号预检必须拒绝。

运行 `python3 scripts/calibration_sheet.py --state <state>` 制作 `06-生成过程/00-真实性校准/真实性校准联系表.jpg`，并列展示候选、对应质感锚点、采集配置、预期缺陷和审查结论。运行 `submit-calibration` 后停止；用户批准时运行 `approve-calibration --user-approved`。三张图直接成为正式通过图。

未批准时只调整共享真实性方案、参考角色或提示词规则；`reopen-gate --gate calibration`最多重跑一次，第二次仍失败进入 `needs_user`。

## 6. 正式生图、总览与用户审查选择

schema v6 authored prompt必须：

- 不超过260字符或900字节；
- 明确“竖版4:5”；
- 只含一个拍摄事件、一个受限机位、一至两种因果缺陷和最多四项本镜排除项；
- 不含“电影感、史诗、英雄机位、戏剧性光线、HDR、商业摄影、概念图”等美学诱导词；
- 不写“某人拿着DV拍”来表达视角，拍摄者和设备位置使用结构化字段。

固定反电影和参考安全条款由 `transport_guard.py` 自动注入。`preflight` 必须提供 `--capture-id`、`--device-visibility` 以及每张参考的角色和类型。重新生成返修必须继承原请求的身份参考哈希。

校准批准后，其余原图只串行生成并登记候选，不查看图片，也不做尺寸或内容审查。全部候选路径齐备后运行：

```bash
python3 <skill-dir>/scripts/review_state.py submit-originals-overview --state <state>
```

展示 `06-生成过程/03-当前总览/首轮原图总览.jpg` 后停止。用户必须明确选择：

```bash
# 全审
python3 <skill-dir>/scripts/review_state.py choose-first-review --state <state> --mode full --user-approved
# 只审指定图号，其余直接通过
python3 <skill-dir>/scripts/review_state.py choose-first-review --state <state> --mode selected --number 03 --number 12 --user-approved
# 完全跳过，全部直接通过
python3 <skill-dir>/scripts/review_state.py choose-first-review --state <state> --mode skip --user-approved
```

只有全审或指定审查的图片才查看原尺寸与联系表，并写入 `08-系统文件/03-真实性审查/NN.json`：

从 `assets/authenticity-review-template.json` 复制审查结构，只修改实际结论，不删字段。

```bash
python3 <skill-dir>/scripts/review_state.py mark-pass \
  --state <state> --number NN --notes "兼容字段" \
  --review-file <project>/08-系统文件/03-真实性审查/NN.json
```

审查JSON必须逐项填写机位物理、计划外设备、采集配置、非电影化、身份、关键道具、连续性和缺陷因果。历史候选与审查不可清空。

首版失败用 `queue-repair`登记，不立即返修。所选图审查完成后运行 `prepare-repair-report`并停止；用户批准指定图号后运行 `authorize-repairs --user-approved`，再统一返修。跳过模式直接进入最终发布准备，不创建首审或返修报告。

## 7. 全篇验收与发布

全审或局部审查模式按故事顺序检查被选图片；跳过模式不再逐图检查内容。

全部图片为 `pass` 后创建manifest，运行状态验证与 `package_release.py`。打包遇到缺失、不可解码或无法输出1080×1350的图片时只报告技术阻塞。完成自审记录、验收记录和AI声明后进入 `complete`。

已完成项目如果只修故事和字幕，使用 `start-text-revision`，不得重开故事或分镜门槛。文字专修只允许改 `01-故事脚本.md`、`03-发布文件说明.md` 和 AI 分镜的“发布字幕”列。通过 `submit-text-revision` 后必须停在 `awaiting_text_revision_approval`；用户批准后运行 `approve-text-revision --user-approved`，不满意则用 `revert-text-revision`。该流程不得清空图片任务、参考资产、manifest或逐图审查。

## 覆盖与安全

- 用户指定气质、图数和视觉参考优先，但不得取消真实性、三次主创作、校准或返修门槛。
- 非恐怖题材不添加鬼脸、尸体或邪教；恐怖题材默认克制，完整怪物不是必选项。
- 用户禁止联网时只用其材料并说明未外部核验。
- 当前传送枪、诺亚方舟及其他旧项目继续按原schema保存；不得因技能升级自动重写或删除。
