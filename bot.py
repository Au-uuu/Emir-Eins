"""
QQ 官方机器人 —— 群 @ / 群全量 / 单聊 消息响应骨架

基于官方 SDK qq-botpy。功能：
  - 群里被 @ 时响应指令
  - 群主开启「接收所有消息」后，按关键词响应（带限流防刷屏）
  - 单聊消息响应
  - 消息去重，避免同一事件重复推送导致重复回复
  - 图片库：上传图片（SHA-256 去重 + 关键词关联）、随机取图、查询统计

启动：python bot.py
"""

import asyncio
import logging
import os
import re
import tempfile
import time
from collections import OrderedDict

import botpy
from botpy.message import C2CMessage, GroupMessage
from dotenv import load_dotenv

import watchdog
from image_store import (
    ImageStore,
    first_frame_png,
    make_card_sheet,
    make_contact_sheet,
)
from raw_events import get_raw, install as install_raw_events
from uploader import MediaUploader, UploadError

load_dotenv()

# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------
APP_ID = os.getenv("QQ_BOT_APPID", "").strip()
APP_SECRET = os.getenv("QQ_BOT_SECRET", "").strip()

# 群「全量消息」模式下触发的关键词；留空表示不开启全量响应
KEYWORD = os.getenv("QQ_BOT_KEYWORD", "").strip()

# 同一群内的回复限流（全量模式防刷屏），单位：秒
GROUP_REPLY_COOLDOWN = float(os.getenv("QQ_BOT_COOLDOWN", "10"))

# 取图指令的每群限流，防止有人连点把接口打爆
IMAGE_COOLDOWN = float(os.getenv("QQ_BOT_IMAGE_COOLDOWN", "3"))

# 看门狗：超过这么久没收到任何网关消息，就认为长连接已死并重启进程（秒）
WATCHDOG_IDLE_TIMEOUT = float(os.getenv("QQ_BOT_IDLE_TIMEOUT", "900"))

# 管理员口令：只在私聊里用「管理员 <口令>」自助登记管理员（openid 入库）。
# 留空则关闭管理员登记功能。口令属于敏感信息，不要提交仓库、不要外传。
ADMIN_PASSWORD = os.getenv("QQ_BOT_ADMIN_PASSWORD", "").strip()

if not APP_ID or not APP_SECRET:
    raise SystemExit(
        "缺少凭据：请在项目目录下创建 .env 文件并填写 "
        "QQ_BOT_APPID 与 QQ_BOT_SECRET（可参考 .env.example）"
    )

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("qqbot")

# botpy 在 import 阶段就已经调用过 logging.basicConfig()，root 上已有 handler，
# 导致上面这次 basicConfig 变成空操作、root 级别停留在默认的 WARNING。
# 结果是本文件里所有 log.info(...) 都被静默丢弃（日志里只剩 botpy 自己的输出）。
# 显式把本项目的 logger 级别设成 INFO，和 botpy 的日志一起进 logs/bot.log。
log.setLevel(logging.INFO)


def _setup_file_log() -> None:
    """把日志同时写入 logs/bot.log，方便后台运行时排查。"""
    log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
    os.makedirs(log_dir, exist_ok=True)
    handler = logging.FileHandler(
        os.path.join(log_dir, "bot.log"), encoding="utf-8"
    )
    handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    )
    logging.getLogger().addHandler(handler)


_setup_file_log()


# ---------------------------------------------------------------------------
# 工具：消息去重 + 群级限流
# ---------------------------------------------------------------------------
class DedupCache:
    """
    平台可能对同一事件多次推送；被动回复窗口为 5 分钟、每条最多回 5 次。
    相同 msg_id 只处理一次，避免重复回复。
    """

    def __init__(self, ttl: float = 300.0, maxsize: int = 4096):
        self.ttl = ttl
        self.maxsize = maxsize
        self._store: OrderedDict[str, float] = OrderedDict()

    def seen(self, key: str) -> bool:
        now = time.monotonic()
        # 清理过期项
        while self._store:
            _, ts = next(iter(self._store.items()))
            if now - ts > self.ttl:
                self._store.popitem(last=False)
            else:
                break

        if key in self._store:
            return True

        self._store[key] = now
        if len(self._store) > self.maxsize:
            self._store.popitem(last=False)
        return False


class Cooldown:
    """按 key 限流，防止在 50 人群里被全量消息刷屏。"""

    def __init__(self, seconds: float):
        self.seconds = seconds
        self._last: dict[str, float] = {}

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        last = self._last.get(key, 0.0)
        if now - last < self.seconds:
            return False
        self._last[key] = now
        return True


dedup = DedupCache()
cooldown = Cooldown(GROUP_REPLY_COOLDOWN)

# 图片库（SQLite + 本地文件）
store = ImageStore()


# ---------------------------------------------------------------------------
# 业务逻辑：指令处理
# ---------------------------------------------------------------------------
HELP_TEXT = (
    "我是群助手，可以收藏图片、随机取图。\n"
    "\n"
    "【收藏】/添加 关键词…　引用一张图片后回复；同消息附图须带「/」\n"
    "【批量】/批量添加 关键词　一次最多 5 张，把消息里的图片入库\n"
    "【删除】/删除 图库名 [私有/公开/全部]　先「引用一张图片」再回复；默认全部\n"
    "【取图】/来只 [关键词] [私有/公开/全部]　随机发一张图；默认全部\n"
    "【查询】/图库　图库列表　/图库 关键词 [页码]　看预览图与关联词（每页 8 张）\n"
    "　　　　/图库 统计　统计信息\n"
    "【关联】/关联 主词 别名…　别名等价主词，如 /关联 猫 猫咪\n"
    "　　　　/查找关联 [词]　/取消关联 别名\n"
    "【私有】/私有 图库名　设为当前群私有　/公开 图库名 取消\n"
    "【名片】/添加名片 备注　引用图片后添加自己的游戏名片\n"
    "　　　　/游戏名片　把自己的名片拼成一张图发送\n"
    "　　　　/删除名片 备注|编号　或引用图片后 /删除名片\n"
    "\n"
    "快捷：只有「添加 / 删除 / 来只」可省略斜杠（添加、删除需引用图片），\n"
    "　　　其余指令必须带「/」。图库名不能是纯数字。\n"
    "用法：长按图片选「引用」，再回复「/添加 关键词」。"
)

# 管理员私聊里的完整帮助（包含 /查询标识、/删除图库 等管理指令）
ADMIN_HELP_TEXT = (
    "【管理员指令】\n"
    "　/删除图库　列出所有图库层（图库→群openid/公开）\n"
    "　/删除图库 图库名 <群openid|公开>　删除该图库的这一层\n"
    "　/私有 图库名 群openid　（私聊）对指定群设私有\n"
    "　/公开 图库名 群openid　（私聊）对指定群取消私有\n"
    "　/查询标识　查看自己的 openid 与当前群 openid\n"
    "　/取消关联 关键词　解关联；删除仅剩的最后一个词\n"
    "　/管理员 <口令>　（私聊）登记管理员\n"
    "\n" + HELP_TEXT
)

# 帮助面板的按钮（点击后由机器人直接回复，不依赖客户端指令面板）
HELP_BUTTONS = [
    [("功能与指令", "/help", 1), ("随机来张图", "/来只", 4)],
    [("图库列表", "/图库", 1)],
]

# /echo 可复读的最大字数，防止超长消息触发平台长度限制
ECHO_MAX_LEN = 200

# 匹配复读指令（大小写不敏感），捕获后面的内容。
# 英文 /echo 与中文 /复读 都认——管理端「指令面板」里配的中文名点一下会插入
# /复读，得让它和 /echo 等价，否则用户从面板点进来机器人会装死。
ECHO_RE = re.compile(r"^/?echo\b\s*(.*)$", re.IGNORECASE | re.DOTALL)
ECHO_CN_RE = re.compile(r"^/?复读\s*(.*)$", re.DOTALL)

# 图片指令：斜杠可有可无，`添加` / `/添加`、`来只` / `/来只` 都认
ADD_RE = re.compile(r"^/?添加(?:图片|图)?\s*(.*)$", re.DOTALL)
# 批量添加：一次把消息（或引用消息）里的多张图片入库。必须带斜杠。
BATCH_ADD_RE = re.compile(r"^/批量添加(?:图片|图)?\s*(.*)$", re.DOTALL)
RANDOM_RE = re.compile(r"^/?来只(?:图)?\s*(.*)$", re.DOTALL)
GALLERY_RE = re.compile(r"^/图库\s*(.*)$", re.DOTALL)

# 删除图片指令（斜杠可省）：引用一张图后回复「/删除 图库名称」
DELETE_RE = re.compile(r"^/?删除\s*(.*)$", re.DOTALL)

# 群私有图库（必须带斜杠）：`/私有 图库名` 把图库设为当前群私有；`/公开 图库名` 取消
PRIVATE_RE = re.compile(r"^/私有\s*(.*)$", re.DOTALL)
PUBLIC_RE = re.compile(r"^/公开\s*(.*)$", re.DOTALL)

# 查询自己的 openid / 当前群 openid。故意用较长的指令名，避免被普通用户随手使用。
# 不进普通帮助，只在「管理员私聊帮助」里出现。
WHOAMI_RE = re.compile(r"^/查询标识\b\s*(.*)$", re.DOTALL)

# 管理员删除某图库层：`/删除图库 <图库名> <群openid|公开>`
DELETE_GALLERY_RE = re.compile(r"^/删除图库\s*(.*)$", re.DOTALL)

# 游戏名片（必须带斜杠）
CARD_ADD_RE = re.compile(r"^/添加名片\s*(.*)$", re.DOTALL)
CARD_RE = re.compile(r"^/游戏名片\s*(.*)$", re.DOTALL)
CARD_DEL_RE = re.compile(r"^/删除名片\s*(.*)$", re.DOTALL)

# 管理员登记指令（**只在私聊可用，且不对外展示**）：「管理员 <口令>」
ADMIN_RE = re.compile(r"^/?管理员\s*(.*)$", re.DOTALL)
ADMIN_EN_RE = re.compile(r"^/?admin\b\s*(.*)$", re.IGNORECASE | re.DOTALL)

# 关键词关联指令（必须带斜杠）。
# 注意「取消关联」「查找关联」都以「关联」结尾，但这两个正则都以 ^ 锚定开头，
# 不写 ^/关联 之外的前缀，所以不会误命中。
LINK_RE = re.compile(r"^/关联\s*(.*)$", re.DOTALL)
UNLINK_RE = re.compile(r"^/取消关联\s*(.*)$", re.DOTALL)
FIND_LINK_RE = re.compile(r"^/查找关联\s*(.*)$", re.DOTALL)

# 一次随机取图的候选数量上限（随机命中后只发一张）
GALLERY_LIST_LIMIT = 30

# /图库 列表每页最多多少个图库
GALLERY_PAGE_SIZE = 30

# /图库 <关键词> 每页回复多少张预览缩略图
GALLERY_IMG_PAGE = 8

# /批量添加 一次最多几张
BATCH_ADD_MAX = 5

# 取图/删除时第二个参数（层选择）
LAYER_WORDS = {"私有": "private", "公开": "public", "全部": "all"}

IMAGE_COOLDOWN_GUARD = Cooldown(IMAGE_COOLDOWN)


# 提及有两种写法：QQ 原始 payload 里群成员是「<@openid>」，客户端显示成「@昵称」。
# 引用/回复别人时 QQ 会在正文开头自动加一个这样的提及，必须先剥掉再匹配指令。
_MENTION = r"(?:<@[^>\s]+>|@[^\s@]+)"
_LEADING_MENTION_RE = re.compile(rf"^\s*(?:{_MENTION}\s*)+")
_MENTION_TOKEN_RE = re.compile(rf"^{_MENTION}$")


def normalize_incoming(raw: str) -> str:
    """
    取出真正用于匹配指令的正文：去掉开头的 @提及（`<@openid>` 或 `@昵称`）与空白。

    群全量模式下用户「@机器人 /来只」、或引用/回复别人消息时，正文开头可能
    带着提及；不剥掉的话所有指令都认不出来。
    """
    text = (raw or "").replace("\u2005", " ").replace("\u00a0", " ")
    return _LEADING_MENTION_RE.sub("", text).strip()


def split_keywords(raw: str) -> list[str]:
    """
    把「添加 deepseek 蓝色大肥鱼」里的关键词切开，空格/逗号/中文顿号都算分隔。

    提及不是关键词，直接丢掉，否则会把「@昵称」/「<@openid>」原样存成关键词。
    """
    parts = re.split(r"[\s,，、]+", (raw or "").strip())
    return [
        p
        for p in parts
        if p and not p.startswith("@") and not _MENTION_TOKEN_RE.match(p)
    ]


def author_openid(message) -> str:
    """
    取发送者的 openid。

    QQ 官方机器人拿不到 QQ 号，只能用 openid（单聊是 user_openid，
    群聊是 member_openid，两者都是「本机器人 + 本用户」范围内唯一）。
    """
    author = getattr(message, "author", None)
    return (
        getattr(author, "user_openid", None)
        or getattr(author, "member_openid", None)
        or ""
    )


_IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp")


def _looks_like_image(content_type, filename, url) -> bool:
    """
    判断是不是图片附件。

    QQ 的附件有时 content_type 为空或标错，所以 content_type 不是唯一依据，
    再看文件名/URL 的扩展名兜底。
    """
    ct = (content_type or "").lower()
    if ct.startswith("image/"):
        return True
    name = (filename or "").lower()
    if name.endswith(_IMAGE_EXTS):
        return True
    path = (url or "").lower().split("?")[0]
    return path.endswith(_IMAGE_EXTS)


def pick_image_attachment(attachments) -> object | None:
    """从消息附件中挑出图片（top-level，即本条消息自己带的图）。"""
    for att in attachments or []:
        if _looks_like_image(
            getattr(att, "content_type", ""),
            getattr(att, "filename", ""),
            getattr(att, "url", ""),
        ):
            return att
    return None


def _find_image_in_elements(elements, depth: int = 0) -> dict | None:
    """
    在 msg_elements 里递归找图片附件。

    用户「引用一条图片消息」时，平台把被引用图片放在 msg_elements 里，
    top-level 的 attachments 是空的。
    """
    if depth > 4 or not elements:
        return None
    for el in elements:
        if not isinstance(el, dict):
            continue
        for att in el.get("attachments") or []:
            if not isinstance(att, dict):
                continue
            if att.get("url") and _looks_like_image(
                att.get("content_type"), att.get("filename"), att.get("url")
            ):
                return att
        # 嵌套元素（聊天记录、引用里再引用）
        nested = _find_image_in_elements(el.get("msg_elements"), depth + 1)
        if nested:
            return nested
    return None


class AttachmentView:
    """把 dict 形式的附件包成带属性的对象，和 SDK 的附件对象保持一致的用法。"""

    def __init__(self, data: dict):
        self.url = data.get("url")
        self.filename = data.get("filename")
        self.content_type = data.get("content_type")
        self.width = data.get("width")
        self.height = data.get("height")
        self.size = data.get("size")


def resolve_image_attachment(message) -> tuple[object | None, str]:
    """
    找出这条消息要用来入库的图片。

    返回 (附件, 来源说明)：
      1. 本条消息自己带的图            -> "本条消息"
      2. 被引用消息里的图（msg_elements）-> "引用的图片消息"
    """
    own = pick_image_attachment(getattr(message, "attachments", None))
    if own is not None:
        return own, "本条消息"

    raw = get_raw(message)
    found = _find_image_in_elements(raw.get("msg_elements"))
    if found:
        return AttachmentView(found), "引用的图片消息"

    return None, ""


def _collect_images_from_elements(elements, out: list, depth: int = 0) -> None:
    """递归收集 msg_elements 里的所有图片附件 dict。"""
    if depth > 4 or not elements:
        return
    for el in elements:
        if not isinstance(el, dict):
            continue
        for att in el.get("attachments") or []:
            if not isinstance(att, dict):
                continue
            if att.get("url") and _looks_like_image(
                att.get("content_type"), att.get("filename"), att.get("url")
            ):
                out.append(AttachmentView(att))
        _collect_images_from_elements(el.get("msg_elements"), out, depth + 1)


def collect_image_attachments(message) -> list:
    """收集本条消息（或引用消息）里的所有图片附件，用于批量添加。"""
    out: list = []
    for att in getattr(message, "attachments", None) or []:
        if _looks_like_image(
            getattr(att, "content_type", ""),
            getattr(att, "filename", ""),
            getattr(att, "url", ""),
        ):
            out.append(att)
    if out:
        return out
    raw = get_raw(message)
    _collect_images_from_elements(raw.get("msg_elements"), out)
    return out


def uploader_of(api) -> MediaUploader:
    """每个 client 复用一个上传器（按需创建，避免全局状态）。"""
    up = getattr(api, "_qqbot_uploader", None)
    if up is None:
        up = MediaUploader(api)
        try:
            api._qqbot_uploader = up
        except Exception:  # noqa: BLE001
            pass
    return up


# 平台拒绝图片格式时返回的典型报错文案（850019 = 富媒体文件格式不支持）
_FORMAT_ERROR_MARKERS = ("850019", "格式不支持", "文件格式")


def _is_format_rejected(exc: Exception) -> bool:
    return any(m in str(exc) for m in _FORMAT_ERROR_MARKERS)


def _png_first_frame_temp(path: str) -> str:
    """把本地图片转成 PNG（动图只取首帧）写入临时文件，返回路径。"""
    with open(path, "rb") as fh:
        png = first_frame_png(fh.read())
    fd, tmp = tempfile.mkstemp(suffix=".png")
    with os.fdopen(fd, "wb") as fh:
        fh.write(png)
    return tmp


async def _upload_once(
    up: MediaUploader, scope: str, scene_id: str, path: str, name: str
) -> str:
    if scope == "group":
        return await up.upload_group_image(scene_id, path, name)
    return await up.upload_c2c_image(scene_id, path, name)


async def _upload_local_image(
    up: MediaUploader, scope: str, scene_id: str, path: str, name: str
) -> str:
    """
    上传本地图片，返回 file_info。

    平台对动图的支持不稳定：GIF 在部分版本会被以 850019 拒绝。这里先按原格式
    试一次——发得出去就保留动图；被拒则自动转成 PNG 首帧重试，保证发得出去。
    """
    try:
        return await _upload_once(up, scope, scene_id, path, name)
    except UploadError as exc:
        if not _is_format_rejected(exc):
            raise
        log.warning("平台拒绝该格式，降级为 PNG 首帧重试：%s", exc)

    png_path = await asyncio.to_thread(_png_first_frame_temp, path)
    try:
        return await _upload_once(
            up, scope, scene_id, png_path, os.path.splitext(name)[0] + ".png"
        )
    finally:
        try:
            os.remove(png_path)
        except OSError:
            pass


async def send_image(
    message, api, scope: str, scene_id: str, path: str, name: str
) -> None:
    """
    发一张本地图片（作为对当前消息的被动回复）。

    图片必须先上传换 file_info，再用 msg_type=7 发送。
    上传时 srv_send_msg=False，所以走的是被动回复、不占用主动消息频次。
    """
    up = uploader_of(api)
    file_info = await _upload_local_image(up, scope, scene_id, path, name)

    # message.reply 由 botpy 分发到 post_group_message / post_c2c_message，
    # 并自动带上 msg_id，因此这是被动回复而非主动消息。
    await message.reply(msg_type=7, media={"file_info": file_info})


async def _send_preview_sheet(
    message, api, scope: str, scene_id: str, recs: list
) -> bool:
    """
    把一页缩略图**拼成一张图**，作为对本条消息的被动回复发出去。

    一条消息只能带一张图，所以用拼图承载整页预览；这样只占 1 次被动回复，
    也不依赖主动消息权限。失败返回 False（调用方降级为文字清单）。
    """
    paths = []
    for rec in recs:
        p = await store.get_thumbnail(rec)
        if p:
            paths.append(p)
    if not paths:
        return False

    sheet = await asyncio.to_thread(make_contact_sheet, paths)
    if not sheet:
        return False
    return await _reply_image_bytes(
        message, api, scope, scene_id, sheet, "preview.jpg"
    )


async def _reply_image_bytes(
    message, api, scope: str, scene_id: str, data: bytes, name: str
) -> bool:
    """把一段图片字节落临时文件、上传换 file_info，再作为被动回复发出。"""
    if not data:
        return False
    fd, tmp = tempfile.mkstemp(suffix=".jpg")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        up = uploader_of(api)
        file_info = await _upload_local_image(up, scope, scene_id, tmp, name)
        await message.reply(msg_type=7, media={"file_info": file_info})
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning("发送图片失败: %s", exc)
        return False
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass


async def do_add_image(message, api, scope: str, scene_id: str, text: str) -> None:
    """
    处理「添加」指令（不带斜杠）。

    只有在能拿到图片时才回复；拿不到图（例如群里有人随便打了句「添加 猫」）
    就静默忽略，避免在全量模式的群里刷屏。

    两种图片来源都支持：
      1. 引用一条图片消息，再回复「添加 关键词」（主要用法）
      2. 图片和文字一起发（部分客户端支持）
    """
    att, source = resolve_image_attachment(message)
    if att is None or not getattr(att, "url", None):
        # 必须是「引用一条图片消息」才会入库；没引用图片时保持安静，
        # 否则在全量模式的群里任何一句「添加 xxx」都会触发一条错误提示。
        log.info("[添加] 本条消息没有图片，忽略")
        return

    # 同一条消息里「附带」的图片，必须用带斜杠的 /添加（引用图片时才可以省略斜杠）
    if source == "本条消息" and not text.strip().startswith("/"):
        log.info("[添加] 同消息附图但未带斜杠，忽略")
        return

    keywords = split_keywords(ADD_RE.match(text).group(1) if ADD_RE.match(text) else "")
    uploader_id = getattr(getattr(message, "author", None), "member_openid", None)

    log.info("[添加] 图片来源=%s 关键词=%s", source, keywords)

    group_openid = scene_id if scope == "group" else None
    try:
        result = await store.add_from_attachment(
            url=att.url,
            keywords=keywords,
            uploader=uploader_id,
            orig_filename=getattr(att, "filename", None),
            content_type=getattr(att, "content_type", None),
            group_openid=group_openid,
        )
    except ValueError as exc:
        await message.reply(content=f"入库失败：{exc}")
        return
    except Exception as exc:  # noqa: BLE001
        log.exception("下载或保存图片失败: %s", exc)
        await message.reply(content=f"下载图片失败：{exc}")
        return

    kws = "、".join(result.keywords) if result.keywords else "（未关联关键词）"
    size_kb = result.size / 1024
    src_note = "（来自引用的图片）" if source == "引用的图片消息" else ""
    fmt = (result.mime or "").replace("image/", "").upper() or "未知"
    if result.mime == "image/gif":
        fmt += " 动图"
    fmt_label = result.conversion or fmt

    if result.duplicate:
        if result.newly_linked:
            body = (
                f"这张图已经在库里了（#{result.image_id}）\n"
                f"新补充的关键词：{'、'.join(result.newly_linked)}\n"
                f"当前关键词：{kws}"
            )
        else:
            body = (
                f"这张图已经在库里了（#{result.image_id}），没有新增内容。\n"
                f"当前关键词：{kws}"
            )
    else:
        body = (
            f"入库成功 #{result.image_id}{src_note}\n"
            f"关键词：{kws}\n"
            f"大小：{size_kb:.1f} KB（{fmt_label}）\n"
            f"指纹：{result.sha256[:12]}…"
        )

    log.info(
        "[添加] id=%s dup=%s 新增关键词=%s",
        result.image_id,
        result.duplicate,
        result.newly_linked,
    )
    await message.reply(content=body)


async def do_batch_add(message, api, scope: str, scene_id: str, text: str) -> None:
    """
    处理「批量添加」指令（必须带斜杠）：一次把消息里的多张图片入库。

    图片来源：本条消息附带的全部图片；若本条没有，则取引用消息里的全部图片。
    关键词与单张 /添加 相同，套用到每一张。
    """
    if not text.strip().startswith("/"):
        log.info("[批量添加] 未带斜杠，忽略")
        return

    atts = collect_image_attachments(message)
    if not atts:
        await message.reply(
            content="用法：附带/引用包含图片的消息，回复 /批量添加 关键词（一次可多张）"
        )
        return
    if len(atts) > BATCH_ADD_MAX:
        await message.reply(
            content=f"一次最多批量添加 {BATCH_ADD_MAX} 张，本次 {len(atts)} 张，已取消。"
        )
        return

    match = BATCH_ADD_RE.match(text)
    keywords = split_keywords(match.group(1) if match else "")
    uploader_id = getattr(getattr(message, "author", None), "member_openid", None)
    group_openid = scene_id if scope == "group" else None

    added = dup = failed = 0
    for att in atts:
        url = getattr(att, "url", None)
        if not url:
            failed += 1
            continue
        try:
            res = await store.add_from_attachment(
                url=url,
                keywords=keywords,
                uploader=uploader_id,
                orig_filename=getattr(att, "filename", None),
                content_type=getattr(att, "content_type", None),
                group_openid=group_openid,
            )
            if res.duplicate:
                dup += 1
            else:
                added += 1
        except Exception as exc:  # noqa: BLE001
            failed += 1
            log.exception("批量添加失败: %s", exc)

    body = f"批量添加完成：共 {len(atts)} 张，新增 {added} 张，重复 {dup} 张"
    if failed:
        body += f"，失败 {failed} 张"
    if keywords:
        body += f"\n关键词：{'、'.join(keywords)}"
    log.info(
        "[批量添加] 共=%d 新增=%d 重复=%d 失败=%d", len(atts), added, dup, failed
    )
    await message.reply(content=body)


async def do_add_card(message, api, scope: str, scene_id: str, text: str) -> None:
    """处理「/添加名片 <备注>」：引用一张图片，登记为该用户的游戏名片。"""
    att, source = resolve_image_attachment(message)
    if att is None or not getattr(att, "url", None):
        raw = get_raw(message)
        log.info(
            "[名片] 未找到图片 attachments=%s msg_elements=%s raw_keys=%s",
            [
                (
                    getattr(a, "content_type", None),
                    getattr(a, "filename", None),
                    bool(getattr(a, "url", None)),
                )
                for a in (getattr(message, "attachments", None) or [])
            ],
            bool(raw.get("msg_elements")),
            list(raw.keys()),
        )
        await message.reply(
            content="用法：先「引用一张图片」，再回复 /添加名片 <备注>"
        )
        return

    remark = (CARD_ADD_RE.match(text).group(1) or "").strip()
    if not remark:
        await message.reply(content="用法：/添加名片 <备注>（先引用一张图片）")
        return

    owner = author_openid(message)
    log.info("[名片] 添加 owner=%s 备注=%s 来源=%s", owner[:8], remark, source)
    try:
        card = await store.add_card(
            url=att.url,
            owner=owner,
            remark=remark,
            content_type=getattr(att, "content_type", None),
        )
    except ValueError as exc:
        # 备注重复 / 图库名不合法 / 图片格式问题
        await message.reply(content=f"添加失败：{exc}")
        return
    except Exception as exc:  # noqa: BLE001
        log.exception("添加名片失败: %s", exc)
        await message.reply(content=f"添加失败：{exc}")
        return

    cards = await store.list_cards(owner)
    num = next((i + 1 for i, c in enumerate(cards) if c.id == card.id), len(cards))
    await message.reply(
        content=f"名片添加成功：【{num}】.{card.remark}\n当前共 {len(cards)} 张"
    )


async def do_game_card(message, api, scope: str, scene_id: str, text: str) -> None:
    """处理「/游戏名片」：把该用户的名片竖向拼成一张图发出。"""
    owner = author_openid(message)
    cards = await store.list_cards(owner)
    if not cards:
        await message.reply(
            content="你还没有游戏名片。先引用一张图片，回复 /添加名片 <备注>"
        )
        return

    triples = [(i + 1, c.remark, c.abs_path) for i, c in enumerate(cards)]
    data, included = await asyncio.to_thread(make_card_sheet, triples)
    if not data:
        await message.reply(content="名片拼图生成失败。")
        return

    ok = await _reply_image_bytes(
        message, api, scope, scene_id, data, "cards.jpg"
    )
    body = f"你的游戏名片共 {len(cards)} 张"
    if included < len(cards):
        body += f"（本图显示前 {included} 张）"
    if not ok:
        body += "\n（图片发送失败，名片清单如下）\n" + "\n".join(
            f"【{i + 1}】.{c.remark}" for i, c in enumerate(cards)
        )
    log.info("[名片] 发送 owner=%s 张数=%s 已发=%s", owner[:8], len(cards), ok)
    await message.reply(content=body)


async def do_delete_card(message, api, scope: str, scene_id: str, text: str) -> None:
    """
    处理「/删除名片」：

      - `/删除名片 <备注>` 或 `/删除名片 <编号>`：删自己对应的名片
      - 引用一张图片后 `/删除名片`：删自己名片里指纹相同的那张
    """
    owner = author_openid(message)
    arg = (CARD_DEL_RE.match(text).group(1) or "").strip()

    if arg:
        if arg.isdigit():
            card = await store.delete_card_by_index(owner, int(arg))
        else:
            card = await store.delete_card_by_remark(owner, arg)
        if card is None:
            await message.reply(content=f"没找到你的名片「{arg}」。")
            return
        await message.reply(content=f"已删除名片：{card.remark}")
        return

    att, source = resolve_image_attachment(message)
    if att is None or not getattr(att, "url", None):
        await message.reply(
            content="用法：/删除名片 <备注|编号>，或引用一张图片后回复 /删除名片"
        )
        return
    try:
        sha = await store.fingerprint_of_attachment(
            att.url, getattr(att, "content_type", None)
        )
    except Exception as exc:  # noqa: BLE001
        await message.reply(content=f"删除失败：{exc}")
        return
    card = await store.delete_card_by_sha(owner, sha)
    if card is None:
        await message.reply(content="你的名片里没有这张图。")
        return
    await message.reply(content=f"已删除名片：{card.remark}")


async def do_delete_image(message, api, scope: str, scene_id: str, text: str) -> None:
    """
    处理「删除」指令：引用一张图片，再回复 `/删除 图库名称`。

    图片按指纹只存一份，图库只存指纹索引，所以删除是「把这张图从该图库移除」。
    其他图库若也存了这张图则不受影响；没有任何图库再引用它时，文件才真正清理。
    """
    att, source = resolve_image_attachment(message)
    if att is None or not getattr(att, "url", None):
        await message.reply(
            content="用法：先「引用一张图片」，再回复 /删除 图库名称"
        )
        return

    words = split_keywords(DELETE_RE.match(text).group(1) or "")
    gallery = words[0] if words else ""
    if not gallery:
        await message.reply(content="用法：/删除 图库名称 [私有/公开/全部]（需引用图片）")
        return
    layer = "all"
    if len(words) >= 2 and words[1] in LAYER_WORDS:
        layer = LAYER_WORDS[words[1]]

    group_openid = scene_id if scope == "group" else None
    log.info("[删除] 图库=%s 层=%s 来源=%s", gallery, layer, source)

    try:
        res = await store.delete_from_gallery(
            url=att.url,
            keyword=gallery,
            content_type=getattr(att, "content_type", None),
            group_openid=group_openid,
            layer=layer,
        )
    except ValueError as exc:
        await message.reply(content=f"删除失败：{exc}")
        return
    except Exception as exc:  # noqa: BLE001
        log.exception("下载或删除图片失败: %s", exc)
        await message.reply(content=f"删除失败：{exc}")
        return

    if res.status == "gallery_missing":
        await message.reply(
            content=f"图库「{res.keyword}」不存在（没有任何图片），无法删除。"
        )
        return
    if res.status == "image_unknown":
        await message.reply(
            content=f"图片库里没有这张图（指纹 {res.sha256[:12]}…），无需删除。"
        )
        return
    if res.status == "private_other":
        await message.reply(
            content=f"图库「{res.keyword}」是其他群的群私有图库，这里无法操作。"
        )
        return
    if res.status == "not_in_gallery":
        await message.reply(
            content=(
                f"图库「{res.keyword}」里没有这张图（指纹 {res.sha256[:12]}…），"
                "换一个图库名试试。"
            )
        )
        return

    # deleted
    body = f"已从图库「{res.keyword}」删除 #{res.image_id}（指纹 {res.sha256[:12]}…）"
    if res.gallery_emptied:
        body += "\n该图库已没有图片。"
    if res.orphan_removed:
        body += "\n这张图已不被任何图库引用，文件也一并清理了。"
    if res.purged:
        body += f"\n顺带清理了 {res.purged} 条无图的空关联。"
    log.info(
        "[删除] 已删 #%s 图库=%s 空图库=%s 孤儿清理=%s",
        res.image_id,
        res.keyword,
        res.gallery_emptied,
        res.orphan_removed,
    )
    await message.reply(content=body)


async def do_random_image(message, api, scope: str, scene_id: str, text: str) -> None:
    """处理「来只」指令：随机取一张图并发送。"""
    args = split_keywords(RANDOM_RE.match(text).group(1) or "")
    keyword = args[0] if args else ""
    layer = "all"
    if len(args) >= 2 and args[1] in LAYER_WORDS:
        layer = LAYER_WORDS[args[1]]

    group_openid = scene_id if scope == "group" else None
    rec = await store.random_image(keyword or None, group_openid, layer)
    if rec is None:
        layer_cn = {v: k for k, v in LAYER_WORDS.items()}.get(layer, "全部")
        if keyword:
            # 别名会解析到主关键词再查，查不到时把真实生效的词也告诉用户
            resolved = await store.resolve_keyword(keyword)
            hint = f"（已按主关键词「{resolved}」查询）" if resolved and resolved != keyword else ""
            await message.reply(
                content=(
                    f"没有找到关键词「{keyword}」在「{layer_cn}」层里的图片{hint}，"
                    "换个词/层或先用 /添加 存图。"
                )
            )
        else:
            await message.reply(
                content=f"图库「{layer_cn}」里还没有图片，先用 /添加 存一张图吧。"
            )
        return

    try:
        await send_image(
            message,
            api,
            scope,
            scene_id,
            rec.abs_path,
            os.path.basename(rec.abs_path),
        )
    except UploadError as exc:
        log.exception("上传图片失败: %s", exc)
        await message.reply(content=f"图片发送失败：{exc}")
        return
    except Exception as exc:  # noqa: BLE001
        log.exception("发送图片失败: %s", exc)
        await message.reply(content=f"图片发送失败：{exc}")
        return

    kws = "、".join(rec.keywords) if rec.keywords else "无关键词"
    log.info("[来只] 发出 #%s keyword=%s", rec.id, keyword or "-")

    # 图片已经作为被动消息发出，这里不再重复发文字，避免占用回复次数
    # 如需附带说明，取消下面这行注释即可
    # await message.reply(content=f"#{rec.id} 关键词：{kws}")


async def build_gallery_list_text(
    page: int = 1, group_openid: str | None = None
) -> str:
    """
    构建「所有图库」列表：按图片数量降序，每个图库最多带两个关联词。

    形如：图库A(12)-关联词1 / 图库B[10]-关联词 / 图库C(9)
    各图库用「 / 」隔开（不换行）；关联词超过两个用 … 省略，完整关联词在
    `/图库 关键词` 里展示。别群的私有层不会出现；数字括号区分层级：
    `名称(数量)` = 公开层，`名称[数量]` = 本群私有层。

    - 每页 GALLERY_PAGE_SIZE 个，`page` 从 1 开始。
    - 第 1 页不显示页码；第 2 页起在**消息开头**显示页码。
    - 只有存在更多页（或不在第一页）时，才在**最后换行**补一行小提示。
    """
    page = max(1, page)
    total = await store.gallery_count(group_openid)
    if total == 0:
        return "图库还是空的，先用 /添加 存图。"

    pages = (total + GALLERY_PAGE_SIZE - 1) // GALLERY_PAGE_SIZE
    if page > pages:
        return f"没有第 {page} 页，图库共 {pages} 页。"

    galleries = await store.list_galleries(
        limit=GALLERY_PAGE_SIZE,
        offset=(page - 1) * GALLERY_PAGE_SIZE,
        group_openid=group_openid,
    )
    group = group_openid or ""
    segs = []
    for name, count, aliases, owner in galleries:
        # 层标识靠括号区分：公开层 name(数量)，本群私有层 name[数量]
        if owner and owner == group:
            seg = f"{name}[{count}]"
        else:
            seg = f"{name}({count})"
        if aliases:
            seg += "-" + "-".join(aliases[:2])
            if len(aliases) > 2:
                seg += "…"
        segs.append(seg)

    head = "图库列表（按图片数降序）："
    if page > 1:
        head = f"第{page}/{pages}页\n" + head

    text = head + "\n" + " / ".join(segs)

    # 分页提示：能一页放下就不发
    if total > GALLERY_PAGE_SIZE:
        if page < pages:
            text += f"\n…还有更多，发送 /图库 {page + 1} 看下一页"
        elif page > 1:
            text += f"\n…已是最后一页，发送 /图库 {page - 1} 看上一页"
    return text


def _association_note(info: tuple[str, list[str]] | None, kw: str) -> str:
    """给单个图库补一行关联说明；info 为 (主关键词, [别名...]) 或 None。"""
    if not info:
        return ""
    main, aliases = info
    if kw == main:
        return f"\n主关键词「{main}」　关联词：{'、'.join(aliases)}"
    others = [a for a in aliases if a != kw]
    tail = f"　同组关联词：{'、'.join(others)}" if others else ""
    return f"\n「{kw}」是关联词，主关键词「{main}」{tail}"


def _text_image_list(recs: list) -> str:
    """预览图发不出去时的文字清单兜底。"""
    if not recs:
        return ""
    return "\n" + "\n".join(
        f"#{r.id}  {r.width or '?'}x{r.height or '?'}  "
        f"{'、'.join(r.keywords) or '无关键词'}"
        for r in recs
    )


async def do_gallery(
    message,
    text: str,
    group_openid: str | None = None,
    api=None,
    scope: str = "group",
    scene_id: str | None = None,
) -> None:
    """
    处理「图库」指令：

      - 无参数：列出所有图库（按图片数降序，每个最多带 2 个关联词）
      - `/图库 <关键词> [页码]`：发预览缩略图（每页 8 张）并展示关联词
      - `/图库 统计`：统计信息

    所有结果都只包含当前群可见的图库（别群的私有图库会被隐藏）。
    """
    arg = (GALLERY_RE.match(text).group(1) or "").strip()

    if arg in ("-s", "统计", "stats"):
        s = await store.stats(group_openid)
        await message.reply(
            content=(
                "图库统计：\n"
                f"图片总数：{s['total']} 张\n"
                f"占用空间：{s['total_size'] / 1024 / 1024:.2f} MB\n"
                f"关键词数：{s['keywords']} 个\n"
                f"未关联关键词：{s['untagged']} 张\n"
                f"今日新增：{s['today']} 张\n"
                f"累计被取用：{s['hits']} 次"
            )
        )
        return

    # 纯数字参数 = 翻页（图库名是数字时会和翻页冲突，以翻页优先）
    if arg.isdigit():
        await message.reply(
            content=await build_gallery_list_text(int(arg), group_openid)
        )
        return

    if arg:
        words = split_keywords(arg)
        kw = words[0] if words else arg
        page = 1
        if len(words) >= 2 and words[1].isdigit():
            page = max(1, int(words[1]))

        info = await store.links_for(kw)
        note = _association_note(info, kw)
        if await store.is_gallery_private(kw, group_openid):
            note += "　[群私有]"

        recs = await store.search_images(
            keyword=kw, limit=1000, group_openid=group_openid
        )
        if not recs:
            await message.reply(content=f"关键词「{kw}」下没有图片。{note}")
            return

        total = len(recs)
        pages = (total + GALLERY_IMG_PAGE - 1) // GALLERY_IMG_PAGE
        if page > pages:
            await message.reply(
                content=f"关键词「{kw}」共 {total} 张，没有第 {page} 页（共 {pages} 页）。{note}"
            )
            return
        page_recs = recs[(page - 1) * GALLERY_IMG_PAGE : page * GALLERY_IMG_PAGE]

        header = (
            f"关键词「{kw}」共 {total} 张"
            f"（第 {page}/{pages} 页，每页 {GALLERY_IMG_PAGE}）{note}"
        )

        sheet_ok = False
        if api is not None:
            sheet_ok = await _send_preview_sheet(
                message, api, scope, scene_id or group_openid, page_recs
            )
        if sheet_ok:
            header += f"\n上方拼图共 {len(page_recs)} 张（左上角序号对应下面清单）"
        elif api is not None:
            header += "\n（预览图发送失败，已用文字列出）"
        header += _text_image_list(page_recs)
        if page < pages:
            header += f"\n…发送 /图库 {kw} {page + 1} 看下一页"

        await message.reply(content=header)
        return

    await message.reply(
        content=await build_gallery_list_text(1, group_openid)
    )


async def do_admin_setup(message, text: str) -> None:
    """
    私聊专用：输入口令登记管理员。

    只允许在单聊里使用，且不出现在帮助里。群里即使命中该指令也保持沉默，
    避免口令被回显或引发猜测。
    """
    if not ADMIN_PASSWORD:
        await message.reply(content="管理员登记未启用（未配置 QQ_BOT_ADMIN_PASSWORD）。")
        return

    match = ADMIN_RE.match(text) or ADMIN_EN_RE.match(text)
    given = (match.group(1) or "").strip() if match else ""
    if not given:
        await message.reply(content="用法：管理员 <口令>")
        return
    if given != ADMIN_PASSWORD:
        log.warning("[管理员] 口令错误 openid=%s", (author_openid(message) or "")[:12])
        await message.reply(content="口令错误。")
        return

    openid = author_openid(message)
    if not openid:
        await message.reply(content="无法识别你的 openid，登记失败。")
        return

    newly = await store.add_admin(openid)
    log.info("[管理员] 登记成功 openid=%s 新增=%s", openid[:12], newly)
    await message.reply(
        content="管理员登记成功。" + ("" if newly else "（你之前已是管理员）")
    )


async def do_whoami(message, scope: str, scene_id: str) -> None:
    """查询发送者自己的 openid 与当前群 openid（未列入帮助的辅助指令）。"""
    openid = author_openid(message) or "（未识别）"
    if scope == "group":
        body = (
            "你的 openid：\n" + openid + "\n"
            "当前群 openid：\n" + (scene_id or "（无）")
        )
    else:
        body = "你的 openid：\n" + openid + "\n（单聊没有群 openid）"
    log.info("[whoami] scope=%s openid=%s group=%s", scope, openid[:12], (scene_id or "")[:12])
    await message.reply(content=body)


async def do_set_private(message, scope: str, scene_id: str, text: str) -> None:
    """
    处理「私有」指令（图库分层：公开层 + 每群私有层）。

    - 群聊：`/私有 图库名`，把该图库的公开层图片全部移入本群私有层。
    - 私聊：仅**管理员**可用 `/私有 图库名 <群聊openid>`，对指定群执行同样的操作。
    """
    words = split_keywords(PRIVATE_RE.match(text).group(1) or "")

    if scope == "group":
        if not words:
            await message.reply(content="用法：/私有 图库名（把该图库设为当前群私有）")
            return
        if len(words) >= 2:
            await message.reply(content="群聊里 /私有 只能写图库名，不能带第二个参数。")
            return
        name, target = words[0], scene_id
    else:
        # 私聊：仅管理员
        is_admin = await store.is_admin(author_openid(message))
        if not words:
            # 管理员不带参数：查看现有群私有绑定，方便核对群 openid
            if not is_admin:
                await message.reply(
                    content=(
                        "私聊里用法：/私有 图库名 群聊openid（仅管理员）\n"
                        "群聊 openid 可在群里发 /id 查询。"
                    )
                )
                return
            mapping = await store.gallery_privacy_map()
            if not mapping:
                await message.reply(content="当前没有任何群私有图库。")
                return
            lines = [f"{kw} → {g}" for kw, g in mapping]
            await message.reply(
                content="当前群私有绑定（图库 → 群openid）：\n" + "\n".join(lines)
            )
            return

        if len(words) < 2:
            await message.reply(
                content=(
                    "私聊里用法：/私有 图库名 群聊openid（仅管理员）\n"
                    "群聊 openid 可在群里发 /id 查询。"
                )
            )
            return
        if not is_admin:
            await message.reply(content="只有管理员能在私聊里按群 openid 设置群私有。")
            return
        name, target = words[0], words[1]

    resolved = await store.set_gallery_private(name, target)
    if resolved is None:
        await message.reply(
            content=f"图库「{name}」不存在（没有任何图片），无法设为群私有。"
        )
        return
    log.info("[私有] 图库=%s 目标群=%s", resolved, target[:12])
    if scope == "group":
        await message.reply(
            content=(
                f"图库「{resolved}」已设为当前群私有：\n"
                "公开层的图片已全部移入本群私有层，只有在本群能 /来只 抽到、"
                "也只在本群 /图库 中展示；\n"
                "其他群仍可往公开层 /添加 同名图库。"
            )
        )
    else:
        await message.reply(
            content=(
                f"图库「{resolved}」已设为群「{target}」私有：\n"
                "公开层图片已全部移入该群私有层，只有在该群能 /来只 抽到、"
                "也只在该群 /图库 中展示；\n"
                "其他群仍可往公开层 /添加 同名图库。"
            )
        )


async def do_set_public(message, scope: str, scene_id: str, text: str) -> None:
    """
    处理「公开」指令：取消图库的群私有。

    - 群聊：`/公开 图库名`，取消当前群的私有层。
    - 私聊：仅**管理员**可用 `/公开 图库名 <群聊openid>`，对指定群取消私有。
    """
    words = split_keywords(PUBLIC_RE.match(text).group(1) or "")

    if scope == "group":
        if not words:
            await message.reply(content="用法：/公开 图库名（取消该图库的群私有）")
            return
        name, target = words[0], scene_id
    else:
        if len(words) < 2:
            await message.reply(
                content="私聊里用法：/公开 图库名 群聊openid（仅管理员）"
            )
            return
        if not await store.is_admin(author_openid(message)):
            await message.reply(content="只有管理员能在私聊里按群 openid 取消群私有。")
            return
        name, target = words[0], words[1]

    resolved = await store.set_gallery_public(name, target)
    if resolved is None:
        await message.reply(content=f"「{name}」在该群不是私有图库。")
        return
    log.info("[公开] 图库=%s 群=%s", resolved, target[:10])
    await message.reply(
        content=f"图库「{resolved}」已恢复公开（该群私有层已并回公开层）。"
    )


async def do_delete_gallery(message, text: str) -> None:
    """
    管理员图库层管理。

      - `/删除图库`：列出所有图库层（图库 → 群openid/公开）
      - `/删除图库 <图库名> <群openid|公开>`：删除该图库的指定层
    """
    if not await store.is_admin(author_openid(message)):
        await message.reply(content="只有 Bot 管理员可以使用 /删除图库。")
        return

    words = split_keywords(DELETE_GALLERY_RE.match(text).group(1) or "")

    if not words:
        layers = await store.all_gallery_layers()
        if not layers:
            await message.reply(content="没有任何图库。")
            return
        lines = []
        for kw, cnt, owner in layers:
            where = owner if owner else "公开"
            lines.append(f"{kw}（{cnt}）→ {where}")
        await message.reply(
            content=(
                "所有图库层（图库（数量）→ 群openid/公开）：\n"
                + "\n".join(lines)
                + "\n\n删除用法：/删除图库 图库名 <群openid|公开>"
            )
        )
        return

    if len(words) < 2:
        await message.reply(content="用法：/删除图库 图库名 <群openid|公开>")
        return

    name, where = words[0], words[1]
    target = "" if where == "公开" else where
    res = await store.delete_gallery_layer(name, target)
    if res.status == "gallery_missing":
        await message.reply(content=f"没有找到图库「{name}」的「{where}」层。")
        return

    line = f"已删除图库层「{res.keyword}」→ {where}，共 {res.count} 张"
    if res.orphan_removed:
        line += "，其中不再被引用的图片已清理"
    log.info("[删除图库] 图库=%s 层=%s 张数=%s", res.keyword, where, res.count)
    await message.reply(content=line)


async def do_link(message, text: str) -> None:
    """
    处理「关联」指令：/关联 主关键词 别名1 别名2 …

    建立星型关联——所有别名都直连主关键词。之后 /来只 别名 与 /来只 主 等价。
    """
    parts = split_keywords(LINK_RE.match(text).group(1) or "")
    if len(parts) < 2:
        await message.reply(
            content=(
                "用法：/关联 主关键词 别名1 别名2 …\n"
                "例如：/关联 猫 猫咪 喵喵\n"
                "之后 /来只 猫咪 与 /来只 猫 等价。"
            )
        )
        return

    main, aliases = parts[0], parts[1:]
    try:
        res = await store.link_keywords(main, aliases)
    except ValueError as exc:
        await message.reply(content=f"关联失败：{exc}")
        return

    lines = [f"已建立关联，主关键词：{res.main}"]
    if res.linked:
        lines.append("新增别名：" + "、".join(res.linked))
    if res.updated:
        lines.append("改挂到本主：" + "、".join(res.updated))
    if res.already:
        lines.append("已在关联中：" + "、".join(res.already))
    if res.images_merged:
        lines.append(f"有 {res.images_merged} 张图的关键词已合并到「{res.main}」")

    info = await store.links_for(res.main)
    if info:
        lines.append("当前别名：" + "、".join(info[1]))
    log.info("[关联] 主=%s 新增=%s 改挂=%s", res.main, res.linked, res.updated)
    await message.reply(content="\n".join(lines))


async def do_unlink(message, text: str) -> None:
    """
    处理「取消关联」指令。

    无论传别名还是主关键词，都只把这一个词移出集合，集合其余成员保持关联。
    若移出的是主关键词，会自动从剩余成员里改选一个新主。

    保护规则：删除集合**仅剩的最后一个词**（独立图库）只允许管理员操作，
    普通用户会被拒绝。管理员判定见 `author_openid` / `store.is_admin`。
    """
    parts = split_keywords(UNLINK_RE.match(text).group(1) or "")
    if not parts:
        await message.reply(
            content="用法：/取消关联 关键词1 [关键词2 …]（只把该词移出集合，集合不受影响）"
        )
        return

    allow_last = await store.is_admin(author_openid(message))

    done: list[str] = []
    blocked: list[str] = []
    missing: list[str] = []
    for kw in parts:
        res = await store.unlink_keyword(kw, allow_last=allow_last)
        if res.kind == "alias":
            done.append(f"「{kw}」已与主关键词「{res.main}」断开关联")
        elif res.kind == "main":
            done.append(
                f"「{kw}」已移出集合，自动改选主关键词「{res.main}」，"
                f"集合其余 {res.count - 1} 个成员保持关联"
            )
        elif res.kind == "protected_last":
            blocked.append(kw)
        elif res.kind == "gallery_deleted":
            line = f"已删除图库「{kw}」（管理员），影响 {res.count} 张图片"
            if res.orphan_removed:
                line += "，其中不再被引用的图片已清理"
            done.append(line)
        else:
            missing.append(kw)

    body = "\n".join(done)
    if blocked:
        if body:
            body += "\n"
        body += (
            "以下关键词已是集合仅剩的最后一个词，普通用户不能删除："
            + "、".join(blocked)
            + "（如需删除请联系管理员）"
        )
    if missing:
        if body:
            body += "\n"
        body += "未找到关联：" + "、".join(missing)
    log.info(
        "[取消关联] 成功=%d 拒绝=%s 未找到=%s 管理员=%s",
        len(done),
        blocked,
        missing,
        allow_last,
    )
    await message.reply(content=body or "没有需要处理的关联。")


async def do_find_link(message, text: str) -> None:
    """处理「查找关联」指令：查某词所在集合，或不带参数列出全部集合。"""
    arg = (FIND_LINK_RE.match(text).group(1) or "").strip()

    if arg:
        words = split_keywords(arg)
        kw = words[0] if words else ""
        if not kw:
            await message.reply(content="用法：/查找关联 [关键词]")
            return
        info = await store.links_for(kw)
        if info is None:
            await message.reply(content=f"「{kw}」没有任何关联。")
            return
        main, aliases = info
        if kw == main:
            await message.reply(
                content=f"主关键词「{main}」\n别名：" + "、".join(aliases)
            )
        else:
            others = [a for a in aliases if a != kw]
            await message.reply(
                content=(
                    f"「{kw}」是别名，主关键词为「{main}」\n"
                    "同组别名：" + ("、".join(others) if others else "（无）")
                )
            )
        return

    sets = await store.list_link_sets()
    if not sets:
        await message.reply(content="还没有任何关联。用 /关联 主关键词 别名 建立。")
        return
    lines = [f"{main} ← {'、'.join(aliases)}" for main, aliases in sets[:30]]
    more = "" if len(sets) <= 30 else f"\n…共 {len(sets)} 组"
    await message.reply(content="关联列表（主 ← 别名）：\n" + "\n".join(lines) + more)


def build_help_keyboard() -> dict:
    """把帮助按钮组装成内嵌键盘结构。"""
    rows = []
    for row in HELP_BUTTONS:
        buttons = []
        for i, (label, data, style) in enumerate(row):
            buttons.append(
                {
                    "id": f"help_{data.strip('/') or 'x'}_{i}",
                    "render_data": {
                        "label": label,
                        "visited_label": label,
                        "style": style,
                    },
                    "action": {
                        "type": 1,  # 回调按钮：点击后平台推送 INTERACTION_CREATE
                        "permission": {"type": 2},  # 所有人可点
                        "data": data,
                        "unsupport_tips": "当前客户端版本不支持按钮，请直接发送指令",
                    },
                }
            )
        rows.append({"buttons": buttons})
    return {"content": {"rows": rows}}


async def send_help(message, full: bool = False) -> None:
    """发送帮助。full=True 时给管理员显示全部指令。优先带按钮，失败退化为纯文本。"""
    text = ADMIN_HELP_TEXT if full else HELP_TEXT
    try:
        await message.reply(content=text, keyboard=build_help_keyboard())
    except Exception as exc:  # noqa: BLE001
        log.warning("发送带按钮的帮助失败，退化为纯文本: %s", exc)
        try:
            await message.reply(content=text)
        except Exception as exc2:  # noqa: BLE001
            log.exception("发送帮助失败: %s", exc2)


async def handle_command(message, api, scope: str, scene_id: str, content: str) -> bool:
    """
    处理指令。返回 True 表示已回复，False 表示不是指令。
    回复统一走 message.reply()，SDK 会自动带上被动回复所需的 msg_id。
    """
    text = content.strip()
    cmd = text.lower()

    # 管理员登记：只在私聊生效；群里命中也不回复、不回显，避免口令外泄。
    if ADMIN_RE.match(text) or ADMIN_EN_RE.match(text):
        if scope == "c2c":
            await do_admin_setup(message, text)
        else:
            log.info("[管理员] 非私聊使用，已忽略")
        return True

    if cmd in ("/help", "帮助", "/帮助", "菜单"):
        # 管理员私聊里给完整指令表；群里/普通用户只给用户指令
        full = scope == "c2c" and await store.is_admin(author_openid(message))
        await send_help(message, full=full)
        return True

    if cmd in ("/ping", "/在线", "在线"):
        await message.reply(content="pong 🏓")
        return True

    # /echo、/复读 <内容> —— 复读群用户的话
    match = ECHO_RE.match(text) or ECHO_CN_RE.match(text)
    if match:
        echoed = (match.group(1) or "").strip()
        if not echoed:
            await message.reply(content="用法：/复读 你想让我复读的内容")
            return True
        if len(echoed) > ECHO_MAX_LEN:
            echoed = echoed[:ECHO_MAX_LEN] + "…（内容过长已截断）"
        log.info("[echo] 复读 %d 字: %s", len(echoed), echoed[:40])
        await message.reply(content=echoed)
        return True

    # 关键词关联：先判「取消/查找」，再判「关联」（前缀互不重叠，顺序仅为清晰）
    if UNLINK_RE.match(text):
        await do_unlink(message, text)
        return True

    if FIND_LINK_RE.match(text):
        await do_find_link(message, text)
        return True

    if LINK_RE.match(text):
        await do_link(message, text)
        return True

    # 查询自己的 openid / 当前群 openid（辅助指令）
    if WHOAMI_RE.match(text):
        await do_whoami(message, scope, scene_id)
        return True

    # 群私有图库：/私有、/公开
    if PRIVATE_RE.match(text):
        await do_set_private(message, scope, scene_id, text)
        return True

    if PUBLIC_RE.match(text):
        await do_set_public(message, scope, scene_id, text)
        return True

    # 游戏名片：/添加名片、/游戏名片、/删除名片
    if CARD_ADD_RE.match(text):
        await do_add_card(message, api, scope, scene_id, text)
        return True

    if CARD_RE.match(text):
        await do_game_card(message, api, scope, scene_id, text)
        return True

    # 删除名片必须放在 /删除、/删除图库 之前
    if CARD_DEL_RE.match(text):
        await do_delete_card(message, api, scope, scene_id, text)
        return True

    # 管理员：删除整层图库（必须放在 /删除 之前，否则会被 /删除 抢先匹配）
    if DELETE_GALLERY_RE.match(text):
        await do_delete_gallery(message, text)
        return True

    # 删除图片（引用图片后使用）
    if DELETE_RE.match(text):
        await do_delete_image(message, api, scope, scene_id, text)
        return True

    # 添加 / 批量添加 / 来只 / 图库
    if BATCH_ADD_RE.match(text):
        await do_batch_add(message, api, scope, scene_id, text)
        return True

    if ADD_RE.match(text):
        await do_add_image(message, api, scope, scene_id, text)
        return True

    if RANDOM_RE.match(text):
        if not IMAGE_COOLDOWN_GUARD.allow(scene_id):
            log.info("[来只] 触发限流，已跳过")
            return True
        await do_random_image(message, api, scope, scene_id, text)
        return True

    if GALLERY_RE.match(text):
        await do_gallery(
            message,
            text,
            scene_id if scope == "group" else None,
            api,
            scope,
            scene_id,
        )
        return True

    return False


# ---------------------------------------------------------------------------
# 机器人
# ---------------------------------------------------------------------------
class MyClient(botpy.Client):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._watchdog_task: asyncio.Task | None = None

    async def on_ready(self):
        log.info("机器人「%s」已上线", self.robot.name)
        # 看门狗必须在这里启动：此时事件循环已在运行。
        # 若在 asyncio.run() 之前创建 Client 并调度任务，任务会被挂到另一个
        # 未运行的事件循环上，导致机器人卡在启动阶段无法连接。
        if self._watchdog_task is None or self._watchdog_task.done():
            self._watchdog_task = asyncio.create_task(
                watchdog.watch(WATCHDOG_IDLE_TIMEOUT)
            )

    # 群里被 @ 时触发
    async def on_group_at_message_create(self, message: GroupMessage):
        if dedup.seen(f"at:{message.id}"):
            log.warning("重复事件已忽略: %s", message.id)
            return

        content = normalize_incoming(message.content)
        has_image = pick_image_attachment(message.attachments) is not None
        log.info(
            "[群@] group=%s %s: %s%s",
            message.group_openid,
            (message.author.member_openid or "")[:8],
            content,
            " [+图片]" if has_image else "",
        )

        try:
            # 只有 @ 没有任何内容 → 视为 /help
            if not content:
                await send_help(message)
                return
            # 指令照常处理；认不出的内容直接无视，不做任何回复
            if await handle_command(
                message, self.api, "group", message.group_openid, content
            ):
                return
            log.info("[群@] 非指令消息，已忽略: %s", content[:40])
        except Exception as exc:  # noqa: BLE001
            log.exception("回复群@消息失败: %s", exc)

    # 群主在群设置里开启「接收所有消息」后，群内每条消息都会推到这里。
    #
    # 官方文档明确：开启全量模式后，@机器人的消息也走这个事件（content 已去掉
    # @前缀），不再单独推 GROUP_AT_MESSAGE_CREATE。所以这里的指令处理不是
    # 「可选的全量响应」，而是群里 @ 机器人能不能用指令的关键。
    async def on_group_message_create(self, message: GroupMessage):
        if dedup.seen(f"all:{message.id}"):
            return

        content = normalize_incoming(message.content)
        if not content:
            return

        log.info("[群全量] group=%s %s", message.group_openid, content[:60])

        # 图片指令加一道群级限流，防止群里被连续刷（/来只 另有一层 3 秒限流）
        if (
            BATCH_ADD_RE.match(content)
            or CARD_ADD_RE.match(content)
            or ADD_RE.match(content)
            or RANDOM_RE.match(content)
        ):
            if not cooldown.allow(message.group_openid):
                log.info("[群全量] 图片指令触发限流，已跳过")
                return

        # 指令：与群@完全一致
        try:
            if await handle_command(
                message, self.api, "group", message.group_openid, content
            ):
                return
        except Exception as exc:  # noqa: BLE001
            log.exception("处理群全量指令失败: %s", exc)
            return

        # 非指令：仅在配置了关键词且命中时才响应，避免在群里刷屏
        if not KEYWORD or KEYWORD not in content:
            return

        if not cooldown.allow(message.group_openid):
            log.info("[群全量] 命中关键词但触发限流，已跳过")
            return

        log.info("[群全量] 命中关键词: %s", content[:30])
        try:
            await message.reply(content=f"检测到关键词「{KEYWORD}」")
        except Exception as exc:  # noqa: BLE001
            log.exception("回复群全量消息失败: %s", exc)

    # 消息列表单聊
    async def on_c2c_message_create(self, message: C2CMessage):
        if dedup.seen(f"c2c:{message.id}"):
            return

        content = normalize_incoming(message.content)
        has_image = pick_image_attachment(message.attachments) is not None
        log.info("[单聊] %s%s", content, " [+图片]" if has_image else "")

        openid = getattr(message.author, "user_openid", None) or getattr(
            message.author, "member_openid", None
        )

        try:
            # 空内容（例如只发了一张图/一个表情）→ 视为 /help（管理员给完整表）
            if not content:
                await send_help(
                    message, full=await store.is_admin(author_openid(message))
                )
                return
            if await handle_command(message, self.api, "c2c", openid, content):
                return
            # 认不出的内容直接无视
            log.info("[单聊] 非指令消息，已忽略: %s", content[:40])
        except Exception as exc:  # noqa: BLE001
            log.exception("回复单聊消息失败: %s", exc)

    # 帮助面板上的按钮被点击时触发
    async def on_interaction_create(self, interaction):
        """
        按钮回调。

        平台要求 3 秒内响应，否则用户侧没有任何反馈。
        所以这里先立刻应答，真正的回复放到后台任务里做。
        """
        button_data = ""
        try:
            resolved = getattr(interaction.data, "resolved", None)
            button_data = (getattr(resolved, "button_data", "") or "").strip()
        except Exception:  # noqa: BLE001
            button_data = ""

        log.info(
            "[按钮] data=%s group=%s",
            button_data,
            (getattr(interaction, "group_openid", "") or "")[:10],
        )

        # 先应答（必须在 3 秒内）
        try:
            await self.api.on_interaction_result(interaction.id, code=0)
        except Exception as exc:  # noqa: BLE001
            log.exception("应答按钮交互失败: %s", exc)
            return

        if not button_data:
            return

        group_openid = getattr(interaction, "group_openid", None)
        if not group_openid:
            return

        # 后台执行真正的动作，避免拖慢应答
        asyncio.create_task(self._handle_help_button(group_openid, button_data))

    async def _handle_help_button(self, group_openid: str, command: str) -> None:
        """帮助面板按钮的实际处理，回复到群里。"""
        try:
            if command == "/help":
                await self.api.post_group_message(
                    group_openid=group_openid,
                    msg_type=0,
                    content=HELP_TEXT,
                    keyboard=build_help_keyboard(),
                )
                return

            if command == "/ping":
                await self.api.post_group_message(
                    group_openid=group_openid, msg_type=0, content="pong 🏓"
                )
                return

            if command == "/图库":
                await self.api.post_group_message(
                    group_openid=group_openid,
                    msg_type=0,
                    content=await build_gallery_list_text(1, group_openid),
                )
                return

            if command == "/来只":
                if not IMAGE_COOLDOWN_GUARD.allow(group_openid):
                    log.info("[按钮] 取图触发限流")
                    return
                rec = await store.random_image(group_openid=group_openid)
                if rec is None:
                    await self.api.post_group_message(
                        group_openid=group_openid,
                        msg_type=0,
                        content="图库还是空的，先引用一张图片并回复 /添加 关键词 存图吧。",
                    )
                    return
                up = uploader_of(self.api)
                file_info = await _upload_local_image(
                    up,
                    "group",
                    group_openid,
                    rec.abs_path,
                    os.path.basename(rec.abs_path),
                )
                await self.api.post_group_message(
                    group_openid=group_openid,
                    msg_type=7,
                    media={"file_info": file_info},
                )
                return
        except UploadError as exc:
            log.exception("[按钮] 上传图片失败: %s", exc)
        except Exception as exc:  # noqa: BLE001
            log.exception("[按钮] 处理失败: %s", exc)


def main():
    # 安装原始事件拦截：用于读取「被引用图片」所在的 msg_elements
    install_raw_events()

    # 安装连接看门狗：长连接静默失效时主动退出，避免"假在线"
    watchdog.install()

    # public_messages      -> 群聊 + 单聊事件（GROUP_AND_C2C_EVENT, 1<<25）
    # public_guild_messages -> 频道 @机器人消息（AT_MESSAGE_CREATE, 1<<30）
    # interaction          -> 消息按钮回调（INTERACTION_CREATE, 1<<26）
    intents = botpy.Intents(
        public_messages=True,
        public_guild_messages=True,
        interaction=True,
    )

    client = MyClient(intents=intents)

    # 用 SDK 推荐的 run()，它内部会正确建立并运行事件循环。
    # 不能自己 asyncio.run(client.start(...))：Client 是在此之前构造的，
    # 其内部任务会被调度到另一个未运行的事件循环上，导致卡在启动阶段。
    client.run(appid=APP_ID, secret=APP_SECRET)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log.info("已手动停止")
