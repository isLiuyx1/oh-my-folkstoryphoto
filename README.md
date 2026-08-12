# oh-my-FolkStoryPhoto

一个面向 Codex 的中文民间故事手机或旧 DV 伪纪录图文技能。它把传说、怪谈、志怪、地方口述或用户提供的灵感，默认整理成 30–39 张 4:5 长篇图文轮播。v5 在写故事前先确认整篇的采集真实性方向，并在正式批量生成前用三张代表镜头校准最终质感。

核心原则：每张图首先要像一个真实的人在当时条件下自然拍下并保留的私人照片，其次才承担悬疑、怪谈或奇观叙事。真实感来自拍摄者、拍摄原因、受限机位、人物行为和设备能力，而不是统一噪点、假日期戳、复古滤镜或电影化调色。

## v5.0 更新

- 新增必须由用户批准的 `00-真实性方案.md`：先确定主设备、最多两种有剧情来源的辅助设备、拍摄者、年代、画幅和稳定/受限/失控三种现场状态，再开始写故事。
- 内置八类设备档案和六张匿名质感锚点；锚点只学习采集缺陷，不复制人物、地点、动作、文字或构图。
- 新增“普通基线、最差拍摄条件、首次重大异常”三张真实性校准。校准图使用正式请求和正式输出路径，通过后直接成为成片，不重复生成。
- 新增第一视角物理约束：当前拍摄设备不能无故完整入镜；人物或设备需要入镜时必须有镜面、固定机位、第二采集源等成立的拍摄解释。
- AI 生产分镜升级为 schema v5，登记采集配置、拍摄者入镜范围、设备可见性和校准角色；主设备必须覆盖至少一半正式图。
- 正式提示词采用 260 字符/900 字节硬预算，并拒绝电影感、史诗、英雄机位、HDR、商业摄影和概念图等诱导词。
- 每张候选新增结构化真实性审查 JSON。机位物理、计划外设备、采集配置、非电影化、身份和关键道具任一失败都不能标记通过。
- 保留并整合 v4.1–v4.4 的目录治理、场景原生文字、清晰第一人称发布字幕、30–39 图八段式证据链、自动传输降级和连续批次能力。
- schema v1–v4 项目保持兼容读取和原目录，不自动迁移或重写。

## 主要能力

- 区分原典、现代解读、视觉参考、用户强制设定和本篇虚构。
- 先批准拍摄真实性方向，再建立年代可信的设备档案与 30–39 图长篇证据链。
- 用三张正式镜头校准普通状态、最差条件和首次重大异常，避免整批生成后才发现质感偏差。
- 对第一视角设备、拍摄者入镜和第二采集源执行物理一致性校验。
- 使用八段式节拍、每 3–5 图一次认知升级和多种证据载体交叉验证，删除不推进因果或可信度的重复镜头。
- 发布字幕采用口语化第一人称和“具体主语＋具体事件＋本图新信息”，逐句通过独立阅读测试，避免空洞悬念和无指向代词。
- 第一阶段只交付真实性方案；批准后才创建可直接编辑的故事脚本，确认故事后再规划分镜。
- 同时维护清晰的五列专业分镜和完整的 AI 生产分镜，两者用途分离。
- 允许招牌、屏幕、文件、设备读数和虚构品牌等场景原生文字承担证据，同时禁止把发布字幕和平台浮层叠入图片。
- 在真实性方向、故事、专业分镜、角色/地点母版和三张校准后分别等待用户明确确认。
- 使用连续的 `01–08` 编号目录分离用户文档、成片、素材、生成过程、制作资料和系统状态。
- 使用连续编号的工作区目录区分进行中、已完成、代表作、创作管理、参考素材、规范、工具和临时文件。
- 提供可预演、可备份、可回滚的旧项目安全整理，自动更新绝对路径、跨项目引用和活动指针。
- 逐图记录拍摄来源、拍摄原因、人物意识、受限机位和手机成像结果。
- 对文稿、参考图、单图和整篇相册执行“自审—自动修订—再次验收”，并保存逐图结构化真实性审查。
- 传输阻塞时自动降级参考输入，单项失败后记录并跳过，继续其他可运行图片。
- 参考图支持一次内容返修；正式返修仍保留用户批准门槛和单轮上限。
- 将网络错误、超时、无候选和后端健康告警分离，持久化请求指纹、恢复事务、派生哈希和失败历史。
- 自动创建和滚动生图批次，每 3 张成功或 15 分钟换批，不增加人工确认。
- 校验机器状态与发布清单，输出 1080×1350 PNG 和联系表。
- 提供可选 Codex `Stop` hook，防止在自审或返修尚未完成时提前结束。

## 作品展示

以下是使用本工作流创作的单帧示例。它们展示的是拍摄者位置、偶然遮挡、设备限制和私人相册感，不代表完整项目的全部分镜。

<table>
  <tr>
    <td width="50%" align="center">
      <img src="docs/showcase/liminal-space.jpg" alt="阈限空间中的受限窥视机位" width="100%">
      <br><strong>阈限空间</strong>：从狭窄遮挡后记录重复空间
    </td>
    <td width="50%" align="center">
      <img src="docs/showcase/peach-blossom-spring.jpg" alt="船上回收无人机的第一视角照片" width="100%">
      <br><strong>桃花源记</strong>：船上调查者回收无人机
    </td>
  </tr>
  <tr>
    <td width="50%" align="center">
      <img src="docs/showcase/dog-calamity.jpg" alt="雨后遛狗时随手拍下的私人相册照片" width="100%">
      <br><strong>狗灾</strong>：雨后遛狗的普通相册基线
    </td>
    <td width="50%" align="center">
      <img src="docs/showcase/shambhala.jpg" alt="山顶手持旧照片比对远方城市的调查画面" width="100%">
      <br><strong>香巴拉传说</strong>：用旧照片比对远方城市
    </td>
  </tr>
</table>

这些图片是虚构项目的 AI 辅助创作示例，不是新闻、历史档案或真实异常事件证据。展示图片 © isLiuyx1，仅供项目演示，不包含在本仓库的 MIT 软件许可证中。

## 工作流

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
→ scene_self_review
→ awaiting_repair_approval
→ repairing
→ final_self_review
→ complete / needs_user
```

真实性方向、故事、专业分镜、参考图、三张真实性校准和返修报告批准是不可绕过的内容门槛。批准文件使用 SHA-256 固化，后续编辑会使下游批准失效。传输恢复、参考图派生、批次切换和单图跳过不会额外要求用户确认。

## 仓库结构

```text
.
├── .codex-plugin/plugin.json
├── skills/oh-my-folkstoryphoto/
│   ├── SKILL.md
│   ├── agents/openai.yaml
│   ├── assets/
│   ├── references/
│   └── scripts/
├── hooks/
├── scripts/install_local.py
└── tests/
```

- `skills/oh-my-folkstoryphoto/`：可独立安装的核心技能。
- `references/`：工作流、真实拍摄、视觉语言、自审、验收和输出规范。
- `assets/`：真实性审查模板与只用于采集质感的匿名锚点。
- `authenticity.py`：校验设备方案、分镜物理、短提示词预算和结构化审查。
- `calibration_sheet.py`：生成三张真实性校准联系表。
- `review_state.py`：校验、迁移和推进结构化审查状态。
- `workspace_layout.py`：在 `01-进行中项目/` 初始化新项目，并安全整理旧工作区。
- `transport_guard.py`：固定提示词/参考图指纹，管理传输重试、探测和熔断。
- `package_release.py`：校验状态后裁切并打包最终图片。
- `hooks/`：可选 Codex 生命周期 hook。
- `tests/`：状态机、传输保护、hook 和图片打包回归测试。

## 安装

要求 Python 3.10+。图片预检和打包需要 Pillow。

先预览安装目标：

```bash
python3 scripts/install_local.py --mode skill --dry-run
python3 scripts/install_local.py --mode plugin --dry-run
```

只安装核心技能：

```bash
python3 scripts/install_local.py --mode skill
```

安装包含可选 hook 的完整插件：

```bash
python3 scripts/install_local.py --mode plugin
```

安装脚本会在替换旧版本前创建带时间戳的备份。插件安装后，在 Codex 中检查并信任 hook，然后开启一个新任务测试。

## 使用

在 Codex 中调用：

```text
$oh-my-folkstoryphoto 根据这个传说制作一套真实手机相册感的图文：<来源>
```

技能会先提出可选手机或旧 DV 真实性方向并等待确认；随后依次确认故事、专业分镜和参考素材。正式批量生成前只先做三张代表性校准图，获批后再逐镜生成、结构化审查、返修和打包。

## 开发与验证

```bash
python3 -m pip install -r requirements-dev.txt
python3 -m unittest discover -s tests -v
```

插件和技能还应使用当前 Codex 安装随附的官方校验器验证。当前回归套件覆盖 schema v1–v5、传输恢复、目录治理、字幕与文字策略、真实性门槛及发布打包；CI 还会运行 Python 语法检查和 JSON 解析检查。

## 发布与内容边界

- 不把网络推测写成历史事实、学术定论或原典唯一解释。
- 不把 AI 图片伪装成真实泄露档案、真实灾难证据或新闻影像。
- 仓库不包含商业 IP、影视角色或完整实践项目素材；仅保留经过筛选和压缩的展示图、匿名化规则与测试样例。
- 自动返修一次后仍不合格的内容必须进入 `needs_user`，不能静默降级为通过。

## 许可证

[MIT](LICENSE) © isliuyx

## 交流讨论

欢迎加入我们的 Telegram 群，交流 Codex 技能、手机伪纪录、民间故事改编和工作流实践：

[加入 Telegram 群聊](https://t.me/+DEqXSdSJrXs1MTc1)
