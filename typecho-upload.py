#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Typecho 文章管理：上传 / 删除 / 列表 / 测试，全部走内置 XML-RPC，不装任何插件。

运行方式（无参数 → 交互菜单，双击也能用）：
    python typecho-upload.py

命令行方式：
    python typecho-upload.py --test                       只测登录
    python typecho-upload.py --list                       列出现有文章
    python typecho-upload.py --dir ./posts                预览，不写入
    python typecho-upload.py --dir ./posts --publish      真正发布
    python typecho-upload.py --dir ./posts --publish --skip-existing
    python typecho-upload.py --delete "文章标题关键词"       删文章（会先列出来确认）
    python typecho-upload.py --delete 123 --yes           按 id 直接删，不问
    python typecho-upload.py --setup                      设置账号密码并保存

站点/凭据优先级（左高右低）：
    命令行 --site/--user/--password > 环境变量 TC_SITE/TC_USER/TC_PASS > config.json > 脚本内默认值
注意：只要 config.json 里写了 site，改脚本里的 SITE 就不会生效；想只靠脚本控制，
      把 config.json 的 "site" 删掉或留空即可。
--site 例子：python typecho-upload.py --site http://192.168.1.10:8091 --test
config.json 查找顺序：脚本同目录 → 当前目录 → %APPDATA%\typecho-upload\
账号可填后台登录名或注册邮箱，两者都能登。
--dir 支持：文件夹、单个 .md、.zip 压缩包

Markdown 前置元数据（可选）：
    ---
    title: 文章标题
    date: 2026-10-19 12:30
    tags: 标签1, 标签2
    categories: 分类1
    slug: custom-slug
    ---
没写 title 时自动取正文第一个 "# 标题"。
正文里的本地图片 ![](./a.png) 会自动传到 /usr/uploads/ 并换成线上地址。

Markdown 标记不用手加 <!--markdown-->，后台开了 xmlrpcMarkdown 会自己加。

退出码：0 成功 / 2 参数或凭据问题 / 3 登录失败 / 4 输入路径问题 / 1 其他错误
"""
import os
import re
import sys
import ssl
import json
import getpass
import argparse
import socket
import traceback
import xmlrpc.client
import unicodedata
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
LOG_PATH = HERE / "typecho-upload.log"


def _cfg_candidates():
    """配置文件可能的位置，按优先级：脚本同目录 → 当前工作目录 → %APPDATA%。

    以前只认脚本同目录，脚本一被移动/复制，之前 --setup 存好的账号密码就"失效"了。
    """
    lst = [HERE / "config.json"]
    try:
        cwd = Path.cwd() / "config.json"
        if cwd != lst[0]:
            lst.append(cwd)
    except Exception:
        pass
    appdata = os.environ.get("APPDATA")
    if appdata:
        lst.append(Path(appdata) / "typecho-upload" / "config.json")
    return lst


CFG_PATH = _cfg_candidates()[0]

# ============ 配置：改这里，或用 config.json，或跑一次 --setup ============
SITE = ""                            # 站点地址，不要带结尾斜杠。注意：config.json 里有 site 时会覆盖这里
USER = ""                            # 后台登录名
PASSWORD = ""                        # 后台密码
# 交互菜单运行时置 True：允许登录失败后现场重输凭据
INTERACTIVE = False


# ---------------------------------------------------------------- 输出
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


class Tee(object):
    """同时写控制台和日志；控制台编码不支持时自动降级，不让字符把程序搞崩。"""

    def __init__(self, stream, path=None, fh=None):
        self.stream = stream
        self.path = path
        self.fh = fh
        self._owns = fh is None
        if self.fh is None and path is not None:
            self._open()

    def _write_header(self):
        try:
            self.fh.write("\n" + "=" * 60 + "\n")
            self.fh.write("运行时间: %s\n" % datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
            self.fh.flush()
        except Exception:
            pass

    def _open(self):
        try:
            self.fh = open(self.path, "a", encoding="utf-8", errors="replace")
            self._write_header()
        except Exception:
            self.fh = None

    def write(self, s):
        if self.stream is not None:
            try:
                self.stream.write(s)
                self.stream.flush()
            except UnicodeEncodeError:
                enc = getattr(self.stream, "encoding", None) or "utf-8"
                try:
                    self.stream.write(s.encode(enc, "replace").decode(enc, "replace"))
                    self.stream.flush()
                except Exception:
                    pass
            except Exception:
                pass
        if self.fh:
            try:
                self.fh.write(_ANSI_RE.sub("", s))
                self.fh.flush()
            except Exception:
                pass
        return len(s)

    def flush(self):
        for st in (self.stream, self.fh):
            try:
                if st:
                    st.flush()
            except Exception:
                pass

    def isatty(self):
        try:
            return bool(self.stream and self.stream.isatty())
        except Exception:
            return False

    @property
    def encoding(self):
        return getattr(self.stream, "encoding", "utf-8")


def install_log():
    try:
        if os.name == "nt":
            sys.stdout.reconfigure(errors="replace")
            sys.stderr.reconfigure(errors="replace")
    except Exception:
        pass
    try:
        fh = open(LOG_PATH, "a", encoding="utf-8", errors="replace")
        fh.write("\n" + "=" * 60 + "\n")
        fh.write("运行时间: %s\n" % datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        fh.flush()
    except Exception:
        fh = None
    try:
        out = Tee(sys.stdout, LOG_PATH, fh=fh)
        err = Tee(sys.stderr, LOG_PATH, fh=fh)
        sys.stdout = out
        sys.stderr = err
    except Exception:
        pass


def hrule(ch="-"):
    print(ch * 62)


def ask(prompt, default=""):
    """读一行，EOF/中断返回 default，不抛异常。"""
    try:
        v = input(prompt).strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return default
    return v or default


# ---------------------------------------------------------------- 配置
def load_config():
    """按候选路径找配置文件；空值视为没设置，避免空串把默认值覆盖成"未设"。"""
    global SITE, USER, PASSWORD, CFG_PATH
    for p in _cfg_candidates():
        if not p.exists():
            continue
        try:
            d = json.loads(p.read_text(encoding="utf-8-sig"))
        except Exception as e:
            print("[提示] %s 读不了，跳过：%s" % (p, e))
            continue
        CFG_PATH = p
        s = str(d.get("site") or "").strip()
        u = str(d.get("user") or "").strip()
        w = str(d.get("password") or "")
        if s:
            SITE = s.rstrip("/")
        if u:
            USER = u
        if w:
            PASSWORD = w
        if os.environ.get("TC_VERBOSE"):
            print("[配置] %s（账号 %s）" % (p, USER or "未设"))
        return True
    return False


def apply_env():
    """环境变量覆盖 config.json，方便脚本/计划任务调用。"""
    global USER, PASSWORD, SITE
    e = os.environ.get("TC_USER", "").strip()
    if e:
        USER = e
    e = os.environ.get("TC_PASS", "")
    if e:
        PASSWORD = e
    e = os.environ.get("TC_SITE", "").strip()
    if e:
        SITE = e


def save_config():
    """写回配置；脚本目录不可写时退回 %APPDATA%\\typecho-upload\\。"""
    global CFG_PATH
    data = json.dumps({"site": SITE, "user": USER, "password": PASSWORD},
                      ensure_ascii=False, indent=2) + "\n"
    targets = [CFG_PATH]
    appdata = os.environ.get("APPDATA")
    if appdata:
        alt = Path(appdata) / "typecho-upload" / "config.json"
        if alt != CFG_PATH:
            targets.append(alt)
    for p in targets:
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(data, encoding="utf-8")
            CFG_PATH = p
            print("[已保存] %s" % p)
            return True
        except Exception as e:
            last = e
    print("[保存失败] %s" % last)
    return False


def ask_credentials():
    """凭据不全时补问；有 TTY 用 getpass，管道下退回 input，绝不卡死。"""
    global USER, PASSWORD
    if not USER:
        print("提示：账号填 Typecho 后台登录名或注册邮箱都可以")
        for _ in range(3):
            USER = ask("Typecho 后台登录名/邮箱: ")
            if USER:
                break
            print("  不能为空，再输一次")
    if not PASSWORD:
        for _ in range(3):
            PASSWORD = read_password("Typecho 后台密码（输入不显示）: ")
            if PASSWORD:
                break
            print("  不能为空，再输一次")


# ---------------------------------------------------------------- 连接
def connect():
    url = SITE.rstrip("/") + "/action/xmlrpc"
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    transport = xmlrpc.client.SafeTransport(context=ctx) if url.startswith("https") \
        else xmlrpc.client.Transport()
    return xmlrpc.client.ServerProxy(url, transport=transport,
                                     allow_none=True, use_builtin_types=True)


def explain(e):
    """把底层异常翻译成能照着做的提示。"""
    if isinstance(e, xmlrpc.client.Fault):
        fs = str(e.faultString)
        low = fs.lower()
        if e.faultCode == 403 or "密码" in fs or "password" in low or "login" in low:
            return ("登录被拒绝（%s）\n"
                    "    1) 账号填后台登录名或注册邮箱都可以\n"
                    "    2) 密码是 Typecho 后台密码，不是群晖密码、不是 SSH 密码\n"
                    "    3) 后台「设置 → 基本设置」里 XML-RPC 接口要是「允许」\n"
                    "    4) 密码最近在后台改过的话，重新跑一次 --setup 覆盖 config.json" % fs)
        if e.faultCode in (401, 405):
            return "接口被拒（%s）\n    去后台「设置 → 基本设置」把 XML-RPC 接口设为「允许」" % fs
        if "not found" in low or "doesn't exist" in low or "no such" in low:
            return "文章不存在或接口不支持该操作（%s）" % fs
        return "服务器返回错误 %s：%s" % (e.faultCode, fs)

    if isinstance(e, xmlrpc.client.ProtocolError):
        if e.errcode == 404:
            return "HTTP 404：%s\n    检查站点地址是否漏了端口" % e.url
        return "HTTP %s %s：%s" % (e.errcode, e.errmsg, e.url)
    if isinstance(e, (socket.timeout, TimeoutError)):
        return "连接超时：访问不到 %s\n    检查网络或端口" % SITE
    if isinstance(e, ConnectionRefusedError):
        return "连接被拒绝：%s\n    检查端口、容器是否在跑" % SITE
    if isinstance(e, ssl.SSLError):
        return "SSL 握手失败：%s\n    站点是 http 就把 SITE 改成 http://" % e
    if isinstance(e, xmlrpc.client.ResponseError):
        return "返回内容不是合法 XML-RPC 响应\n    通常被登录页/防火墙拦了，或路径不对"
    return "%s: %s" % (type(e).__name__, e)


def login(proxy):
    try:
        blogs = proxy.metaWeblog.getUsersBlogs("", USER, PASSWORD)
        return True, ((blogs[0] or {}).get("blogName", "?") if blogs else "?")
    except Exception as e:
        return False, explain(e)


def read_password(prompt="Typecho 后台密码（输入不显示）: "):
    """有终端就隐藏输入，管道/重定向就退回普通输入，绝不卡死。"""
    try:
        tty = sys.stdin is not None and sys.stdin.isatty()
    except Exception:
        tty = False
    if tty:
        try:
            return getpass.getpass(prompt).strip()
        except Exception:
            pass
    return ask("Typecho 后台密码: ")


def ensure_login(proxy, retry=True):
    """登录；失败时当场让用户重输，成功后顺手存回 config.json。

    老版本只打印一段固定提示就返回失败，用户改了密码也照样失败。
    """
    global USER, PASSWORD
    # 只有交互菜单里才现场追问；命令行/脚本调用一律直接失败，避免管道下卡住
    can_ask = bool(INTERACTIVE)
    for attempt in range(3 if (retry and can_ask) else 1):
        ok, info = login(proxy)
        if ok:
            print("[成功] 登录通过：%s" % info)
            if attempt > 0:
                save_config()
            return True
        print("[失败] 登录没通过")
        print("       %s" % info)
        if not retry or attempt == 2 or not can_ask:
            break
        print("       重新输入账号密码（直接回车=退出）")
        u = ask("后台登录名或邮箱 [%s]: " % (USER or ""), USER)
        if u:
            USER = u
        p = read_password("后台密码: ")
        if not p:
            break
        PASSWORD = p
    return False


# ---------------------------------------------------------------- Markdown
FM_RE = re.compile(r"^\s*---\s*\n(.*?)\n---\s*\n", re.S)
IMG_RE = re.compile(r"!\[([^\]]*)\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")


def parse_md(path: Path):
    raw = path.read_text(encoding="utf-8-sig")
    meta = {}
    m = FM_RE.match(raw)
    if m:
        cur_list = None
        for line in m.group(1).splitlines():
            s = line.rstrip()
            if not s.strip():
                continue
            li = re.match(r"^\s*-\s+(.+)$", s)
            if li and cur_list is not None:
                cur_list.append(li.group(1).strip().strip('"').strip("'"))
                continue
            if ":" not in s:
                continue
            k, v = s.split(":", 1)
            k = k.strip().lower()
            v = v.strip().strip('"').strip("'")
            cur_list = None
            if k in ("tags", "tag", "keywords"):
                vals = [t.strip() for t in re.split(r"[,，]", v) if t.strip()]
                if vals:
                    meta["tags"] = vals
                else:
                    meta["tags"] = cur_list = []
            elif k in ("categories", "category", "cats"):
                vals = [t.strip() for t in re.split(r"[,，]", v) if t.strip()]
                if vals:
                    meta["categories"] = vals
                else:
                    meta["categories"] = cur_list = []
            else:
                meta[k] = v
        body = raw[m.end():]
    else:
        body = raw

    if not meta.get("title"):
        h = re.search(r"^\s*#\s+(.+)$", body, re.M)
        if h:
            meta["title"] = h.group(1).strip()
            body = body[:h.start()] + body[h.end():]
    if not meta.get("title"):
        meta["title"] = path.stem
    meta.setdefault("slug", path.stem if re.fullmatch(r"[A-Za-z0-9_\-]+", path.stem) else "")
    return meta, body.lstrip("\n")


def guess_mime(name: str):
    ext = name.lower().rsplit(".", 1)[-1]
    return {
        "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
        "gif": "image/gif", "webp": "image/webp", "svg": "image/svg+xml",
        "bmp": "image/bmp", "ico": "image/x-icon",
        "mp4": "video/mp4", "pdf": "application/pdf",
        "zip": "application/zip", "txt": "text/plain",
    }.get(ext, "application/octet-stream")


def upload_image(proxy, img_path: Path):
    data = img_path.read_bytes()
    name = img_path.name
    r = proxy.metaWeblog.newMediaObject(
        "1", USER, PASSWORD,
        {"name": name, "type": guess_mime(name), "bits": xmlrpc.client.Binary(data)})
    return (r or {}).get("url", "")


def rewrite_images(proxy, body: str, base_dir: Path, do_upload: bool):
    uploaded, failed = 0, []

    def repl(m):
        nonlocal uploaded
        alt, src = m.group(1), m.group(2)
        if re.match(r"^(https?:)?//", src) or src.startswith("data:"):
            return m.group(0)
        p = (base_dir / src.lstrip("/")).resolve()
        if not p.exists():
            failed.append(src)
            return m.group(0)
        if not do_upload:
            uploaded += 1
            return m.group(0)
        try:
            url = upload_image(proxy, p)
            uploaded += 1
            return "![%s](%s)" % (alt, url)
        except Exception as e:
            failed.append("%s (%s)" % (src, e))
            return m.group(0)

    return IMG_RE.sub(repl, body), uploaded, failed


# ---------------------------------------------------------------- 输入整理
def unpack_zip(zpath: Path):
    """解压到临时目录（兼容 Windows 压缩包 GBK 文件名）。"""
    import zipfile
    dest = HERE / "_md_tmp" / datetime.now().strftime("%Y%m%d-%H%M%S")
    try:
        dest.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(str(zpath)) as zf:
            for info in zf.infolist():
                if info.is_dir():
                    continue
                name = info.filename
                if not (info.flag_bits & 0x800):
                    try:
                        name = name.encode("cp437").decode("gbk")
                    except Exception:
                        pass
                name = name.replace("\\", "/")
                if ".." in name.split("/"):
                    continue
                out = dest / name
                out.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(info) as src, open(str(out), "wb") as fh:
                    fh.write(src.read())
        return dest
    except Exception as e:
        print("[错误] 解压失败：%s（%s）" % (zpath, e))
        return None


def resolve_input(raw: str):
    """把 --dir 的值整理成 (根目录, [md 文件])；失败返回 (None, 原因)。"""
    raw = (raw or "").strip().strip('"').strip("'").strip()
    if not raw:
        return None, "没有指定文章路径"
    raw = raw.rstrip("\\/") or raw
    if len(raw) == 2 and raw[1] == ":":
        raw += "\\"
    try:
        target = Path(raw).expanduser().resolve()
    except Exception as e:
        return None, "路径解析失败: %s（%s）" % (raw, e)

    if target.is_file():
        suffix = target.suffix.lower()
        if suffix == ".md":
            return target.parent, [target]
        if suffix == ".zip":
            d = unpack_zip(target)
            if d is None:
                return None, "压缩包解开失败"
            print("解压到  : %s" % d)
            target = d
        else:
            return None, "这是 %s 文件，只能处理 .md 或 .zip" % (suffix or "未知类型")

    if not target.is_dir():
        return None, "路径不存在: %s" % target
    files = sorted(p for p in target.rglob("*.md") if p.name.lower() != "readme.md")
    if not files:
        return None, "目录里没有 .md 文件: %s" % target
    return target, files


# ---------------------------------------------------------------- 文章操作
def fetch_posts(proxy, limit=200):
    posts = proxy.metaWeblog.getRecentPosts("1", USER, PASSWORD, limit)
    out = []
    for p in posts or []:
        d = p.get("dateCreated")
        try:
            if hasattr(d, "timetuple"):           # xmlrpc.client.DateTime
                d = datetime(*d.timetuple()[:6])
            elif isinstance(d, str):
                d = datetime.strptime(d, "%Y%m%dT%H:%M:%S")
            ds = d.strftime("%Y-%m-%d %H:%M")
        except Exception:
            ds = str(d or "")
        out.append({
            "id": p.get("postid") or p.get("postId") or "",
            "title": (p.get("title") or "").strip(),
            "status": p.get("post_status") or "",
            "date": ds,
            "link": p.get("link") or "",
        })
    return out


def do_list(limit=200):
    proxy = connect()
    try:
        posts = fetch_posts(proxy, limit)
    except Exception as e:
        if not ensure_login(proxy):
            return 3
        print("[错误] 拉取文章失败：%s" % explain(e))
        return 1
    if not posts:
        print("（没有读到文章）")
        return 0
    print("共 %d 篇：" % len(posts))
    hrule()
    print("%-6s %-10s %-17s %s" % ("ID", "状态", "日期", "标题"))
    for p in posts:
        print("%-6s %-10s %-17s %s" % (p["id"], p["status"], p["date"], p["title"]))
    return 0


# Typecho 1.3 的注册表（var/Widget/XmlRpc.php）：
#   metaWeblog.deletePost / blogger.deletePost -> bloggerDeletePost(blogId, postId, user, pass, publish)
# 所以首参是 blogId，固定 "1"；后面两种 4 参写法只做兜底，正常不会走到。
def _delete_variants(pid):
    pid = str(pid)
    return [
        ("metaWeblog.deletePost", ("1", pid, USER, PASSWORD, True)),
        ("blogger.deletePost", ("1", pid, USER, PASSWORD, True)),
        ("metaWeblog.deletePost", (pid, USER, PASSWORD, True)),
        ("blogger.deletePost", (pid, USER, PASSWORD, True)),
    ]


def delete_one(proxy, pid):
    last = None
    for name, args in _delete_variants(pid):
        obj_name, meth = name.split(".")
        try:
            getattr(getattr(proxy, obj_name), meth)(*args)
            return True, name
        except Exception as e:
            last = e
            s = str(e).lower()
            # 只有"方法不存在/签名不对"才换下一种签名；别的错误直接报出来
            if isinstance(e, xmlrpc.client.Fault) and (
                    "method" in s or "signature" in s or "参数" in s or "param" in s
                    or "不存在" in s or "not found" in s):
                continue
            return False, explain(e)
    return False, explain(last) if last else "未知错误"


def do_delete(keyword, limit=200, assume_yes=False):
    keyword = (keyword or "").strip()
    if not keyword:
        print("[错误] 要指定要删的文章：--delete \"标题关键词\" 或 --delete 文章ID")
        return 2

    proxy = connect()
    try:
        posts = fetch_posts(proxy, limit)
    except Exception as e:
        if not ensure_login(proxy):
            return 3
        print("[错误] 拉取文章失败：%s" % explain(e))
        return 1

    key_num = keyword.isdigit()
    hits = []
    for p in posts:
        if key_num and str(p["id"]) == keyword:
            hits.append(p)
        elif keyword.lower() in p["title"].lower():
            hits.append(p)

    if not hits:
        print("[没找到] 最近 %d 篇里没有匹配「%s」的文章" % (len(posts), keyword))
        print("         先跑 --list 看一眼准确标题")
        return 1

    print("匹配到 %d 篇：" % len(hits))
    hrule()
    for p in hits:
        print("  ID %-6s [%s] %s  %s" % (p["id"], p["status"], p["date"], p["title"]))
    hrule()

    if not assume_yes:
        ans = ask("确认删除上面这些？删除后进回收站/草稿不可逆，输入 Y 继续: ")
        if ans.upper() != "Y":
            print("已取消，什么都没删。")
            return 0

    ok_n = fail_n = 0
    for p in hits:
        success, info = delete_one(proxy, p["id"])
        if success:
            print("[已删] ID %s  %s" % (p["id"], p["title"]))
            ok_n += 1
        else:
            print("[失败] ID %s  %s\n       %s" % (p["id"], p["title"], info))
            fail_n += 1

    hrule()
    print("删除完成：成功 %d，失败 %d" % (ok_n, fail_n))
    return 0 if fail_n == 0 else 1


def do_publish(args):
    raw = args.dir or os.environ.get("TC_DIR") or ""
    if not (raw or "").strip().strip('"').strip("'"):
        print("[错误] 没有指定文章路径（--dir）")
        return 2

    root, files = resolve_input(raw)
    if root is None:
        print("[错误] %s" % files)
        print("       支持：文件夹、单个 .md、.zip")
        return 4

    ask_credentials()
    if not USER or not PASSWORD:
        print("[错误] 用户名或密码是空的，什么都没做。")
        return 2

    print("站点    : %s" % (SITE.rstrip("/") + "/action/xmlrpc"))
    print("目录    : %s" % root)
    print("文件数  : %d" % len(files))
    print("模式    : %s" % ("写入发布" if args.publish else "预览（不写入）"))
    hrule()

    proxy = connect()
    if not ensure_login(proxy):
        return 3
    hrule()

    skip = set()
    if args.skip_existing:
        try:
            skip = {p["title"] for p in fetch_posts(proxy, 200)}
        except Exception:
            skip = set()

    ok_n = skip_n = fail_n = 0
    for f in files:
        try:
            meta, body = parse_md(f)
        except Exception as e:
            print("[错误] 读不了 %s：%s" % (f.name, e))
            fail_n += 1
            continue

        title = meta["title"].strip()
        if title in skip:
            print("[跳过] %s（标题已存在）" % title)
            skip_n += 1
            continue

        body, up, failed = rewrite_images(
            proxy, body, f.parent, args.publish and not args.no_upload)

        # 注意：这台 Typecho 1.3.0 的 XML-RPC 只要带 dateCreated 字段就返回
        # HTTP 500（服务端可能已建文章但响应报错），所以不传创建时间，
        # 文章一律用服务器当前时间入库。
        post = {
            "title": title,
            "description": body,
            "mt_keywords": ",".join(meta.get("tags", [])),
            "categories": meta.get("categories", []),
            "post_status": args.status,
        }
        if meta.get("slug"):
            post["wp_slug"] = meta["slug"]

        line = "%-38s 图片%d" % (title[:36], up)
        if failed:
            line += "  失败:%s" % "; ".join(failed[:2])

        if args.publish:
            try:
                # 第 1 个参数是 blogId（固定 1），不是文章 ID；新建与否只看 post 里有没有 postId
                pid = proxy.metaWeblog.newPost(
                    "1", USER, PASSWORD, post, args.status == "publish")
                print("[OK] %s  id=%s" % (line, pid))
                ok_n += 1
            except Exception as e:
                print("[错误] %s\n       %s" % (line, explain(e)))
                fail_n += 1
        else:
            print("[预览] %s" % line)
            ok_n += 1

    hrule()
    print("完成：成功 %d，跳过 %d，失败 %d，共 %d" % (ok_n, skip_n, fail_n, len(files)))
    if not args.publish:
        print("这是预览模式，没有写入任何内容。加 --publish 才真正发。")
    return 0 if fail_n == 0 else 1


def do_test(retry=True):
    print("站点    : %s" % (SITE.rstrip("/") + "/action/xmlrpc"))
    print("用户名  : %s" % (USER or "(空)"))
    hrule()
    if not ensure_login(connect(), retry=retry):
        return 3
    print("       账号密码没问题，可以正常收发文章。")
    return 0


def do_setup():
    global SITE, USER, PASSWORD
    print("=" * 62)
    print("  Typecho 账号设置（只需一次，存进 config.json）")
    print("=" * 62)
    s = ask("站点地址 [%s]: " % SITE, SITE)
    if s:
        SITE = s.rstrip("/")
    u = ask("后台登录名或邮箱 [%s]: " % (USER or ""), USER)
    if u:
        USER = u
    p = read_password("后台密码（输入不显示）: ")
    if p:
        PASSWORD = p

    if not USER or not PASSWORD:
        print("[错误] 用户名或密码是空的，什么都没保存。")
        return 2
    hrule()
    code = do_test(retry=False)
    if code == 0:
        save_config()
        print("\n设置完成。之后一条命令即可：")
        print('  python typecho-upload.py --dir "D:\\posts" --publish --skip-existing')
    else:
        print("\n登录没通过，本次没有覆盖旧配置。改对后重跑 --setup。")
    return code


# ---------------------------------------------------------------- 交互菜单

def _sw(s):
    """显示宽度：全角字符按 2 列算，用来对齐中文。"""
    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in s)


def _color_on():
    """只在真终端里上色：管道/重定向、NO_COLOR、不支持 VT 的老控制台一律关。"""
    if os.environ.get("NO_COLOR"):
        return False
    try:
        if not sys.stdout.isatty():
            return False
    except Exception:
        return False
    if os.name == "nt":
        try:
            import ctypes
            k = ctypes.windll.kernel32
            h = k.GetStdHandle(-11)
            mode = ctypes.c_uint32()
            if not k.GetConsoleMode(h, ctypes.byref(mode)):
                return False
            if not (mode.value & 0x0004):        # ENABLE_VIRTUAL_TERMINAL_PROCESSING
                k.SetConsoleMode(h, mode.value | 0x0004)
            return True
        except Exception:
            return False
    return True


def _c(text, code, on=True):
    return ("\033[%sm%s\033[0m" % (code, text)) if on else text


def _hr(n):
    """分隔线：UTF-8 终端用细线，GBK 控制台退回 ASCII，保证宽度一致不乱码。"""
    enc = (getattr(sys.stdout, "encoding", "") or "").lower()
    return ("\u2500" if "utf" in enc else "-") * n


def _marker():
    """选中标记：UTF-8 终端用竖线 │，GBK 控制台退回 |。"""
    enc = (getattr(sys.stdout, "encoding", "") or "").lower()
    return "\u2502" if "utf" in enc else "|"


# 品牌蓝 #73AAFF → 紫 #C084FC，用来做字标和分隔线的渐变
_GRAD_A = (0x73, 0xAA, 0xFF)
_GRAD_B = (0xC0, 0x84, 0xFC)


def _rgb(c1, c2, t):
    return tuple(int(c1[i] + (c2[i] - c1[i]) * t) for i in range(3))


def _grad(s, on, bold=False):
    """逐字符渐变；终端不支持 24 位色时退回单色，设置 TC_NO_TRUECOLOR 可强制关闭。"""
    if not on:
        return s
    if os.environ.get("TC_NO_TRUECOLOR"):
        return _c(s, "1;36" if bold else "90", on)
    n = max(1, len(s) - 1)
    out = []
    for i, ch in enumerate(s):
        r, g, b = _rgb(_GRAD_A, _GRAD_B, i / n)
        out.append("\033[%s38;2;%d;%d;%dm%s" % ("1;" if bold else "", r, g, b, ch))
    return "".join(out) + "\033[0m"


MENU_ROWS = [
    ("01", "发布文章", "publish"),
    ("02", "预览草稿", "preview"),
    ("03", "文章列表", "list"),
    ("04", "删除文章", "delete"),
]
MENU_QUIT = ("00", "退出", "quit")


def _truecolor():
    return not os.environ.get("TC_NO_TRUECOLOR")


def render_menu(sel=None):
    """光标式菜单：选中行左侧一条竖线 + 品牌色文字，不铺色块。"""
    on = _color_on()
    rows = MENU_ROWS + [MENU_QUIT]
    mark = _marker()

    # 行宽按实际内容算，不留多余空白
    W = 0
    for num, label, _k in rows:
        W = max(W, 2 + _sw(num) + 1 + _sw(label))

    def d(t):
        return _c(t, "90", on)

    def n(t):
        return _c(t, "36", on)

    lines = [
        "  " + _grad("TYPECHO管理", on, bold=True),
        "  " + _grad(_hr(W), on),
    ]
    for i, (num, label, _key) in enumerate(rows):
        if i == sel:
            body = "%s %s %s" % (mark, num, label)
            if on and _truecolor():
                line = "\033[1;38;2;115;170;255m" + body + "\033[0m"
            elif on:
                line = _c(body, "1;36", on)
            else:
                line = body
            lines.append(line)
        elif num == "00":
            lines.append("  " + d(num) + " " + d(label))
        else:
            lines.append("  " + n(num) + " " + label)
    return "\n".join(lines)


def _read_key():
    """读一个按键：up/down/enter/esc/quit，其余原样返回；非终端返回空串。"""
    try:
        if not (sys.stdin and sys.stdin.isatty()):
            return ""
    except Exception:
        return ""

    if os.name == "nt":
        try:
            import msvcrt
        except Exception:
            return ""
        ch = msvcrt.getwch()
        if ch in ("\x00", "\xe0"):
            return {"H": "up", "P": "down", "K": "left", "M": "right"}.get(
                msvcrt.getwch(), "")
        if ch == "\r":
            return "enter"
        if ch == "\x1b":
            return "esc"
        if ch == "\x03":
            return "quit"
        return ch

    try:
        import select
        import termios
        import tty
    except Exception:
        return ""
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    ch = ""
    try:
        tty.setraw(fd)
        ch = os.read(fd, 1).decode("latin-1", "ignore")
        if ch == "\x1b":
            # 单独按 Esc 时不能阻塞等待后续字节
            seq = ""
            for _ in range(2):
                r, _, _ = select.select([fd], [], [], 0.05)
                if not r:
                    break
                seq += os.read(fd, 1).decode("latin-1", "ignore")
            return {"[A": "up", "[B": "down", "[C": "right", "[D": "left"}.get(seq, "esc")
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
    if ch in ("\r", "\n"):
        return "enter"
    if ch == "\x03":
        return "quit"
    return ch


def _menu_select():
    """方向键菜单。非终端（管道/重定向）自动退回输入编号。返回动作 key 或 None。"""
    rows = MENU_ROWS + [MENU_QUIT]

    if not (sys.stdin and sys.stdin.isatty()):
        while True:
            print()
            print(render_menu())
            s = ask("\n  请选择 > ", "00").strip().strip('"').strip("'")
            if s.lower() in ("q", "quit", "exit"):
                return None
            if s.isdigit():
                s = s.zfill(2)
                for num, _label, key in rows:
                    if num == s:
                        return key
            if os.path.exists(s):
                return ("path", s)
            print(_c("  没有这个选项", "33", _color_on()))

    sel = 0
    done = None
    result = None
    frame = render_menu(sel)
    up = frame.count("\n") + 1
    sys.stdout.write("\033[?25l" + frame + "\n")      # 隐藏光标，减少重绘闪烁
    sys.stdout.flush()

    try:
        while True:
            k = _read_key()

            if k in ("up", "k"):
                sel = (sel - 1) % len(rows)
            elif k in ("down", "j"):
                sel = (sel + 1) % len(rows)
            elif k in ("enter", " "):
                done = sel
                result = rows[sel][2]
                break
            elif k in ("esc", "quit", "q"):
                break
            elif k and k.isdigit():
                if k == "0":
                    break
                hit = [i for i, r in enumerate(rows) if r[0] == "0" + k]
                if hit:
                    done = hit[0]
                    result = rows[done][2]
                    break
            else:
                continue

            sys.stdout.write("\033[%dA\033[J" % up)   # 回到菜单顶部并清掉残影
            sys.stdout.flush()
            frame = render_menu(sel)
            sys.stdout.write(frame + "\n")
            sys.stdout.flush()
    finally:
        sys.stdout.write("\033[?25h")
        if done is not None:
            # 选中后收起整块菜单，只留一行「刚选了什么」
            num, label, _key = rows[done]
            sys.stdout.write("\033[%dA\033[J" % up)
            sys.stdout.write("  " + _c(_marker() + " " + num, "36", _color_on())
                             + "   " + label + "\n")
        sys.stdout.flush()
    return result


def menu():
    global INTERACTIVE
    INTERACTIVE = True
    while True:
        choice = _menu_select()
        if not choice:
            return 0

        if isinstance(choice, tuple):          # 拖进来的路径，直接发布
            args = argparse.Namespace(dir=choice[1], publish=True, status="publish",
                                      no_upload=False, skip_existing=True)
            ask_credentials()
            do_publish(args)
        elif choice in ("publish", "preview"):
            p = ask("  路径（文件夹 / .md / .zip，回车=当前目录）: ", ".")
            args = argparse.Namespace(dir=p.strip().strip('"'), publish=(choice == "publish"),
                                      status="publish", no_upload=False, skip_existing=True)
            ask_credentials()
            do_publish(args)
        elif choice == "list":
            ask_credentials()
            do_list()
        elif choice == "delete":
            ask_credentials()
            kw = ask("  要删的标题关键词或文章 ID: ")
            do_delete(kw)

        print()
        ask("  按回车继续…")


# ---------------------------------------------------------------- 入口
def main(argv=None):
    load_config()
    apply_env()

    ap = argparse.ArgumentParser(
        description="Typecho 上传/删除/列表/测试，单文件搞定")
    ap.add_argument("--dir", help="Markdown 目录、单个 .md，或 .zip")
    ap.add_argument("--publish", action="store_true", help="真正创建文章（默认只预览）")
    ap.add_argument("--status", default="publish",
                    choices=["publish", "draft", "private"], help="发布状态")
    ap.add_argument("--no-upload", action="store_true", help="不传本地图片")
    ap.add_argument("--skip-existing", action="store_true", help="标题已存在则跳过")
    ap.add_argument("--list", action="store_true", help="列出文章")
    ap.add_argument("--delete", metavar="KEY", help="删除文章：标题关键词或文章 ID")
    ap.add_argument("--yes", action="store_true", help="删除时不再询问")
    ap.add_argument("--limit", type=int, default=200, help="拉取文章数量上限")
    ap.add_argument("--test", action="store_true", help="只测试登录")
    ap.add_argument("--setup", action="store_true", help="设置账号密码并保存")
    ap.add_argument("--site", help="站点地址（覆盖配置文件，如 http://192.168.1.10:8091）")
    ap.add_argument("--user", help="后台用户名（覆盖配置文件）")
    ap.add_argument("--password", help="后台密码（覆盖配置文件）")
    args = ap.parse_args(argv)

    if args.site and args.site.strip():
        globals()["SITE"] = args.site.strip().rstrip("/")
    if args.user and args.user.strip():
        globals()["USER"] = args.user.strip()
    if args.password:
        globals()["PASSWORD"] = args.password

    if args.setup:
        return do_setup()
    if args.test:
        ask_credentials()
        if not USER or not PASSWORD:
            print("[错误] 用户名或密码是空的，什么都没做。")
            return 2
        return do_test()
    if args.list:
        ask_credentials()
        if not USER or not PASSWORD:
            return 2
        return do_list(args.limit)
    if args.delete is not None:
        ask_credentials()
        if not USER or not PASSWORD:
            return 2
        return do_delete(args.delete, args.limit, args.yes)
    if args.dir:
        return do_publish(args)

    # 没有任何参数 → 交互菜单
    return menu()


if __name__ == "__main__":
    install_log()
    code = 0
    try:
        code = main()
    except KeyboardInterrupt:
        print("\n已取消")
        code = 130
    except Exception:
        print("\n出错了，下面是详细信息：")
        traceback.print_exc()
        code = 1
    print("\n日志: %s" % LOG_PATH)
    try:
        sys.stdout.flush()
    except Exception:
        pass
    sys.exit(code)
