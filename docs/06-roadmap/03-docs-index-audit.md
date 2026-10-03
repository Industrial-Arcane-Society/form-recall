# 文档目录与索引审查记录

审查日期：2026-10-02；合并后复验日期：2026-10-04。此记录用于追踪研究文档体系本身的结构与可达性，不代表产品功能验收。前半部分保留 2026-10-02 的历史审计范围，文末附本次合入 main 后的当前检查结果。

## 审查范围与规则

- 活动入口为仓库根目录 `README.md` 与 `docs/README.md`；`docs/README.md` 是 `docs/` 唯一根级 Markdown 索引。
- `docs/` 子目录采用 `NN-kebab-case` 编号命名，文件采用 `NN-kebab-case.md` 命名；索引提供精选入口，完整阶段与历史资料由迁移 Todo 和目录结构导航。
- 旧文档、历史实验结果、Agent 运行日志及临时数据均归档在 `temp/`，历史记录不得被表述为当前实现的验收证据。
- 核对根 README 与 `docs/**/*.md` 的相对 Markdown 链接是否指向存在的文件或目录。

## 证据与修复

- `Get-ChildItem docs -File -Filter *.md` 只返回 `docs/README.md`，满足根索引唯一约束。
- `Get-ChildItem docs -Name -Directory` 返回 `00-research`、`01-architecture`、`02-methods`、`03-experiments`、`04-design`、`05-operations`、`06-roadmap`；均使用两位数字前缀和小写连字符命名。
- `docs/` 内活动文档使用连续的两位数字文件前缀与 kebab-case 名称。根 README 指向 `docs/README.md`，文档索引提供研究章程、系统审计、架构、方法、实验模板、界面规范、运行指南和迁移路线入口。
- 旧版 `docs/` 内容位于 `temp/legacy-documents/docs/`；历史实验报告位于 `temp/evidence-archive/`；运行日志目录为 `temp/agent-logs/`。`docs/README.md` 已说明归档内容不自动构成当前验收证据。
- 2026-10-02 历史审查时，`docs/06-roadmap/02-f1-acp-host.md` 仅存在于 `feat/rust-dsh-acp-host`，不在当时的 `main` 树中；因此当时按 main 可用文件集排除该卡。本次合并后，F1 卡已属于 main，并已加入 `docs/README.md`。
- 2026-10-02 历史复验：根 `README.md` 与 `docs/**/*.md` 共 12 个 Markdown 文件、26 个本地 Markdown 链接目标、缺失 0 个；按当时的 main 文件集计算，索引覆盖 9/9 篇子目录文档。该计数仅反映当时的小型文档快照，不代表当前 92 篇文档的覆盖情况。
- 本次合并后 `docs/README.md` 保留研究/运维主入口并列出精选阶段卡；未索引的具体阶段说明仍可由迁移 Todo 和 `06-roadmap/` 目录访问。
- 结构复验结果：`docs/` 根级 Markdown 只有 `README.md`；7 个目录名均符合 `NN-kebab-case`；子目录 Markdown 文件名均符合 `NN-kebab-case.md`；`temp/agent-logs/` 存在。

## 链接验证

对根 `README.md` 和 `docs/**/*.md` 执行本地 Markdown 链接扫描。扫描忽略外部 URL 和纯页面锚点，检查相对路径是否存在；历史扫描按当时的 main 文件集排除了 F1 卡，合并后扫描则不再排除。

可复核的结构检查命令：

```powershell
Get-ChildItem docs -File -Filter *.md
Get-ChildItem docs -Name -Directory
```

完整链接扫描可从仓库根目录复跑：

```powershell
@'
from pathlib import Path
from urllib.parse import urlsplit, unquote
import re

root = Path('.')
files = [root / 'README.md', *sorted((root / 'docs').rglob('*.md'))]
patterns = [re.compile(r'!?\[[^\]]*\]\(([^)]+)\)'),
            re.compile(r'^\s*\[[^\]]+\]:\s*(\S+)', re.M)]

def local_targets(source):
    text = source.read_text(encoding='utf-8')
    for pattern in patterns:
        for match in pattern.finditer(text):
            raw = match.group(1).strip()
            target = raw[1:raw.index('>')] if raw.startswith('<') and '>' in raw else raw.split()[0]
            parts = urlsplit(target.replace('\\', '/'))
            if not parts.path or parts.scheme or parts.path.startswith('//'):
                continue
            path = unquote(parts.path)
            resolved = root / path.lstrip('/') if path.startswith('/') else source.parent / path
            yield target, resolved.resolve()

missing = [(source, target) for source in files for target, path in local_targets(source) if not path.exists()]
index = root / 'docs' / 'README.md'
indexed = {path for target, path in local_targets(index) if target.lower().split('#', 1)[0].endswith('.md')}
children = {path.resolve() for path in (root / 'docs').rglob('*.md') if path != index}
unindexed = sorted(path.relative_to(root.resolve()).as_posix() for path in children - indexed)
count = sum(1 for source in files for _ in local_targets(source))
print(f'files={len(files)} local_links={count} missing={len(missing)} indexed_docs={len(children)-len(unindexed)}/{len(children)}')
for source, target in missing:
    print(f'MISSING {source}: {target}')
for path in unindexed:
    print(f'UNINDEXED {path}')
'@ | python -
```

## 2026-10-04 合并后复验

- 检查根 `README.md` 与 `docs/**/*.md` 共 93 个 Markdown 文件；本地链接扫描得到 117 个链接目标，缺失 0。
- `docs/` 当前有 92 个 Markdown 文件（包含索引本身），其余 91 篇子文档中有 22 篇由精选索引直接链接，69 篇未直接列入索引。未列出的阶段卡和历史诊断仍可从完整迁移 Todo 与 `06-roadmap/` 目录定位；索引没有宣称全量覆盖。
- `docs/README.md` 已加入 F1 ACP Host 阶段卡，以及浏览器预览与 Tauri 开发启动检查入口。F7 状态以 Todo 为准，真实 DSH/provider 视觉质量仍未验收。

## 未解决项

- 链接扫描只验证本地目标路径存在，不核验 Markdown 渲染器对扩展语法的兼容，也不校验每个页面内锚点是否有效。
- 扫描器按 Markdown inline/reference 链接形式解析；未检查裸 URL 和 HTML `href`。本次文件集未发现 HTML 链接、嵌套括号目标或 reference definition。
- 外部链接未进行联网状态或来源有效性核验。
- `temp/` 当前同时承载归档、日志、诊断、缓存与本地设置，后续仍需按各类数据的生成者、保留期限及忽略规则维护其子目录；本次没有搬动历史文件。
- 文档按“研究问题—方法—实验记录—实现/运维—路线图”组织，但研究结论与实验结果仍需在未来功能验收中持续补充原始输入、环境版本、失败样本、量测和复跑命令。
