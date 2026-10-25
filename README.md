# typecho-upload

> Typecho 文章管理：上传 / 删除 / 列表 / 测试，全部走内置 XML-RPC，**不装任何插件**，单文件搞定。

**作者：大鱼（数据渔港）** ｜ **联系邮箱：见 GitHub 主页**

---

## ✨ 功能特性

- **批量发布**：整个文件夹、单个 `.md`、甚至 `.zip` 压缩包一次导入
- **自动传图**：Markdown 里的本地图片 `![](./a.png)` 自动上传到 `/usr/uploads/` 并替换为线上地址
- **前置元数据**：支持 title / date / tags / categories / slug，没写 title 自动取正文第一个 `# 标题`
- **防重复**：`--skip-existing` 标题已存在自动跳过
- **预览模式**：默认只预览不写入，加 `--publish` 才真正发布
- **文章管理**：列表、按标题关键词或 ID 删除（删除前先列出确认）
- **双击可用**：无参数运行进入方向键交互菜单，中文对齐、支持 GBK 老控制台
- **凭据安全**：密码隐藏输入，配置文件支持多级查找，环境变量覆盖优先

## 📦 安装

仅需 Python 3.8+（无第三方依赖）：

```bash
# 下载单文件即可使用
curl -O https://raw.githubusercontent.com/datafh/typecho-upload/main/typecho-upload.py
```

## 🚀 快速开始

```bash
# 1. 首次使用：配置站点、账号、密码（存进 config.json，只需一次）
python typecho-upload.py --setup

# 2. 预览要发布的文章（不写入）
python typecho-upload.py --dir ./posts

# 3. 确认无误后真正发布
python typecho-upload.py --dir ./posts --publish --skip-existing
```

## 📖 命令一览

```bash
python typecho-upload.py                # 无参数 → 交互菜单（双击也能用）
python typecho-upload.py --test         # 只测登录
python typecho-upload.py --list         # 列出现有文章
python typecho-upload.py --dir ./posts  # 预览，不写入
python typecho-upload.py --dir ./posts --publish           # 真正发布
python typecho-upload.py --dir ./posts --publish --skip-existing  # 跳过已存在标题
python typecho-upload.py --delete "标题关键词"   # 删文章（会先列出来确认）
python typecho-upload.py --delete 123 --yes     # 按 id 直接删，不问
python typecho-upload.py --setup       # 设置账号密码并保存
```

`--dir` 支持：文件夹、单个 `.md`、`.zip` 压缩包（兼容 Windows GBK 文件名压缩包）。

## 📝 Markdown 前置元数据（可选）

```markdown
---
title: 文章标题
date: 2026-10-19 12:30
tags: 标签1, 标签2
categories: 分类1
slug: custom-slug
---
正文……
```

- 没写 `title` 时自动取正文第一个 `# 标题`，再没有就用文件名
- 正文里的本地图片会自动上传到站点并替换成线上地址
- Markdown 标记不用手加 `<!--markdown-->`，后台开了 xmlrpcMarkdown 会自己加

## ⚙️ 配置说明

**config.json 查找顺序**：脚本同目录 → 当前工作目录 → `%APPDATA%\typecho-upload\`

首次运行可直接复制模板：

```bash
cp config.example.json config.json
# 然后编辑 config.json 填入你的站点地址、账号、密码
```

**站点/凭据优先级**（左高右低）：

```
命令行 --site/--user/--password > 环境变量 TC_SITE/TC_USER/TC_PASS > config.json > 脚本内默认值
```

环境变量方式（适合脚本 / 计划任务调用，避免明文落盘）：

```bash
set TC_SITE=http://你的站点:8091
set TC_USER=你的账号
set TC_PASS=你的密码
python typecho-upload.py --dir ./posts --publish
```

> 注意：只要 config.json 里写了 site，脚本内的默认值就不会生效；想只靠脚本控制，
> 把 config.json 的 "site" 删掉或留空即可。

**服务器要求**：Typecho 后台「设置 → 基本设置 → XML-RPC 接口」需设为「允许」。

## 🔢 退出码

| 码 | 含义 |
|---|---|
| 0 | 成功 |
| 1 | 其他错误 |
| 2 | 参数或凭据问题 |
| 3 | 登录失败 |
| 4 | 输入路径问题 |

## ⚠️ 已知适配说明

- 针对 Typecho 1.3 适配：XML-RPC 带 `dateCreated` 字段会触发 HTTP 500，故文章一律用服务器当前时间入库
- 删除接口按 Typecho 1.3 注册表实现，并兼容多种历史签名自动回退

## 📄 许可

仅供个人使用，请勿用于违反目标站点使用规范的场景。使用产生的一切后果由使用者自行承担。

Copyright © 大鱼（数据渔港）
