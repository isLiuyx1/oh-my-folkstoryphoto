# 完整工作流与状态

## 目录

- [1. 输入与资料定位](#1-输入与资料定位)
- [2. 工作区与项目初始化](#2-工作区与项目初始化)
- [3. 真实性方向确认](#3-真实性方向确认)
- [4. 故事与第一次主创作确认](#4-故事与第一次主创作确认)
- [4. 分镜与第二次确认](#4-分镜与第二次确认)
- [5. 参考图与第三次确认](#5-参考图与第三次确认)
- [5. 正式生图](#5-正式生图)
- [6. 传输失败处理](#6-传输失败处理)
- [7. 自审、返修报告与统一返修](#7-自审返修报告与统一返修)
- [8. 发布](#8-发布)

## 1. 输入与资料定位

接受原典、传说名称、网址、文章、PDF、截图、视频、本地素材、口述设定或视觉参考。为每份材料标记用途：

- **传统或事实来源**：用于文化背景和原典边界。
- **怪谈式解读**：地方口述、网络文章或现代推测，不当作学术定论。
- **视觉参考**：只提取拍摄行为、节奏、色调或构图，不照搬具体故事。
- **强制设定**：用户要求保留的角色、结局、场景与禁区。

使用视觉参考时，在 `07-制作资料/01-创作方案.md` 写“参考作品拆解”，分别登记：可学习的叙事结构、节奏、证据载体和视觉语言，以及明确拒绝的字幕、平台 UI、具体人物、具体故事设定和质量问题。只转换抽象方法；不得复刻参考作品的角色、地点、道具、冲突或怪物设计。

来源足够时直接开展；只有缺失选择会显著改变故事时才提问。

## 2. 工作区与项目初始化

工作区根目录固定使用 `01-进行中项目`、`02-已完成作品`、`03-代表作品`、`04-创作管理`、`05-参考素材`、`06-规范与模板`、`07-技能与工具`、`08-临时文件`。新项目运行：

```bash
python3 <skill-dir>/scripts/workspace_layout.py init-project \
  --workspace /workspace --project-name <主题>
```

命令选择未占用的版本化名称，在 `01-进行中项目/` 创建schema v6项目并更新活动指针。第一阶段只创建 `00-真实性方案.md` 和状态文件；不得提前创建故事、分镜或图片任务。

用户明确要求整理旧工作区时，必须先预演：

```bash
python3 <skill-dir>/scripts/workspace_layout.py organize --workspace /workspace
```

预演按状态而非原文件夹名称分类：`phase=complete` 进入 `02-已完成作品/`，其余状态进入 `01-进行中项目/`；无状态旧项目只有同时存在最终发布目录和验收记录时才可判为完成。存在 `generating`、`active_attempt`、目标冲突或无法判定的旧项目时拒绝执行。预演无阻断且用户已授权整理后运行同一命令并加 `--apply`。执行前备份所有将被改写的文本状态，移动后同步更新绝对路径、跨项目引用和活动指针；校验失败自动回滚。该操作只重定位父目录，旧项目仍按原 schema 和内部目录继续，不做 v1–v3→v4 迁移。

schema v6状态按以下顺序推进：

`transport_guard.py` 与 `package_release.py` 需要 Pillow。若系统 `python3` 缺少 Pillow，在 Codex Desktop 先加载工作区依赖，再使用返回的捆绑 Python 路径；不要因依赖错误跳过预检。

```text
realism_self_review
→ awaiting_realism_approval
→ drafting
→ story_self_review
→ awaiting_story_approval
→ plan_self_review
→ awaiting_storyboard_approval
→ reference_self_review
→ awaiting_reference_approval
→ calibration_self_review
→ awaiting_calibration_approval
→ scene_generation
→ awaiting_first_review_decision
→ scene_self_review
→ awaiting_repair_approval
→ repairing
→ final_self_review
→ complete / needs_user
```

不得跳过真实性、故事、专业分镜、参考图或三张校准批准。用户修改已批准文件时先回退对应门槛。

## 3. 真实性方向确认

读取 [realism-profiles.md](realism-profiles.md)，根据题材向用户展示建议的主设备、最多两种辅助设备、三种现场状态和可选质感锚点。完成 `00-真实性方案.md` 后进入 `awaiting_realism_approval` 并停止。

用户明确批准后运行：

```bash
python3 <skill-dir>/scripts/review_state.py approve-realism \
  --state /project/08-系统文件/review-state.json --user-approved
```

命令保存真实性方案哈希并创建故事文件。没有明确批准不得采用默认手机风格继续。

## 4. 故事与第一次主创作确认

第一阶段只完成 `01-故事脚本.md`。内容包括推荐标题、核心矛盾、主角动机和第一人称完整故事；不生成分镜、字幕表、提示词、发布说明或参考任务。

进入 `story_self_review`，检查故事是否具备现实基线、异常入口、渐进发现、撤离、验证和最终证据。通过后设为 `awaiting_story_approval` 并停止。用户可直接编辑故事文件；收到明确批准后运行：

```bash
python3 <skill-dir>/scripts/review_state.py approve-story \
  --state /project/08-系统文件/review-state.json --user-approved
```

命令重新读取用户当前版本、保存 SHA-256，并进入 `plan_self_review`。

## 5. 分镜与第二次确认

故事批准后完成：

1. 3–5 个标题备选和推荐标题。
2. 一句话核心矛盾。
3. 主角身份、动机、同行者和见证者。
4. 现实基线、异常入口、渐进发现、撤离、验证和最终证据。
5. 默认 30–39 图长篇证据链，每张只承担一个主要信息点。用户明确指定其他图数时可覆盖默认值，并在创作方案记录原因。
6. 每图一句只供平台发布的第一人称发布字幕；非空且不超过48个可见字符，多数建议4–28字，补充画面看不见的时间、原因、选择、声音或反应。
7. 每图的场景原生文字：载体、精确内容、语言和是否关键证据；没有时写“无”。
8. 人物、服装、道具、地点、季节和设备连续性。
9. 每张的可信拍摄事件。
10. 每张的最终提示词与参考图计划。
11. 发布简介、AI 声明、节奏和灵感来源说明。

`07-制作资料/01-创作方案.md` 必须包含以下八段式节拍表；各段图数可在范围内调整，但总数默认保持 30–39：

| 段落 | 建议图数 | 叙事任务 |
|---|---:|---|
| 1. 人物锚点 | 3–4 | 建立主角日常、关系和现实处境 |
| 2. 可信来源 | 4–5 | 建立家庭、职业、知识或接触异常的资格 |
| 3. 异常入口 | 4–5 | 引入委托、遗物、传说或异常线索 |
| 4. 现实验证 | 4–5 | 定位地点并取得可复核的现实证据 |
| 5. 交叉印证 | 4–6 | 用不同证据载体确认异常不是单一误判 |
| 6. 阻力门槛 | 3–5 | 出现隐瞒、阻力、封锁或进入代价 |
| 7. 亲历升级 | 3–5 | 主角直接接触异常，危险逐步升级 |
| 8. 证据余波 | 2–4 | 给出最终证据、反转与事后余波 |

每 3–5 图必须出现一次新证据、因果转折或认知升级；超过五图的段落至少包含两个有效节拍。逐图执行删图测试：删除后若不影响因果、可信度、悬念或必要空间过渡，则合并或删除。同一地点、人物动作或异常表现不得连续重复两张以上，除非后一张新增可验证信息。

调查悬疑题材优先使用至少四类证据载体，例如生活相册、家族或历史档案、地图/文件、第三方记录、监控回放、现场照片和设备数据。每种证据必须在 AI 分镜中说明拍摄者或制作者、形成时间、获取途径和保留原因；不得只用不同滤镜伪装来源变化。

### 发布字幕写法

发布字幕采用声音卡中的说话方式，像当事人在那一刻说话，不写成诗、预告片文案、谜语、论文结论或抽象旁白。允许短句、停顿、改口和没说完，不强迫每句套入同一语法模板。

按八段式节拍选择字幕功能：

1. 人物锚点：交代“我是谁、他是谁、我们什么关系”。
2. 可信来源：说明人物的职业、经验或为什么有资格接触线索。
3. 异常入口：写清谁带来了什么委托、遗物、手稿或消息。
4. 现实验证：写清在哪里找到了什么具体东西。
5. 交叉印证：说明哪种第二来源又记录到什么现象。
6. 阻力门槛：写清谁采取了什么阻止、隐瞒或封锁动作。
7. 亲历升级：写清主角此刻看到、听到或遭遇的具体变化。
8. 证据余波：交代留下了什么结果、缺失、变化或反证。

逐组执行连续三图测试：相邻2–3张字幕要像同一个人的连续经历，可以使用前图已建立的代词。禁止“情况越来越不对劲”“我看到了无法解释的东西”等空洞悬念占位句。对每句再做朗读测试和删字幕测试。

字幕不应逐字描述画面已有信息，而应补充关系、时间、原因、来源或结果。例如画面已经能看见洞口，字幕应说明“谁在什么工作中挖到它”或“第二来源何时记录到变化”，而不是只写“地上有一个洞”。相邻字幕避免连续三句使用相同开头，如“后来”“然后”“那天”；连续性依靠因果推进，不依靠机械连接词。

根目录 `02-专业分镜表.md` 只用五列。schema v6 AI分镜必须使用采集配置ID、拍摄者、拍摄者入镜范围、设备可见性和校准角色；主配置至少覆盖一半，三种校准角色各且仅一张。

运行 `register-storyboard --planned-count N` 后才创建正式图片任务。文字自审通过后设为 `awaiting_storyboard_approval` 并停止；收到明确批准后运行 `approve-storyboard --user-approved`，同时固化两张分镜表哈希。

## 6. 参考图与第三次确认

方案确认后先读取 `$imagegen`：

- 生成主要角色单人/双人参考、固定服装与背包。
- 结构易漂移时生成车辆、船只或测绘装备参考。
- 为重复异常地点与最终奇观生成空间母版。
- 明确每张输入图是人物参考、地点母版、上一镜连续性参考还是编辑目标。

进入 `reference_self_review`。身份或地点失败时自动修正一次；仍失败设为 `needs_user`。通过后设为 `awaiting_reference_approval`，展示参考图并停止。

每项参考资产先登记并走统一传输状态机：

```bash
python3 <skill-dir>/scripts/review_state.py register-reference-job \
  --state /project/08-系统文件/review-state.json --reference-id hero-main \
  --kind character --output-dir 05-参考素材/01-角色参考

python3 <skill-dir>/scripts/transport_guard.py reference-preflight \
  --state /project/08-系统文件/review-state.json --reference-id hero-main \
  --prompt-file /path/to/reference-prompt.txt
```

使用 `record-reference-success` 或 `record-reference-failure` 登记传输结果，再用 `review_state.py record-reference-review --verdict pass|fail` 登记实际审查。首次内容失败时状态机保留首版并从原始锚点固化一次 regenerate；第二版仍失败才设为 `needs_user`。一个参考任务耗尽恢复阶梯后只阻塞该项；继续其他参考任务。所有已登记参考任务通过后进入 `awaiting_reference_approval` 并停止；收到明确批准后运行 `approve-references --user-approved`，保存每个批准候选的路径与哈希。

## 7. 三张真实性校准与正式生图

参考批准后进入 `calibration_self_review`。只允许生成普通基线、最差拍摄条件、首次重大异常三张正式分镜；其他图号预检必须失败。三张均通过结构化审查后生成校准联系表并停止。用户批准后，三张保留为正式通过图，schema v6进入 `scene_generation`。

其余原图只生成并登记候选路径，不查看图片、不验证画幅或内容。全部路径齐备后运行 `submit-originals-overview`，生成一张01–NN编号拼图；无法读取的图片显示编号占位格。随后停止于 `awaiting_first_review_decision`，由用户运行 `choose-first-review --mode full|selected|skip --user-approved` 选择全审、指定图号审查或跳过。

- schema v5/v6 `preflight` 必须同时提供 `--capture-id`、`--device-visibility`，每张参考提供同序 `--reference-role` 与 `--reference-kind identity|prop|location|capture_style`。
- authored prompt不得超过260字符或900字节；只写一个事件、一个受限机位、一至两种因果缺陷和最多四个关键排除项。固定反电影与参考安全条款由工具自动注入。

- 每个独立场景单独调用一次内置 imagegen。
- 每个正式提示词必须明确包含 `vertical 4:5` 或“竖版 4:5”，且不得包含 `landscape`、`horizontal`、`横版`、`3:2`、`16:9` 等冲突要求。`preflight` 对缺失或冲突画幅直接拒绝，不得依赖发布阶段才裁切。
- 开始或续跑前运行 `transport_guard.py batch-status`；一次只允许一个任务处于 `generating`。`preflight` 在没有活动批次、旧批次达到 3 张成功或持续 15 分钟时自动创建下一批。
- 批次边界只用于限流与审计，不是用户确认门槛。传输失败不结束批次；有自动恢复档位时立即固化下一档请求，否则延后当前项并继续下一项。
- 尚无候选的正式原图若使用两张独立参考，硬超时为480秒；0–1张自然参考、单文件参考板和返修调用为600秒。v3–v5根据固化开始时间计算实际耗时。
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
- 必需参考图调用前逐一确认文件存在。新请求以 0–1 张为常态、最多 2 张：无连续性锚点的环境/物证镜头使用 0 张；有人物时优先只含本镜必要人物的自然同框图，再考虑已通过相邻镜头或单人物/单地点母版；第二张只用于主锚点缺失的关键地点、物件或异常结构。参考板只能作为下述已授权的双参考超时降级，不能在首次请求中预先制作或绕过来源数量限制。
- 选择参考图时必须同时读取项目的 `参考图说明.md`（或等价清单）和本镜出镜表，逐一核对姓名、固定服装与用途；不得仅凭画面猜身份。每个 `--reference` 必须带一个同序 `--reference-role`，例如 `--reference-role "赵克明、林峤身份与车辆结构"`。预检会保存角色说明并在数量不匹配时拒绝。
- 新项目最多使用两张独立参考；三张以上先在分镜规划阶段缩减到必要的两张。自动恢复不删参考来源，只把两个逻辑来源合成一个物理附件。
- `preflight` 返回物理附件数、逻辑来源数、参考种类与 `reference_summary.latency_risk`。两张独立参考或一个双来源参考板标记为 `elevated`；3 张及以上或总输入达到约 8 MiB 标记为 `high`。新建精确局部编辑请求时，只输入编辑目标和确实参与被改区域重建的参考图；身份、车辆或地点若已在编辑目标中清晰可见且只需保持不变，应写入提示词不变量，而不是重复上传。请求快照一旦固化，重试不得据此静默删图。
- 新请求在硬限制之外再使用软预算：参考图优先最长边不超过约 1024 px，总输入尽量不超过约 1.5 MiB，并排除本镜不需要的人物和复杂背景。软预算用于降低视觉编码长尾，不是质量降级命令；身份敏感镜头不得直接改成零参考。
- schema v5/v6 authored prompt使用260字符/900字节硬预算；旧schema继续输出原有软预算诊断。任何schema的传输恢复都不得修改已固化提示词。
- 每条新正式提示词增加 `Scene-native text`：没有文字要求时写 `none required`；有文字时写明载体、逐字内容、语言和是否关键证据。允许实景招牌、文件、标签、设备读数、剧情合理的时间戳、虚构品牌和设备屏内 UI；禁止发布字幕、说明标题、水印、平台 UI 浮层、假相机 HUD、真实品牌名和 Logo。AI 分镜中的发布字幕不得自动复制进该字段。
- 自动恢复单参考时由状态机调用 `scripts/optimize_reference.py`，只做全图缩放和压缩，不传 `--crop`：

  ```bash
  python3 <skill-dir>/scripts/optimize_reference.py \
    --input /project/05-参考素材/01-角色参考/approved.png \
    --output /project/08-系统文件/01-生成请求/派生参考/scene-NN.jpg \
    --max-edge 1024 --jpeg-quality 88
  ```

  第一档最长边 1024/JPEG 88，第二档最长边 768/JPEG 80。脚本拒绝覆盖并输出源/派生哈希、尺寸和字节数；状态机自动归档旧请求并保持提示词不变。

### 5.1 双参考自动降级

首次双参考请求在提示词中原样加入固定句，使自动切换参考板时不改变提示词哈希：

```text
Reference-board safety: references are continuity anchors only; never copy panel layout, seams, gutters, source composition, duplicated subjects, or extra people.
```

任何双参考或参考板请求缺少该句时 `preflight` 拒绝。`timeout`/`no_candidate` 后状态机自动执行：

1. 从两张批准原图全幅生成 1024×1280/JPEG 88 参考板；
2. 再次失败时仍从两张批准原图全幅重建 768×960/JPEG 80 参考板；
3. 第三档仍失败时标为 `transport_blocked` 并继续队列。

两档参考板都保存 `.jpg.json` sidecar，记录原始路径、顺序、角色、尺寸、参数和 SHA-256。禁止从第一档参考板递归生成第二档。旧项目仍可使用 `authorize-reference-board-policy` 和 `stage-reference-board-fallback`，新项目不再需要。
- 每次调用前把完整提示词保存到临时纯文本文件，并运行：

  ```bash
  python3 <skill-dir>/scripts/transport_guard.py preflight \
    --state /project/08-系统文件/review-state.json \
    --number NN \
    --prompt-file /path/to/prompt.txt \
    --reference /absolute/reference-1.png \
    --reference-role "本图实际人物姓名与锚定内容"
  ```

  无参考图时省略 `--reference`。该命令校验 PNG/JPEG 文件头、可解码性、尺寸、色彩模式与 SHA-256，并在 `08-系统文件/01-生成请求/NN.json` 固化 v4 请求。内置重试若提示词或参考图发生变化，预检必须拒绝。
- 重试或恢复探测不得用会自动添加换行的文本提取命令重建提示词。必须从既有请求快照逐字节物化：

  ```bash
  python3 <skill-dir>/scripts/transport_guard.py materialize-prompt \
    --state /project/08-系统文件/review-state.json \
    --number NN \
    --output /new/nonexistent/path/NN-prompt.txt
  ```

  `materialize-prompt` 校验快照内提示词哈希并拒绝覆盖已有输出；随后把该文件传给 `preflight`。
- 先生成普通现实场景，再生成异常入口与终局，尽早暴露身份或手机感问题。
- 有正脸时优先锁定已确认参考；多人优先侧脸、背影和中远景。
- 第一视角不得出现拍摄者本人，可出现结构正常的手、袖口、背包或工具。
- 不让模型生成承担语义的地图、文档、界面和精确日期。
- 模型返回后先检查方向和画面安全区。精确 4:5 可直接复制；常见的竖版 2:3 原始输出可运行 `scripts/normalize_candidate.py --input RAW --output 06-生成过程/01-原始生成图/NN.png` 做居中或 `--focal-y` 指定焦点的非破坏性裁切。横版输出必须重生成，禁止裁成竖版。`record-success` 会拒绝任何非精确 4:5 候选。
- 横向监控、旧照片、地图或电脑画面只能作为现实场景中的实体载体被竖版拍摄；不得直接把横向画面贴入 4:5 成片制造上下黑边。同一证据来源保持设备性格一致；后段可更暗、更匆忙，但不得突然变成电影剧照、游戏截图或专业恐怖海报。
- 将精确 4:5 结果复制到 `06-生成过程/01-原始生成图/NN.png`；不得只保留生成缓存路径。

## 6. 传输失败处理

将下列情况视为传输失败，而不是内容失败：

- 返回 `network error` 或等价网络/路由错误；
- 请求超时；
- 调用结束但没有返回可检查的候选文件。

按以下顺序处理：

1. 每次调用前都运行 `preflight`；它会累加持久化的 `attempts_total`，记录唯一尝试 ID、开始时间、请求指纹、参考图数量/尺寸/总字节数，并保存可恢复的尝试前状态。跨对话不会重置。
2. 失败后立即运行：

   ```bash
   python3 <skill-dir>/scripts/transport_guard.py record-failure \
     --state /project/08-系统文件/review-state.json \
     --number NN \
     --error-type network_error \
     --message "原始错误摘要"
   ```

   `--error-type` 只能是 `network_error`、`timeout` 或 `no_candidate`。
3. `timeout` 或 `no_candidate` 且存在 1–2 张参考时，`record-failure` 先结束在途尝试并释放全局锁，再以幂等事务生成下一档派生输入、归档旧请求并返回 `retry_ready: true`；立即用返回的 `next_references` 和 `next_reference_roles` 重新预检，不冷却、不请求批准。派生失败只把当前项设为 `transport_blocked`。
4. `network_error` 不改变输入；当前项进入约 2 分钟、10 分钟的延后队列，同时继续其他可运行项。零参考请求也按此方式延后。
5. 第三次失败且没有下一档自动恢复时设为 `transport_blocked`。两个不同任务的相同错误只写入 `backend_health_warning`，不阻止后续请求。
6. 返回候选图后先复制进项目，再运行 `record-success --candidate /project/06-生成过程/01-原始生成图/NN.png`。该图进入 `review_pending`。

传输失败不得增加 `repair_count`、创建伪造返修文件、改变既有候选图的内容判定，或写入项目级 `blocking_reasons`。自动派生必须记录原始/派生哈希、参数、请求版本和触发错误。

### 6.1 调用中断恢复

若内置调用被用户中止、回合中断或工具超时，`preflight` 可能已写入 `generating`。恢复前必须检查项目候选目录和工具返回，确认没有候选文件；有候选时应复制回项目并运行 `record-success`，不得使用中断恢复。

生成缓存可能同时包含其他任务的输出。只检查当前任务可确认归属的缓存目录或工具明确返回的文件；全局搜索结果不得仅凭时间戳认领。迟到文件必须先实际查看并与当前分镜、提示词和参考角色核对，内容不符时视为其他任务产物。

确认无候选后运行：

```bash
python3 <skill-dir>/scripts/transport_guard.py recover-interrupted \
  --state /project/08-系统文件/review-state.json \
  --number NN \
  --reason turn_interrupted \
  --confirm-no-candidate
```

参考任务把 `--number NN` 改为 `--reference-id ID`。中断不会结束活动批次；恢复后继续当前阶段的下一项。

`--reason` 只能是 `user_abort`、`turn_interrupted` 或 `tool_timeout`。普通尝试恢复为 `pending`；探测恢复为原来的 `transport_blocked` 和熔断状态。中断会写入尝试历史，但不增加 `consecutive_failures` 或 `repair_count`。

### 6.2 小批次与状态诊断

```bash
python3 <skill-dir>/scripts/transport_guard.py batch-status \
  --state /project/08-系统文件/review-state.json

```

`batch-status` 只把当前阶段允许的任务列为可运行：`reference_self_review` 为参考任务、schema v6的 `scene_generation`（旧schema的 `scene_self_review`）为正式原图、`repairing` 为已批准返修。它同时报告在途、冷却、恢复事务、阻塞项及非阻断性后端健康告警。`preflight` 自动滚动批次；`batch-start` 仅为旧项目和人工诊断保留。

全部可运行任务结束且仍有 `transport_blocked` 时运行：

```bash
python3 <skill-dir>/scripts/transport_guard.py prepare-blocked-report \
  --state /project/08-系统文件/review-state.json
```

报告生成前不得因单图阻塞中止整个队列。

### 6.3 备用通道

不得自行切换 CLI、API 或其他模型。只有用户明确授权后才运行：

```bash
python3 <skill-dir>/scripts/transport_guard.py authorize-fallback \
  --state /project/08-系统文件/review-state.json \
  --backend cli_api \
  --model MODEL_ID \
  --user-approved
```

备用调用前仍运行 `preflight --backend cli_api --model MODEL_ID`。v4 必须已有 `08-系统文件/01-生成请求/NN.json` 作为内置请求基线；脚本将备用请求保存为独立快照，并明确记录提示词与参考图是否变化。不得以删参考图或弱化提示词作为规避传输错误的手段。

没有 API Key、但 Codex CLI 已用 ChatGPT 订阅登录时，可在用户明确授权后使用订阅 bridge：

```bash
python3 <skill-dir>/scripts/transport_guard.py authorize-fallback \
  --state /project/08-系统文件/review-state.json \
  --backend codex_subscription_bridge --model gpt-image-2 --user-approved

python3 <skill-dir>/scripts/transport_guard.py preflight \
  --state /project/08-系统文件/review-state.json --number NN \
  --backend codex_subscription_bridge --model gpt-image-2 \
  --prompt-file /path/to/prompt.txt --reference /path/to/reference.png

python3 <skill-dir>/scripts/subscription_image_bridge.py \
  --prompt-file /path/to/prompt.txt \
  --reference /path/to/reference.png \
  --output /project/06-生成过程/01-原始生成图/NN.png \
  --log /project/08-系统文件/01-生成请求/NN-subscription-bridge.log \
  --size 1024x1280 --timeout-seconds 480
```

该 bridge 使用新的临时 `codex exec` 和任务专属输出，可改善长对话回传与缓存串图，但仍使用 ChatGPT/Codex 订阅图片服务，不能保证消除服务端长尾。它必须使用 `--ephemeral`、忽略项目规则、低推理强度、固定输出路径和最多 2 张 `-i` 附件。失败仍按传输失败处理，但不得结束整个批次或队列。

## 7. 用户选择后的审查、返修报告与统一返修

仅在用户选择全审或局部审查后查看相应原图。未选图片直接通过；跳过模式不执行内容审查。按 [quality-checklist.md](quality-checklist.md) 分类问题：

- 影响拍摄事件或整体摄影语言：整图重生成。
- 身份、空间和机位正确的局部瑕疵：编辑返修。
- 严重肢体、车辆或建筑结构错误：改变机位或重生成，不用涂抹遮掩。

原候选移入或复制到 `06-生成过程/02-返修记录/NN-v1-问题.png`，修正版用 `NN-v2-修正版.png`。每个失败项只能自动修复一次。复审仍失败时设为 `needs_user` 并停止，不得静默选用旧图。

只有已经返回并可实际查看的候选图，其内容失败才算一次返修。网络错误、超时和无候选文件不属于返修。

返修生图仍必须经过传输预检，增加 `--repair-mode edit` 或 `--repair-mode regenerate`。脚本允许 `review_pending` 原候选进入一次返修，为其保存独立的 `08-系统文件/01-生成请求/NN-repair.json`；传输失败会恢复为 `review_pending`，不会丢失原候选。`edit` 的自动恢复始终保持编辑目标为第一张独立附件，并把目标与支持图分别按 1024/88、768/80 两档全图压缩，永不拼板或转成 regenerate。返修候选成功返回后，`record-success` 保留原始 `candidate`，并登记 `repair_count: 1`、`repair_mode` 和 `repair_file`。

用户选择审查后：通过项运行 `mark-pass`；失败项运行 `queue-repair` 记录问题、建议方式和摄影红旗。不得在原图生成期间自动审查或立即返修。

只有用户选择的图片完成首审后才能运行：

```bash
python3 <skill-dir>/scripts/review_state.py prepare-repair-report \
  --state /project/08-系统文件/review-state.json
```

存在待返修项时状态进入 `awaiting_repair_approval` 并停止。用户阅读报告并明确批准后运行：

```bash
python3 <skill-dir>/scripts/review_state.py authorize-repairs \
  --state /project/08-系统文件/review-state.json --number NN --user-approved
```

省略 `--number` 表示批准报告中的全部项目。只有获批图号可进入 `preflight --repair-mode`。获批返修发生 `timeout`/`no_candidate` 后自动使用同一参考降级阶梯，无需再次批准输入优化。旧项目仍可使用：

```bash
python3 <skill-dir>/scripts/transport_guard.py supersede-repair \
  --state /project/08-系统文件/review-state.json \
  --number NN \
  --reason "具体诊断与拟调整内容" \
  --user-approved
```

命令把旧请求非破坏性归档为版本化快照，并在状态中保存原提示词/参考哈希和原因；随后才可用新提示词与参考计划重新 `preflight --repair-mode`。不得用于绕过内容质量、删除仍参与被改区域重建的必需参考或修改原始正式请求。

原图或返修图复审通过后，用确定性命令登记：

```bash
python3 <skill-dir>/scripts/review_state.py mark-pass \
  --state /project/08-系统文件/review-state.json \
  --number NN \
  --notes "兼容字段；v5以审查JSON为准" \
  --review-file /project/08-系统文件/03-真实性审查/NN.json
```

schema v5/v6审查JSON逐项填写机位物理、计划外设备、采集配置、非电影化、身份、关键道具、连续性和缺陷因果。任一硬项失败都不设置 `final_source`；返修版本仍失败时自动进入 `needs_user`。历史候选审查保存在 `candidate_versions`，不得被清空。

## 8. 发布

所有图片通过后进入 `final_self_review`：

1. 检查普通相册测试、证据链比例、设备和角色连续性。
2. 制作期 manifest 可以只引用已通过图片；最终验收 manifest 必须包含全部计划图号。
3. 用 `review_state.py` 校验状态。
4. 用 `package_release.py --state /project/08-系统文件/review-state.json` 生成 1080×1350 PNG 与联系表；v4 输出必须等于状态中的 `04-最终发布版-N图`，存在缺图或 manifest 不完整时脚本必须拒绝。
5. 完成 `验收记录.md` 和 `自审记录.md`。
6. 将状态设为 `complete` 后再次校验。

## 8. 已完成项目的文字专修

只修故事和字幕时，不重走生图门槛：

```bash
python3 scripts/review_state.py start-text-revision --state <review-state.json>
# 只修故事、发布说明、AI分镜的发布字幕列
python3 scripts/text_audit.py --story <01-故事脚本.md> --publication <03-发布文件说明.md> --storyboard <02-AI生成分镜.md>
python3 scripts/review_state.py submit-text-revision --state <review-state.json>
```

`submit-text-revision` 会硬校验图号、空字幕、48字上限、两份字幕同步，并确认图片记录、参考资产、manifest与最终PNG的哈希没变。它还会报告公式句、统一句长、技术词来源和可能未回收的线索，但不自动改写。

提交后停在 `awaiting_text_revision_approval`。用户批准后运行 `approve-text-revision --user-approved`；用户不满意时运行 `revert-text-revision`。

内置工具持续失败时保留所有完成内容并报告；单图阻塞不妨碍其他独立分镜推进，但任何缺图都会阻止进入 `final_self_review` 和发布打包。
