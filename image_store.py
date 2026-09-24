"""
图片库存储层。

职责：
  - 下载 QQ 消息中的图片附件并落盘（附件 URL 是临时的，必须立即下载）
  - 按内容 SHA-256 去重，同一张图只存一份
  - 维护关键词关联，支持一图多词、一词多图
  - 提供随机取图与查询统计

目录结构：
  data/images.db           SQLite 元数据
  data/images/ab/ab12...  按哈希前两位分桶存放
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import sqlite3
import struct
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Iterable, Sequence

log = logging.getLogger("qqbot.images")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
IMAGES_DIR = os.path.join(DATA_DIR, "images")
DB_PATH = os.path.join(DATA_DIR, "images.db")

# 单张图片下载上限，防止恶意大文件
MAX_DOWNLOAD_BYTES = 20 * 1024 * 1024

# 关键词最长长度，避免有人塞超长文本
MAX_KEYWORD_LEN = 24
MAX_KEYWORDS_PER_IMAGE = 10

# 游戏名片备注最长长度
CARD_REMARK_MAX = 40

ALLOWED_MIME = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/bmp": ".bmp",
}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS images (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    sha256        TEXT    NOT NULL UNIQUE,
    rel_path      TEXT    NOT NULL,
    mime          TEXT,
    ext           TEXT,
    size          INTEGER NOT NULL,
    width         INTEGER,
    height        INTEGER,
    orig_filename TEXT,
    uploader      TEXT,
    added_at      REAL    NOT NULL,
    hits          INTEGER NOT NULL DEFAULT 0
);

-- 图库分层：一个「图库」= (关键词, owner_group)。
--   owner_group = ''  -> 公开层，任何群/单聊都可见
--   owner_group = 群openid -> 该群私有层，只在该群可见
-- 同一张图可以同时存在多个图库（不同的 keyword 或不同的 owner_group）。
CREATE TABLE IF NOT EXISTS keywords (
    image_id    INTEGER NOT NULL REFERENCES images(id) ON DELETE CASCADE,
    keyword     TEXT    NOT NULL,
    owner_group TEXT    NOT NULL DEFAULT '',
    added_at    REAL    NOT NULL,
    PRIMARY KEY (image_id, keyword, owner_group)
);

-- 关键词关联：别名 -> 主关键词。
--
-- 只保留一层星型结构：所有别名都直接挂在主关键词上，主关键词自己绝不会
-- 作为别名出现（link_keywords 会保证这一点）。因此「断连」永远只发生在
-- 别名与主关键词之间，不会出现别名互相串联的链。
CREATE TABLE IF NOT EXISTS keyword_links (
    alias    TEXT PRIMARY KEY,
    main     TEXT NOT NULL,
    added_at REAL NOT NULL
);

-- Bot 管理员。QQ 官方机器人拿不到 QQ 号，只能用每人唯一的 openid。
-- openid 通过私聊「管理员 <密码>」自助登记（见 bot.py）。
CREATE TABLE IF NOT EXISTS admins (
    openid   TEXT PRIMARY KEY,
    added_at REAL NOT NULL
);

-- 群私有标记：某个关键词在某个群被「设为私有」。
-- 关键词可同时在多个群里私有（每个群各自一层）；没有记录 = 该群没有私有层。
CREATE TABLE IF NOT EXISTS gallery_privacy (
    keyword      TEXT NOT NULL,
    group_openid TEXT NOT NULL,
    added_at     REAL NOT NULL,
    PRIMARY KEY (keyword, group_openid)
);

-- 游戏名片：属于某个用户（openid）的一张图片 + 备注。
-- 图片本体仍存在 images 表（原始文件），这里只引用 image_id。
-- 同一用户备注唯一（重复备注添加失败）。
CREATE TABLE IF NOT EXISTS name_cards (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    owner    TEXT    NOT NULL,
    remark   TEXT    NOT NULL,
    image_id INTEGER NOT NULL REFERENCES images(id) ON DELETE CASCADE,
    added_at REAL    NOT NULL,
    UNIQUE (owner, remark)
);
CREATE INDEX IF NOT EXISTS idx_name_cards_owner ON name_cards(owner);

-- 通过 sha256 反查 id 的辅助索引
-- 注意：keywords 的索引在迁移之后再建（见 _ensure_indexes），避免旧库改名冲突。
CREATE INDEX IF NOT EXISTS idx_images_added ON images(added_at);
CREATE INDEX IF NOT EXISTS idx_keyword_links_main ON keyword_links(main);
"""


@dataclass
class AddResult:
    """添加结果。duplicate=True 表示库里已有同一张图。"""

    image_id: int
    sha256: str
    duplicate: bool
    newly_linked: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    size: int = 0
    conversion: str = ""
    mime: str = ""


@dataclass
class ImageRecord:
    id: int
    sha256: str
    rel_path: str
    abs_path: str
    mime: str | None
    size: int
    width: int | None
    height: int | None
    added_at: float
    hits: int
    keywords: list[str]


@dataclass
class LinkResult:
    """建立关联的结果。main 是最终生效的主关键词（可能是解析后的根词）。"""

    main: str
    linked: list[str] = field(default_factory=list)      # 新挂上的别名
    updated: list[str] = field(default_factory=list)     # 从别的主改挂过来的别名
    already: list[str] = field(default_factory=list)     # 本来就是这个主的别名
    images_merged: int = 0                               # 被合并到主关键词的图片数


@dataclass
class UnlinkResult:
    """
    取消关联的结果。无论取消的是别名还是主关键词，都只把「这一个词」移出集合，
    集合本身保持不变。

    kind:
      - alias           : 移出的是别名，main 为它原来的主关键词
      - main            : 移出的是主关键词，集合已自动改选新主，main 为新主关键词，
                          count 为剩余成员数（新集合的规模）
      - protected_last  : 这是集合仅剩的最后一个词，普通用户禁止删除
      - gallery_deleted : 管理员删除了这个「只剩一个词」的独立图库，count 为受影响的图片数
      - missing         : 这个词没有任何关联（也不是有图的独立图库）
    """

    keyword: str
    kind: str
    main: str = ""
    count: int = 0
    purged: int = 0          # 顺带清理掉的「无图空关键词」关联边数
    orphan_removed: bool = False  # 删除图库时是否顺带清理了不再被引用的图片文件


@dataclass
class DeleteResult:
    """
    从某个图库（关键词）里删除一张图片的结果。

    图片按指纹只存一份，图库只持有「指纹索引」。所以删除是「解除该图库与这张图的
    关联」，其他图库不受影响；当这张图不再被任何图库引用时，顺手清理文件。

    status:
      - deleted         : 已从该图库移除
      - not_in_gallery  : 图库存在，但这张图不在里面
      - gallery_missing : 该图库不存在（没有任何图片，或被关联清理掉了）
      - private_other   : 该图库是「别的群」的群私有，当前群不可操作
      - image_unknown   : 整个图片库里都没有这张图（指纹对不上）
    """

    status: str
    keyword: str
    image_id: int | None = None
    sha256: str = ""
    orphan_removed: bool = False   # 已不被任何图库引用，图片文件也清理了
    gallery_emptied: bool = False  # 移除后该图库已没有任何图片
    purged: int = 0                # 顺带清理掉的无图空关联边数
    count: int = 0                 # 受影响图片数（整层删除时用）


@dataclass
class CardRecord:
    """一张游戏名片。"""

    id: int
    owner: str
    remark: str
    sha256: str
    abs_path: str
    width: int | None = None
    height: int | None = None
    added_at: float = 0.0


def is_valid_gallery_name(name: str) -> bool:
    """
    图库名规则：不能为空、不能是纯数字（避免与 /图库 的页码冲突）。
    空格在分词阶段已被去掉，这里不再单独判断。
    """
    kw = clean_keyword(name)
    return bool(kw) and not kw.isdigit()



def _parse_image_size(data: bytes) -> tuple[int | None, int | None]:
    """从图片字节里解析宽高，支持 PNG / JPEG / GIF / BMP / WebP。"""
    try:
        if data[:8] == b"\x89PNG\r\n\x1a\n":
            w, h = struct.unpack(">II", data[16:24])
            return w, h

        if data[:6] in (b"GIF87a", b"GIF89a"):
            w, h = struct.unpack("<HH", data[6:10])
            return w, h

        if data[:2] == b"BM":
            w, h = struct.unpack("<ii", data[18:26])
            return abs(w), abs(h)

        if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
            chunk = data[12:16]
            if chunk == b"VP8X":
                w = int.from_bytes(data[24:27], "little") + 1
                h = int.from_bytes(data[27:30], "little") + 1
                return w, h
            if chunk == b"VP8 ":
                w = int.from_bytes(data[26:28], "little") & 0x3FFF
                h = int.from_bytes(data[28:30], "little") & 0x3FFF
                return w, h
            if chunk == b"VP8L":
                bits = int.from_bytes(data[21:25], "little")
                return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1

        if data[:2] == b"\xff\xd8":  # JPEG
            idx = 2
            end = len(data)
            while idx + 9 < end:
                if data[idx] != 0xFF:
                    idx += 1
                    continue
                marker = data[idx + 1]
                # SOF0..SOF15（排除 DHT=0xC4 / JPG=0xC8 / DAC=0xCC）
                if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
                    h, w = struct.unpack(">HH", data[idx + 5:idx + 9])
                    return w, h
                seg_len = struct.unpack(">H", data[idx + 2:idx + 4])[0]
                idx += 2 + seg_len
    except Exception:  # noqa: BLE001
        pass
    return None, None


def clean_keyword(item: str) -> str:
    """单个关键词的归一化：去首尾空白与包裹符号、转小写、限长。

    存和查都必须走这个函数，否则 [关键词] 这类写法会匹配不上。
    """
    kw = (item or "").strip().strip(",，、[]【】()（）\"'“”‘’").lower()
    return kw[:MAX_KEYWORD_LEN] if len(kw) > MAX_KEYWORD_LEN else kw


def normalize_keywords(raw: Iterable[str]) -> list[str]:
    """
    清洗关键词：去空白、统一小写（便于匹配）、去重、限制个数与长度。
    输入可以已经按空格/逗号切分过。
    """
    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        kw = clean_keyword(item)
        if not kw or kw in seen:
            continue
        seen.add(kw)
        out.append(kw)
        if len(out) >= MAX_KEYWORDS_PER_IMAGE:
            break
    return out


# 原样保存、原样尝试发送的格式。
#
# 平台这块一直是「文档说支持 gif/webp/bmp、发送接口却可能只认 png/jpg」的
# 薛定谔状态（实测 GIF 会报 850019 格式不支持）。所以 GIF 也归到这里：
# 发得出去就保留动图，发不出去由发送层自动降级成 PNG 首帧（见
# bot.py 的 _upload_local_image），无论如何都不会比原来更差。
SENDABLE_MIME = {"image/png", "image/jpeg", "image/gif"}


def first_frame_png(data: bytes) -> bytes:
    """把任意图片（含动图）转成 PNG，动图只取第一帧。"""
    try:
        import io

        from PIL import Image
    except ImportError:
        raise ValueError(
            "该格式需要转换成 PNG 才能发送，但服务端未安装 Pillow，"
            "请联系管理员执行 pip install Pillow"
        ) from None

    try:
        with Image.open(io.BytesIO(data)) as im:
            im.seek(0)  # 动图只取第一帧
            has_alpha = im.mode in ("RGBA", "LA") or (
                im.mode == "P" and "transparency" in im.info
            )
            target = im.convert("RGBA" if has_alpha else "RGB")
            buf = io.BytesIO()
            target.save(buf, format="PNG", optimize=True)
            return buf.getvalue()
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"图片转换失败：{exc}") from exc


def convert_to_sendable(data: bytes, mime: str) -> tuple[bytes, str, str]:
    """
    把图片归一化成 QQ 能发送的格式。

    返回 (新字节, 新 mime, 说明)。png/jpg/gif 原样返回，其余一律转 PNG（首帧）。
    """
    if mime in SENDABLE_MIME:
        return data, mime, ""

    converted = first_frame_png(data)
    note = f"已由 {mime.replace('image/', '').upper()} 转为 PNG"
    return converted, "image/png", note


# 预览图（缩略图）：长边不超过这么多像素，JPEG 体积压到 100KB 以内。
THUMB_MAX_SIDE = 512
MAX_THUMB_BYTES = 100 * 1024


def make_thumbnail(data: bytes) -> bytes | None:
    """把任意图片压成一张 ≤100KB 的 JPEG 缩略图；失败返回 None。"""
    try:
        import io

        from PIL import Image
    except ImportError:
        return None

    try:
        with Image.open(io.BytesIO(data)) as im:
            im.seek(0)  # 动图只取第一帧
            if im.mode in ("RGBA", "LA") or (
                im.mode == "P" and "transparency" in im.info
            ):
                rgba = im.convert("RGBA")
                bg = Image.new("RGB", rgba.size, (255, 255, 255))
                bg.paste(rgba, mask=rgba.split()[-1])
                im = bg
            else:
                im = im.convert("RGB")

            im.thumbnail((THUMB_MAX_SIDE, THUMB_MAX_SIDE))

            best = b""
            for side in (THUMB_MAX_SIDE, 384, 256, 192, 128):
                work = im.copy()
                work.thumbnail((side, side))
                for quality in (80, 68, 56, 45, 35):
                    buf = io.BytesIO()
                    work.save(buf, format="JPEG", quality=quality, optimize=True)
                    best = buf.getvalue()
                    if len(best) <= MAX_THUMB_BYTES:
                        return best
            return best or None
    except Exception:  # noqa: BLE001
        return None


# 画中文需要的字体（服务器/本地常见路径），可用 QQ_BOT_FONT 覆盖
_FONT_CANDIDATES = [
    os.getenv("QQ_BOT_FONT", ""),
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJKsc-Regular.otf",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    "/usr/share/fonts/truetype/arphic/uming.ttc",
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/simhei.ttf",
    "C:/Windows/Fonts/simsun.ttc",
    "/System/Library/Fonts/PingFang.ttc",
]


def _load_cjk_font(size: int):
    try:
        from PIL import ImageFont
    except ImportError:
        return None
    for path in _FONT_CANDIDATES:
        if path and os.path.isfile(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:  # noqa: BLE001
                continue
    try:
        return ImageFont.load_default()
    except Exception:  # noqa: BLE001
        return None


def make_card_sheet(
    cards: "list[tuple[int, str, str]]",
    width: int = 1920,
    header_h: int = 120,
    gap: int = 20,
    font_size: int = 62,
    max_height: int = 16000,
) -> tuple[bytes | None, int]:
    """
    游戏名片拼接：竖向排列，每张图缩到 `width` 宽（保持长宽比），
    上方留一行「【编号】.备注」。返回 (JPEG 字节 | None, 实际包含张数)。
    """
    try:
        import io

        from PIL import Image, ImageDraw
    except ImportError:
        return None, 0

    font = _load_cjk_font(font_size)
    blocks = []
    total_h = 0
    for num, remark, path in cards:
        if not path or not os.path.isfile(path):
            continue
        try:
            with Image.open(path) as im:
                im = im.convert("RGB")
                if im.width != width:
                    nh = max(1, round(im.height * width / im.width))
                    im = im.resize((width, nh), Image.LANCZOS)
        except Exception:  # noqa: BLE001
            continue
        block_h = header_h + im.height + gap
        if total_h + block_h > max_height:
            break
        blocks.append((f"【{num}】.{remark}", im))
        total_h += block_h

    if not blocks:
        return None, 0

    try:
        sheet = Image.new("RGB", (width, total_h), (255, 255, 255))
        draw = ImageDraw.Draw(sheet)
        y = 0
        for label, im in blocks:
            if font is not None:
                draw.text(
                    (14, y + max(0, (header_h - font_size) // 2)),
                    label,
                    fill=(0, 0, 0),
                    font=font,
                )
            else:
                draw.text((14, y + 10), label, fill=(0, 0, 0))
            y += header_h
            sheet.paste(im, (0, y))
            y += im.height + gap
        buf = io.BytesIO()
        sheet.save(buf, format="JPEG", quality=90, optimize=True)
        return buf.getvalue(), len(blocks)
    except Exception:  # noqa: BLE001
        return None, 0


def make_contact_sheet(
    paths: "list[str]", cols: int = 4, cell: int = 300, pad: int = 6
) -> bytes | None:
    """
    把多张本地图片拼成一张网格图（一条消息只能带一张图，就用拼图承载一页预览）。
    左上角标 1..N 序号。失败返回 None。
    """
    try:
        import io

        from PIL import Image, ImageDraw
    except ImportError:
        return None

    paths = [p for p in paths if p and os.path.isfile(p)]
    if not paths:
        return None

    try:
        n = len(paths)
        cols = max(1, min(cols, n))
        rows = (n + cols - 1) // cols
        width = cols * cell + (cols + 1) * pad
        height = rows * cell + (rows + 1) * pad
        sheet = Image.new("RGB", (width, height), (255, 255, 255))
        draw = ImageDraw.Draw(sheet)

        for i, path in enumerate(paths):
            try:
                with Image.open(path) as im:
                    im = im.convert("RGB")
                    im.thumbnail((cell, cell))
                    r, c = divmod(i, cols)
                    x = pad + c * (cell + pad) + (cell - im.width) // 2
                    y = pad + r * (cell + pad) + (cell - im.height) // 2
                    sheet.paste(im, (x, y))
            except Exception:  # noqa: BLE001
                continue
            r, c = divmod(i, cols)
            bx = pad + c * (cell + pad)
            by = pad + r * (cell + pad)
            draw.rectangle([bx, by, bx + 26, by + 20], fill=(0, 0, 0))
            draw.text((bx + 7, by + 3), str(i + 1), fill=(255, 255, 255))

        buf = io.BytesIO()
        sheet.save(buf, format="JPEG", quality=82, optimize=True)
        return buf.getvalue()
    except Exception:  # noqa: BLE001
        return None


class ImageStore:
    def __init__(
        self,
        db_path: str = DB_PATH,
        images_dir: str = IMAGES_DIR,
        thumbs_dir: str | None = None,
    ):
        self.db_path = db_path
        self.images_dir = images_dir
        self.thumbs_dir = thumbs_dir or os.path.join(
            os.path.dirname(images_dir), "thumbs"
        )
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        os.makedirs(images_dir, exist_ok=True)
        os.makedirs(self.thumbs_dir, exist_ok=True)
        self._init_db()

    def _thumb_path(self, sha: str) -> str:
        return os.path.join(self.thumbs_dir, sha[:2], f"{sha}.jpg")

    # ---------------- 数据库 ----------------

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(_SCHEMA)
            self._migrate(conn)
            self._ensure_indexes(conn)

    def _migrate(self, conn: sqlite3.Connection) -> None:
        """把旧库升级到「图库分层」模型。幂等，安全可重复执行。"""
        # keywords: 旧表主键是 (image_id, keyword)，缺少 owner_group 列
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(keywords)")}
        keywords_upgraded = False
        if "owner_group" not in cols:
            conn.executescript(
                """
                ALTER TABLE keywords RENAME TO keywords_old;
                CREATE TABLE keywords (
                    image_id    INTEGER NOT NULL REFERENCES images(id) ON DELETE CASCADE,
                    keyword     TEXT    NOT NULL,
                    owner_group TEXT    NOT NULL DEFAULT '',
                    added_at    REAL    NOT NULL,
                    PRIMARY KEY (image_id, keyword, owner_group)
                );
                INSERT INTO keywords (image_id, keyword, owner_group, added_at)
                    SELECT image_id, keyword, '', added_at FROM keywords_old;
                DROP TABLE keywords_old;
                """
            )
            keywords_upgraded = True

        # gallery_privacy: 旧表主键是 keyword（一个词只能绑一个群），改成 (keyword, group)
        sql_row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='gallery_privacy'"
        ).fetchone()
        if sql_row and "PRIMARY KEY (keyword, group_openid)" not in (sql_row["sql"] or ""):
            conn.executescript(
                """
                ALTER TABLE gallery_privacy RENAME TO gallery_privacy_old;
                CREATE TABLE gallery_privacy (
                    keyword      TEXT NOT NULL,
                    group_openid TEXT NOT NULL,
                    added_at     REAL NOT NULL,
                    PRIMARY KEY (keyword, group_openid)
                );
                INSERT OR IGNORE INTO gallery_privacy (keyword, group_openid, added_at)
                    SELECT keyword, group_openid, added_at FROM gallery_privacy_old;
                DROP TABLE gallery_privacy_old;
                """
            )

        # 旧模型里「私有词」的图片其实都还在公开层；升级时按私有标记搬到对应群私有层，
        # 才能保持「私有内容只在该群可见」。只在 keywords 刚升级的那一次执行。
        if keywords_upgraded:
            for r in conn.execute(
                "SELECT keyword, group_openid FROM gallery_privacy"
            ).fetchall():
                kw = r["keyword"]
                g = r["group_openid"]
                conn.execute(
                    "INSERT OR IGNORE INTO keywords "
                    "(image_id, keyword, owner_group, added_at) "
                    "SELECT image_id, keyword, ?, added_at FROM keywords "
                    "WHERE keyword = ? AND owner_group = ''",
                    (g, kw),
                )
                conn.execute(
                    "DELETE FROM keywords WHERE keyword = ? AND owner_group = ''",
                    (kw,),
                )

    @staticmethod
    def _ensure_indexes(conn: sqlite3.Connection) -> None:
        conn.executescript(
            """
            CREATE INDEX IF NOT EXISTS idx_keywords_keyword ON keywords(keyword);
            CREATE INDEX IF NOT EXISTS idx_keywords_lookup
                ON keywords(keyword, owner_group);
            """
        )

    # ---------------- 下载 ----------------

    @staticmethod
    def _download_sync(url: str) -> bytes:
        req = urllib.request.Request(
            url, headers={"User-Agent": "Mozilla/5.0 (QQBot)"}
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            # 先看 Content-Length，超限直接拒绝
            length = resp.headers.get("Content-Length")
            if length and int(length) > MAX_DOWNLOAD_BYTES:
                raise ValueError(
                    f"图片过大（{int(length)} 字节），上限 {MAX_DOWNLOAD_BYTES}"
                )
            data = resp.read(MAX_DOWNLOAD_BYTES + 1)

        if len(data) > MAX_DOWNLOAD_BYTES:
            raise ValueError(f"图片过大，上限 {MAX_DOWNLOAD_BYTES} 字节")
        return data

    async def download(self, url: str) -> bytes:
        return await asyncio.to_thread(self._download_sync, url)

    # ---------------- 核心：添加 ----------------

    async def add_from_attachment(
        self,
        url: str,
        keywords: Sequence[str] = (),
        uploader: str | None = None,
        orig_filename: str | None = None,
        content_type: str | None = None,
        group_openid: str | None = None,
    ) -> AddResult:
        """
        下载一张图并入库存档。

        去重依据是图片内容的 SHA-256：同一张图无论换什么文件名、换谁发，
        都只会存一份，但会用新关键词补充关联。

        group_openid 用于群私有限制：不能把图片加进「别的群的私有图库」。
        """
        data = await self.download(url)

        # 归一化成 QQ 能发送的格式（png/jpg/gif），并确定真实 mime
        data, mime, conv_note = self._normalize(data, content_type)
        ext = ALLOWED_MIME[mime]

        # 扩展名跟着真实格式走，否则平台会以「格式不支持」拒绝发送
        if orig_filename:
            base, _old_ext = os.path.splitext(orig_filename)
            orig_filename = base + ext

        sha = hashlib.sha256(data).hexdigest()
        kws = normalize_keywords(keywords)
        for kw in kws:
            if not is_valid_gallery_name(kw):
                raise ValueError(f"图库名不合法（不能是纯数字）：{kw}")

        result = await asyncio.to_thread(
            self._persist,
            data,
            sha,
            ext,
            mime,
            kws,
            uploader,
            orig_filename,
            group_openid,
        )
        result.conversion = conv_note
        result.mime = mime
        return result

    @classmethod
    def _normalize(cls, data: bytes, content_type: str | None) -> tuple[bytes, str, str]:
        """
        把下载到的字节归一化成入库/比对用的形态，返回 (字节, mime, 转换说明)。

        入库和按指纹删除都必须走同一套归一化，否则 WebP/BMP 这类需要转码的图，
        删除时算出的指纹会和入库时不一致，导致「明明在库里却找不到」。
        """
        mime = (content_type or "").lower().split(";")[0].strip()
        if mime not in ALLOWED_MIME:
            # 有些附件不带 content_type，按魔数兜底判断
            mime = cls._guess_mime(data)
        if mime not in ALLOWED_MIME:
            raise ValueError(
                f"不支持的文件类型：{content_type or '未知'}（仅支持 jpg/png/gif/webp/bmp）"
            )

        # 魔数可能和 content_type 不一致（QQ 有时会标错），以真实字节为准
        real_mime = cls._guess_mime(data)
        if real_mime:
            mime = real_mime

        return convert_to_sendable(data, mime)

    @staticmethod
    def _guess_mime(data: bytes) -> str:
        if data[:8] == b"\x89PNG\r\n\x1a\n":
            return "image/png"
        if data[:2] == b"\xff\xd8":
            return "image/jpeg"
        if data[:6] in (b"GIF87a", b"GIF89a"):
            return "image/gif"
        if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
            return "image/webp"
        if data[:2] == b"BM":
            return "image/bmp"
        return ""

    def _ensure_image_row(
        self,
        conn: sqlite3.Connection,
        data: bytes,
        sha: str,
        ext: str,
        mime: str,
        orig_filename: str | None,
        uploader: str | None,
    ) -> tuple[int, bool]:
        """确保图片已落盘并入库；返回 (image_id, 是否已存在)。"""
        rel_path = os.path.join(sha[:2], f"{sha}{ext}")
        abs_path = os.path.join(self.images_dir, rel_path)
        row = conn.execute(
            "SELECT id FROM images WHERE sha256 = ?", (sha,)
        ).fetchone()
        if row is not None:
            return int(row["id"]), True

        os.makedirs(os.path.dirname(abs_path), exist_ok=True)
        # 先写临时文件再改名，避免并发下读到半张图
        tmp_path = abs_path + ".part"
        with open(tmp_path, "wb") as fh:
            fh.write(data)
        os.replace(tmp_path, abs_path)

        w, h = _parse_image_size(data)
        cur = conn.execute(
            """INSERT INTO images
               (sha256, rel_path, mime, ext, size, width, height,
                orig_filename, uploader, added_at, hits)
               VALUES (?,?,?,?,?,?,?,?,?,?,0)""",
            (
                sha,
                rel_path.replace(os.sep, "/"),
                mime,
                ext,
                len(data),
                w,
                h,
                orig_filename,
                uploader,
                time.time(),
            ),
        )
        return int(cur.lastrowid), False

    def _persist(
        self,
        data: bytes,
        sha: str,
        ext: str,
        mime: str,
        kws: list[str],
        uploader: str | None,
        orig_filename: str | None,
        group_openid: str | None = None,
    ) -> AddResult:
        g = self._group_of(group_openid)

        with self._connect() as conn:
            # 入库前把每个关键词解析到主关键词：别名不落库，图片只沉淀在主关键词上。
            # 解析后可能出现重复（两个别名指向同一个主），这里再去重一次。
            # 每个词再决定落到哪一层：本群对这个词设过私有 -> (词, 本群)，否则公开层。
            resolved: list[tuple[str, str]] = []
            for kw in kws:
                root = self._resolve_with(conn, kw)
                if not root:
                    continue
                owner = g if self._is_private_in(conn, root, g) else ""
                if (root, owner) not in resolved:
                    resolved.append((root, owner))
            kws = resolved

            image_id, duplicate = self._ensure_image_row(
                conn, data, sha, ext, mime, orig_filename, uploader
            )

            newly_linked: list[str] = []
            for root, owner in kws:
                cur = conn.execute(
                    """INSERT OR IGNORE INTO keywords
                       (image_id, keyword, owner_group, added_at)
                       VALUES (?,?,?,?)""",
                    (image_id, root, owner, time.time()),
                )
                if cur.rowcount and root not in newly_linked:
                    newly_linked.append(root)

            linked = [
                r["keyword"]
                for r in conn.execute(
                    "SELECT DISTINCT keyword FROM keywords WHERE image_id = ? "
                    "ORDER BY keyword",
                    (image_id,),
                )
            ]

        # 入库时就压好预览图，供 /图库 关键词 展示
        self._write_thumbnail(sha, data)

        return AddResult(
            image_id=image_id,
            sha256=sha,
            duplicate=duplicate,
            newly_linked=newly_linked,
            keywords=linked,
            size=len(data),
        )

    # ---------------- 预览缩略图 ----------------

    def _write_thumbnail(self, sha: str, data: bytes) -> None:
        path = self._thumb_path(sha)
        if os.path.exists(path):
            return
        thumb = make_thumbnail(data)
        if not thumb:
            return
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            tmp = path + ".part"
            with open(tmp, "wb") as fh:
                fh.write(thumb)
            os.replace(tmp, path)
        except OSError:
            pass

    async def get_thumbnail(self, record: ImageRecord) -> str | None:
        """返回缩略图路径；没有就现生成（兼容入库早于本功能的旧图）。失败返回 None。"""
        return await asyncio.to_thread(self._get_thumbnail_sync, record)

    def _get_thumbnail_sync(self, record: ImageRecord) -> str | None:
        path = self._thumb_path(record.sha256)
        if os.path.exists(path):
            return path
        try:
            with open(record.abs_path, "rb") as fh:
                data = fh.read()
        except OSError:
            return None
        self._write_thumbnail(record.sha256, data)
        return path if os.path.exists(path) else None

    # ---------------- 群私有可见性 ----------------

    @staticmethod
    def _group_of(group_openid: str | None) -> str:
        """把群标识归一化；单聊/未提供用空串表示（看不到任何群私有内容）。"""
        return (group_openid or "").strip()

    @staticmethod
    def _layer_visible_sql(col: str = "owner_group") -> str:
        """SQL 片段：该层对当前群可见（公开层，或本群私有层）。需绑定参数 group。"""
        return f"{col} IN ('', ?)"

    @staticmethod
    def _image_visible_sql() -> str:
        """SQL 片段：这张图对当前群可见。需绑定参数 group（images 表别名记为 i）。"""
        return (
            "NOT EXISTS (SELECT 1 FROM keywords kk WHERE kk.image_id = i.id "
            "AND kk.owner_group NOT IN ('', ?))"
        )

    def _is_private_in(
        self, conn: sqlite3.Connection, kw: str, group: str
    ) -> bool:
        """该关键词是否在 group 里设过私有层。"""
        if not group:
            return False
        return (
            conn.execute(
                "SELECT 1 FROM gallery_privacy WHERE keyword = ? AND group_openid = ?",
                (kw, group),
            ).fetchone()
            is not None
        )

    # ---------------- 随机取图 ----------------

    async def random_image(
        self,
        keyword: str | None = None,
        group_openid: str | None = None,
        layer: str = "all",
    ) -> ImageRecord | None:
        """layer: all=公开+本群私有；private=仅本群私有；public=仅公开。"""
        return await asyncio.to_thread(
            self._random_sync, keyword, group_openid, layer
        )

    def _random_sync(
        self,
        keyword: str | None,
        group_openid: str | None = None,
        layer: str = "all",
    ) -> ImageRecord | None:
        g = self._group_of(group_openid)
        # 单聊没有群，不存在「私有层」；显式要私有直接返回空
        if layer == "private" and not g:
            return None
        with self._connect() as conn:
            kw = self._resolve_with(conn, keyword) or None
            if kw:
                if layer == "private":
                    cond = "k.owner_group = ?"
                    params: tuple = (g,)
                elif layer == "public":
                    cond = "k.owner_group = ''"
                    params = ()
                else:
                    cond = "k.owner_group IN ('', ?)"
                    params = (g,)
                row = conn.execute(
                    "SELECT i.* FROM images i JOIN keywords k ON k.image_id = i.id "
                    f"WHERE k.keyword = ? AND {cond} ORDER BY RANDOM() LIMIT 1",
                    (kw, *params),
                ).fetchone()
            elif layer == "private":
                if not g:
                    return None
                row = conn.execute(
                    "SELECT * FROM images i WHERE EXISTS ("
                    "SELECT 1 FROM keywords k WHERE k.image_id = i.id "
                    "AND k.owner_group = ?) ORDER BY RANDOM() LIMIT 1",
                    (g,),
                ).fetchone()
            elif layer == "public":
                row = conn.execute(
                    "SELECT * FROM images i WHERE NOT EXISTS ("
                    "SELECT 1 FROM keywords k WHERE k.image_id = i.id "
                    "AND k.owner_group <> '') ORDER BY RANDOM() LIMIT 1",
                ).fetchone()
            else:
                row = conn.execute(
                    f"SELECT * FROM images i WHERE {self._image_visible_sql()} "
                    "ORDER BY RANDOM() LIMIT 1",
                    (g,),
                ).fetchone()

            if row is None:
                return None

            conn.execute(
                "UPDATE images SET hits = hits + 1 WHERE id = ?", (row["id"],)
            )
            return self._to_record(conn, row, g)

    async def get(self, image_id: int) -> ImageRecord | None:
        return await asyncio.to_thread(self._get_sync, image_id)

    def _get_sync(self, image_id: int) -> ImageRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM images WHERE id = ?", (image_id,)
            ).fetchone()
            return self._to_record(conn, row) if row else None

    def _to_record(
        self, conn: sqlite3.Connection, row: sqlite3.Row, group: str = ""
    ) -> ImageRecord:
        # 只回当前群可见层的关键词，避免把别群私有词名带出来
        kws = [
            r["keyword"]
            for r in conn.execute(
                "SELECT keyword FROM keywords WHERE image_id = ? "
                "AND owner_group IN ('', ?) ORDER BY keyword",
                (row["id"], group),
            )
        ]
        return ImageRecord(
            id=row["id"],
            sha256=row["sha256"],
            rel_path=row["rel_path"],
            abs_path=os.path.join(self.images_dir, row["rel_path"].replace("/", os.sep)),
            mime=row["mime"],
            size=row["size"],
            width=row["width"],
            height=row["height"],
            added_at=row["added_at"],
            hits=row["hits"],
            keywords=kws,
        )

    # ---------------- 查询 ----------------

    async def stats(self, group_openid: str | None = None) -> dict:
        return await asyncio.to_thread(self._stats_sync, group_openid)

    def _stats_sync(self, group_openid: str | None = None) -> dict:
        g = self._group_of(group_openid)
        img_vis = self._image_visible_sql()
        with self._connect() as conn:
            total = conn.execute(
                f"SELECT COUNT(*) c FROM images i WHERE {img_vis}", (g,)
            ).fetchone()["c"]
            total_size = conn.execute(
                f"SELECT COALESCE(SUM(i.size),0) s FROM images i WHERE {img_vis}", (g,)
            ).fetchone()["s"]
            kw_count = conn.execute(
                "SELECT COUNT(DISTINCT keyword) c FROM keywords "
                "WHERE owner_group IN ('', ?)",
                (g,),
            ).fetchone()["c"]
            # 未关联关键词的图片没有图库、也就没有私有层，视为公开
            untagged = conn.execute(
                "SELECT COUNT(*) c FROM images i WHERE NOT EXISTS "
                "(SELECT 1 FROM keywords k WHERE k.image_id = i.id)"
            ).fetchone()["c"]
            today_zero = time.time() - 86400
            today = conn.execute(
                f"SELECT COUNT(*) c FROM images i "
                f"WHERE i.added_at >= ? AND {img_vis}",
                (today_zero, g),
            ).fetchone()["c"]
            hits = conn.execute(
                f"SELECT COALESCE(SUM(i.hits),0) h FROM images i WHERE {img_vis}", (g,)
            ).fetchone()["h"]
        return {
            "total": total,
            "total_size": total_size,
            "keywords": kw_count,
            "untagged": untagged,
            "today": today,
            "hits": hits,
        }

    async def list_keywords(
        self, limit: int = 100, group_openid: str | None = None
    ) -> list[tuple[str, int]]:
        return await asyncio.to_thread(self._list_keywords_sync, limit, group_openid)

    def _list_keywords_sync(
        self, limit: int, group_openid: str | None = None
    ) -> list[tuple[str, int]]:
        g = self._group_of(group_openid)
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT keyword, COUNT(*) c FROM keywords
                   WHERE owner_group IN ('', ?)
                   GROUP BY keyword ORDER BY c DESC, keyword LIMIT ?""",
                (g, limit),
            )
            return [(r["keyword"], r["c"]) for r in rows]

    async def find_keywords(
        self, query: str, group_openid: str | None = None, limit: int = 15
    ) -> list[tuple[str, int]]:
        """模糊查找相关图库：query 是关键词/别名的子串，或它们包含 query。"""
        return await asyncio.to_thread(
            self._find_keywords_sync, query, group_openid, limit
        )

    def _find_keywords_sync(
        self, query: str, group_openid: str | None, limit: int
    ) -> list[tuple[str, int]]:
        q = clean_keyword(query)
        if not q:
            return []
        g = self._group_of(group_openid)
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT keyword, COUNT(*) c FROM keywords "
                "WHERE owner_group IN ('', ?) GROUP BY keyword",
                (g,),
            ).fetchall()
            alias_map: dict[str, list[str]] = {}
            for r in conn.execute("SELECT main, alias FROM keyword_links"):
                alias_map.setdefault(r["main"], []).append(r["alias"])

        scored: list[tuple[tuple, str, int]] = []
        for r in rows:
            kw = r["keyword"]
            if kw == q:
                continue  # 精确命中的走正常流程
            best: tuple | None = None
            for name in [kw, *alias_map.get(kw, [])]:
                if q in name:
                    rank = (0, name.index(q), -r["c"], kw)
                elif name in q:
                    rank = (1, 0, -r["c"], kw)
                else:
                    continue
                if best is None or rank < best:
                    best = rank
            if best is not None:
                scored.append((best, kw, r["c"]))
        scored.sort(key=lambda x: x[0])
        return [(kw, c) for _rank, kw, c in scored[:limit]]

    async def list_galleries(
        self, limit: int = 50, offset: int = 0, group_openid: str | None = None
    ) -> list[tuple[str, int, list[str], str]]:
        """列出图库分层（公开层 + 本群私有层），按图片数量降序，可分页。

        返回 [(图库名, 图片数, [关联词...], owner_group), ...]。
        别群的私有层不会出现；同名的公开层与私有层会各占一条。
        """
        return await asyncio.to_thread(
            self._list_galleries_sync, limit, offset, group_openid
        )

    def _list_galleries_sync(
        self, limit: int, offset: int, group_openid: str | None = None
    ) -> list[tuple[str, int, list[str], str]]:
        g = self._group_of(group_openid)
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT keyword, owner_group, COUNT(*) c FROM keywords
                   WHERE owner_group IN ('', ?)
                   GROUP BY keyword, owner_group ORDER BY c DESC, keyword
                   LIMIT ? OFFSET ?""",
                (g, limit, max(0, offset)),
            ).fetchall()
            out: list[tuple[str, int, list[str], str]] = []
            for row in rows:
                aliases = [
                    r["alias"]
                    for r in conn.execute(
                        "SELECT alias FROM keyword_links WHERE main = ? ORDER BY alias",
                        (row["keyword"],),
                    )
                ]
                out.append(
                    (row["keyword"], row["c"], aliases, row["owner_group"])
                )
            return out

    async def gallery_count(self, group_openid: str | None = None) -> int:
        """图库分层总数（用于分页计算），只算当前群可见的。"""
        return await asyncio.to_thread(self._gallery_count_sync, group_openid)

    def _gallery_count_sync(self, group_openid: str | None = None) -> int:
        g = self._group_of(group_openid)
        with self._connect() as conn:
            return conn.execute(
                """SELECT COUNT(*) c FROM (
                       SELECT DISTINCT keyword, owner_group FROM keywords
                       WHERE owner_group IN ('', ?))""",
                (g,),
            ).fetchone()["c"]

    async def search_images(
        self,
        keyword: str | None = None,
        limit: int = 10,
        group_openid: str | None = None,
    ) -> list[ImageRecord]:
        return await asyncio.to_thread(
            self._search_sync, keyword, limit, group_openid
        )

    def _search_sync(
        self,
        keyword: str | None,
        limit: int,
        group_openid: str | None = None,
    ) -> list[ImageRecord]:
        g = self._group_of(group_openid)
        with self._connect() as conn:
            kw = self._resolve_with(conn, keyword) or None
            if kw:
                rows = conn.execute(
                    """SELECT i.* FROM images i
                       JOIN keywords k ON k.image_id = i.id
                       WHERE k.keyword = ? AND k.owner_group IN ('', ?)
                       ORDER BY i.added_at DESC LIMIT ?""",
                    (kw, g, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    f"SELECT * FROM images i WHERE {self._image_visible_sql()} "
                    "ORDER BY i.added_at DESC LIMIT ?",
                    (g, limit),
                ).fetchall()
            return [self._to_record(conn, r, g) for r in rows]

    async def image_ids_for_keyword(
        self, keyword: str, group_openid: str | None = None
    ) -> list[int]:
        return await asyncio.to_thread(self._ids_sync, keyword, group_openid)

    def _ids_sync(self, keyword: str, group_openid: str | None = None) -> list[int]:
        g = self._group_of(group_openid)
        with self._connect() as conn:
            resolved = self._resolve_with(conn, keyword)
            return [
                r["image_id"]
                for r in conn.execute(
                    "SELECT image_id FROM keywords "
                    "WHERE keyword = ? AND owner_group IN ('', ?)",
                    (resolved, g),
                )
            ]

    # ---------------- 关键词关联（别名 -> 主关键词） ----------------

    def _resolve_with(self, conn: sqlite3.Connection, keyword: str | None) -> str:
        """把关键词解析到它的主关键词；不是别名就返回清洗后的自身。"""
        cur = clean_keyword(keyword or "")
        if not cur:
            return ""
        # 正常情况下只有一层，循环兜底防止历史数据里出现异常链或环
        for _ in range(8):
            row = conn.execute(
                "SELECT main FROM keyword_links WHERE alias = ?", (cur,)
            ).fetchone()
            if row is None or not row["main"] or row["main"] == cur:
                return cur
            cur = row["main"]
        return cur

    async def resolve_keyword(self, keyword: str | None) -> str:
        return await asyncio.to_thread(self._resolve_sync, keyword)

    def _resolve_sync(self, keyword: str | None) -> str:
        with self._connect() as conn:
            return self._resolve_with(conn, keyword)

    async def link_keywords(
        self, main: str, aliases: Sequence[str]
    ) -> LinkResult:
        return await asyncio.to_thread(self._link_sync, main, list(aliases))

    def _link_sync(self, main: str, aliases: Sequence[str]) -> LinkResult:
        main_kw = clean_keyword(main)
        if not main_kw:
            raise ValueError("主关键词不能为空")
        if not is_valid_gallery_name(main_kw):
            raise ValueError(f"主关键词不能是纯数字：{main_kw}")

        cleaned: list[str] = []
        for a in aliases:
            a = clean_keyword(a)
            if not a:
                continue
            if not is_valid_gallery_name(a):
                raise ValueError(f"别名不能是纯数字：{a}")
            if a not in cleaned:
                cleaned.append(a)
        if not cleaned:
            raise ValueError("请至少提供一个别名关键词")

        with self._connect() as conn:
            # 主关键词若本身是别名，先解析到它的根主，避免出现二级串联
            root = self._resolve_with(conn, main_kw)

            linked: list[str] = []
            updated: list[str] = []
            already: list[str] = []
            merged = 0

            for a in cleaned:
                if a == root:
                    continue

                row = conn.execute(
                    "SELECT main FROM keyword_links WHERE alias = ?", (a,)
                ).fetchone()
                if row is not None and row["main"] == root:
                    already.append(a)
                    continue

                # a 若是别的集合的主关键词，把它名下的别名一并改挂到 root，
                # 这样无论怎么关联都维持「一层星型」。
                children = conn.execute(
                    "SELECT alias FROM keyword_links WHERE main = ?", (a,)
                ).fetchall()
                for child in children:
                    conn.execute(
                        "UPDATE keyword_links SET main = ? WHERE alias = ?",
                        (root, child["alias"]),
                    )

                # 把已经用别名标注过的图片改标到主关键词（保留各自的 owner_group 层），
                # 保证 /来只别名 == /来只主
                cur = conn.execute(
                    "INSERT OR IGNORE INTO keywords "
                    "(image_id, keyword, owner_group, added_at) "
                    "SELECT image_id, ?, owner_group, added_at FROM keywords "
                    "WHERE keyword = ?",
                    (root, a),
                )
                merged += cur.rowcount
                conn.execute("DELETE FROM keywords WHERE keyword = ?", (a,))

                # 别名若在群里设过私有标记，一并挂到主关键词上，避免改主后私有失效
                conn.execute(
                    "INSERT OR IGNORE INTO gallery_privacy "
                    "(keyword, group_openid, added_at) "
                    "SELECT ?, group_openid, added_at FROM gallery_privacy "
                    "WHERE keyword = ?",
                    (root, a),
                )
                conn.execute("DELETE FROM gallery_privacy WHERE keyword = ?", (a,))

                if row is None:
                    conn.execute(
                        "INSERT INTO keyword_links (alias, main, added_at) "
                        "VALUES (?, ?, ?)",
                        (a, root, time.time()),
                    )
                    linked.append(a)
                else:
                    conn.execute(
                        "UPDATE keyword_links SET main = ? WHERE alias = ?",
                        (root, a),
                    )
                    updated.append(a)

        return LinkResult(
            main=root,
            linked=linked,
            updated=updated,
            already=already,
            images_merged=merged,
        )

    async def unlink_keyword(
        self, keyword: str, allow_last: bool = False
    ) -> UnlinkResult:
        """移出一个关键词。allow_last=True（管理员）才允许删掉集合仅剩的最后一个词。"""
        return await asyncio.to_thread(self._unlink_sync, keyword, allow_last)

    def _unlink_sync(self, keyword: str, allow_last: bool = False) -> UnlinkResult:
        kw = clean_keyword(keyword)
        if not kw:
            return UnlinkResult(keyword="", kind="missing")

        with self._connect() as conn:
            # 情况一：是别名 —— 只断开它与主关键词这一条边，集合其余成员不受影响
            row = conn.execute(
                "SELECT main FROM keyword_links WHERE alias = ?", (kw,)
            ).fetchone()
            if row is not None:
                conn.execute("DELETE FROM keyword_links WHERE alias = ?", (kw,))
                # 关键词删空后就地清理：集合若已无任何图片则整体消失
                purged = self._purge_empty_collections_with(conn)
                return UnlinkResult(
                    keyword=kw, kind="alias", main=row["main"], count=1, purged=purged
                )

            # 情况二：是主关键词 —— 只把它自己移出集合，集合必须保持完整。
            # 从剩余成员里自动选一个新主（取字典序最小的，保证结果稳定），
            # 其余成员改挂新主，图片池也随集合迁到新主名下。
            members = [
                r["alias"]
                for r in conn.execute(
                    "SELECT alias FROM keyword_links WHERE main = ? ORDER BY alias",
                    (kw,),
                )
            ]
            if not members:
                # 情况三：既不是别名也不是主 —— 它是独立图库（集合只剩它一个关键词）。
                # 删掉它就等于删掉集合的最后一个词：普通用户禁止，管理员才允许。
                has_image = conn.execute(
                    "SELECT 1 FROM keywords WHERE keyword = ? LIMIT 1", (kw,)
                ).fetchone()
                if has_image is None:
                    return UnlinkResult(keyword=kw, kind="missing")
                if not allow_last:
                    return UnlinkResult(keyword=kw, kind="protected_last")
                return self._delete_gallery_with(conn, kw)

            new_main = members[0]
            # 新主不能再作为别名存在
            conn.execute("DELETE FROM keyword_links WHERE alias = ?", (new_main,))
            # 其余成员改挂新主
            conn.execute(
                "UPDATE keyword_links SET main = ? WHERE main = ?", (new_main, kw)
            )
            # 图片池跟随集合（保留各自 owner_group 层）：旧主退化为普通词
            conn.execute(
                "INSERT OR IGNORE INTO keywords "
                "(image_id, keyword, owner_group, added_at) "
                "SELECT image_id, ?, owner_group, added_at FROM keywords "
                "WHERE keyword = ?",
                (new_main, kw),
            )
            conn.execute("DELETE FROM keywords WHERE keyword = ?", (kw,))
            # 私有标记也一并迁到新主，避免改主后私有失效
            conn.execute(
                "INSERT OR IGNORE INTO gallery_privacy "
                "(keyword, group_openid, added_at) "
                "SELECT ?, group_openid, added_at FROM gallery_privacy "
                "WHERE keyword = ?",
                (new_main, kw),
            )
            conn.execute("DELETE FROM gallery_privacy WHERE keyword = ?", (kw,))

            # 关键词删空后就地清理：图片池为空则整个集合消失
            purged = self._purge_empty_collections_with(conn)

            return UnlinkResult(
                keyword=kw,
                kind="main",
                main=new_main,
                count=len(members),
                purged=purged,
            )

    def _delete_gallery_with(
        self, conn: sqlite3.Connection, kw: str
    ) -> UnlinkResult:
        """管理员操作：删除独立图库（把该关键词从所有图片上摘掉，并清理孤儿图片）。"""
        image_ids = [
            r["image_id"]
            for r in conn.execute(
                "SELECT image_id FROM keywords WHERE keyword = ?", (kw,)
            )
        ]
        conn.execute("DELETE FROM keywords WHERE keyword = ?", (kw,))
        # 图库被管理员删除，私有标记也一并清掉
        conn.execute("DELETE FROM gallery_privacy WHERE keyword = ?", (kw,))

        orphans: list[tuple[str, str]] = []
        for image_id in image_ids:
            row = conn.execute(
                "SELECT sha256, rel_path FROM images WHERE id = ?", (image_id,)
            ).fetchone()
            rel = self._gc_image_if_orphan(conn, image_id)
            if rel is not None and row is not None:
                orphans.append((rel, row["sha256"]))

        purged = self._purge_empty_collections_with(conn)

        for rel, sha in orphans:
            self._remove_image_files(rel, sha)

        return UnlinkResult(
            keyword=kw,
            kind="gallery_deleted",
            count=len(image_ids),
            orphan_removed=bool(orphans),
            purged=purged,
        )

    async def links_for(self, keyword: str) -> tuple[str, list[str]] | None:
        """返回该关键词所在集合的 (主关键词, 全部别名)；没有关联返回 None。"""
        return await asyncio.to_thread(self._links_for_sync, keyword)

    def _links_for_sync(self, keyword: str) -> tuple[str, list[str]] | None:
        kw = clean_keyword(keyword)
        if not kw:
            return None

        with self._connect() as conn:
            main = self._resolve_with(conn, kw)
            aliases = [
                r["alias"]
                for r in conn.execute(
                    "SELECT alias FROM keyword_links WHERE main = ? ORDER BY alias",
                    (main,),
                )
            ]
            if not aliases:
                return None
            return main, aliases

    async def list_link_sets(self) -> list[tuple[str, list[str]]]:
        """列出所有关联集合，返回 [(主关键词, [别名...]), ...]。"""
        return await asyncio.to_thread(self._list_link_sets_sync)

    def _list_link_sets_sync(self) -> list[tuple[str, list[str]]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT main, alias FROM keyword_links ORDER BY main, alias"
            ).fetchall()

        sets: dict[str, list[str]] = {}
        for row in rows:
            sets.setdefault(row["main"], []).append(row["alias"])
        return list(sets.items())

    # ---------------- 群私有图库 ----------------

    async def set_gallery_private(
        self, keyword: str, group_openid: str
    ) -> str | None:
        """
        在 group 里把图库设为私有：把该图库的**公开层图片全部移入 (词, group) 私有层**，
        并打上私有标记；之后该群的 /添加 会进私有层。图库不存在返回 None。
        """
        return await asyncio.to_thread(self._set_private_sync, keyword, group_openid)

    def _set_private_sync(self, keyword: str, group_openid: str) -> str | None:
        g = self._group_of(group_openid)
        kw = clean_keyword(keyword)
        if not g or not kw:
            return None
        with self._connect() as conn:
            root = self._resolve_with(conn, kw)
            exists = conn.execute(
                "SELECT 1 FROM keywords WHERE keyword = ? LIMIT 1", (root,)
            ).fetchone()
            if exists is None:
                return None
            # 公开层 -> 本群私有层
            conn.execute(
                "INSERT OR IGNORE INTO keywords "
                "(image_id, keyword, owner_group, added_at) "
                "SELECT image_id, keyword, ?, added_at FROM keywords "
                "WHERE keyword = ? AND owner_group = ''",
                (g, root),
            )
            conn.execute(
                "DELETE FROM keywords WHERE keyword = ? AND owner_group = ''", (root,)
            )
            conn.execute(
                "INSERT OR IGNORE INTO gallery_privacy "
                "(keyword, group_openid, added_at) VALUES (?, ?, ?)",
                (root, g, time.time()),
            )
            return root

    async def set_gallery_public(
        self, keyword: str, group_openid: str
    ) -> str | None:
        """
        在 group 里取消私有：把 (词, group) 私有层**并回公开层**，去掉私有标记。
        本来就没在该群设过私有则返回 None。
        """
        return await asyncio.to_thread(self._set_public_sync, keyword, group_openid)

    def _set_public_sync(self, keyword: str, group_openid: str) -> str | None:
        g = self._group_of(group_openid)
        kw = clean_keyword(keyword)
        if not g or not kw:
            return None
        with self._connect() as conn:
            root = self._resolve_with(conn, kw)
            row = conn.execute(
                "SELECT 1 FROM gallery_privacy "
                "WHERE keyword = ? AND group_openid = ?",
                (root, g),
            ).fetchone()
            if row is None:
                return None
            # 私有层 -> 公开层
            conn.execute(
                "INSERT OR IGNORE INTO keywords "
                "(image_id, keyword, owner_group, added_at) "
                "SELECT image_id, keyword, '', added_at FROM keywords "
                "WHERE keyword = ? AND owner_group = ?",
                (root, g),
            )
            conn.execute(
                "DELETE FROM keywords WHERE keyword = ? AND owner_group = ?",
                (root, g),
            )
            conn.execute(
                "DELETE FROM gallery_privacy WHERE keyword = ? AND group_openid = ?",
                (root, g),
            )
            return root

    async def is_gallery_private(
        self, keyword: str, group_openid: str | None
    ) -> bool:
        """该图库是否在给定群设过私有层。"""
        return await asyncio.to_thread(
            self._is_private_public_sync, keyword, group_openid
        )

    def _is_private_public_sync(
        self, keyword: str, group_openid: str | None
    ) -> bool:
        g = self._group_of(group_openid)
        with self._connect() as conn:
            root = self._resolve_with(conn, keyword)
            return self._is_private_in(conn, root, g)

    async def gallery_privacy_map(self) -> list[tuple[str, str]]:
        """全部私有标记：[(图库名, 群openid), ...]。"""
        return await asyncio.to_thread(self._privacy_map_sync)

    def _privacy_map_sync(self) -> list[tuple[str, str]]:
        with self._connect() as conn:
            return [
                (r["keyword"], r["group_openid"])
                for r in conn.execute(
                    "SELECT keyword, group_openid FROM gallery_privacy "
                    "ORDER BY keyword, group_openid"
                )
            ]

    # ---------------- 管理员 ----------------

    async def is_admin(self, openid: str | None) -> bool:
        return await asyncio.to_thread(self._is_admin_sync, openid)

    def _is_admin_sync(self, openid: str | None) -> bool:
        oid = (openid or "").strip()
        if not oid:
            return False
        with self._connect() as conn:
            return (
                conn.execute(
                    "SELECT 1 FROM admins WHERE openid = ?", (oid,)
                ).fetchone()
                is not None
            )

    async def add_admin(self, openid: str | None) -> bool:
        """登记一个管理员 openid；返回 True 表示是新登记（之前不是）。"""
        return await asyncio.to_thread(self._add_admin_sync, openid)

    def _add_admin_sync(self, openid: str | None) -> bool:
        oid = (openid or "").strip()
        if not oid:
            return False
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT OR IGNORE INTO admins (openid, added_at) VALUES (?, ?)",
                (oid, time.time()),
            )
            return bool(cur.rowcount)

    async def list_admins(self) -> list[str]:
        return await asyncio.to_thread(self._list_admins_sync)

    def _list_admins_sync(self) -> list[str]:
        with self._connect() as conn:
            return [r["openid"] for r in conn.execute(
                "SELECT openid FROM admins ORDER BY added_at")]

    async def remove_admin(self, openid: str | None) -> bool:
        return await asyncio.to_thread(self._remove_admin_sync, openid)

    def _remove_admin_sync(self, openid: str | None) -> bool:
        oid = (openid or "").strip()
        if not oid:
            return False
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM admins WHERE openid = ?", (oid,))
            return bool(cur.rowcount)

    # ---------------- 从图库删除图片（按指纹） ----------------

    def _purge_empty_collections_with(self, conn: sqlite3.Connection) -> int:
        """
        清理「空关键词」：某个集合的主关键词名下已经一张图都没有时，把该集合的
        关联边全部删掉。图片都按主关键词沉淀，所以主为空 == 整个集合没有图片。
        """
        removed = 0
        mains = [
            r["main"]
            for r in conn.execute("SELECT DISTINCT main FROM keyword_links")
        ]
        for main_kw in mains:
            has_image = conn.execute(
                "SELECT 1 FROM keywords WHERE keyword = ? LIMIT 1", (main_kw,)
            ).fetchone()
            if has_image is None:
                cur = conn.execute(
                    "DELETE FROM keyword_links WHERE main = ?", (main_kw,)
                )
                removed += cur.rowcount
        # 没有任何图片的私有标记也一并清理（图库已彻底消失）
        for row in conn.execute(
            "SELECT DISTINCT keyword FROM gallery_privacy"
        ).fetchall():
            has_image = conn.execute(
                "SELECT 1 FROM keywords WHERE keyword = ? LIMIT 1", (row["keyword"],)
            ).fetchone()
            if has_image is None:
                removed += conn.execute(
                    "DELETE FROM gallery_privacy WHERE keyword = ?", (row["keyword"],)
                ).rowcount
        return removed

    async def fingerprint_of_attachment(
        self, url: str, content_type: str | None = None
    ) -> str:
        """下载附件并返回它在库里的指纹（SHA-256），走与入库完全相同的归一化。"""
        data = await self.download(url)
        data, _mime, _note = self._normalize(data, content_type)
        return hashlib.sha256(data).hexdigest()

    async def delete_from_gallery(
        self,
        url: str,
        keyword: str,
        content_type: str | None = None,
        group_openid: str | None = None,
        layer: str = "all",
    ) -> DeleteResult:
        """从指定图库删除引用的这张图片（按指纹匹配）。

        layer: all=私有优先、没有再公开；private=仅本群私有；public=仅公开。
        """
        sha = await self.fingerprint_of_attachment(url, content_type)
        return await asyncio.to_thread(
            self._delete_from_gallery_sync, sha, keyword, group_openid, layer
        )

    def _delete_from_gallery_sync(
        self,
        sha: str,
        keyword: str,
        group_openid: str | None = None,
        layer: str = "all",
    ) -> DeleteResult:
        g = self._group_of(group_openid)
        with self._connect() as conn:
            # 图库名允许是别名，统一解析到主关键词
            kw = self._resolve_with(conn, keyword)
            if not kw:
                return DeleteResult(status="gallery_missing", keyword=keyword)

            if layer == "private":
                layers = [g] if g else []
            elif layer == "public":
                layers = [""]
            else:
                # 全部：本群设过私有 -> 私有层优先，否则公开层；私有层没有再看公开层
                private_here = self._is_private_in(conn, kw, g)
                layers = [g, ""] if private_here else [""]

            row = conn.execute(
                "SELECT id FROM images WHERE sha256 = ?", (sha,)
            ).fetchone()
            if row is None:
                exists = conn.execute(
                    "SELECT 1 FROM keywords WHERE keyword = ? LIMIT 1", (kw,)
                ).fetchone()
                if exists is None:
                    return DeleteResult(status="gallery_missing", keyword=kw)
                return DeleteResult(status="image_unknown", keyword=kw, sha256=sha)
            image_id = int(row["id"])

            # 图库是否存在（任一层）
            exists = conn.execute(
                "SELECT 1 FROM keywords WHERE keyword = ? LIMIT 1", (kw,)
            ).fetchone()
            if exists is None:
                return DeleteResult(status="gallery_missing", keyword=kw)

            target_layer = None
            for cand in layers:
                hit = conn.execute(
                    "SELECT 1 FROM keywords "
                    "WHERE keyword = ? AND image_id = ? AND owner_group = ?",
                    (kw, image_id, cand),
                ).fetchone()
                if hit is not None:
                    target_layer = cand
                    break
            if target_layer is None:
                return DeleteResult(
                    status="not_in_gallery", keyword=kw, image_id=image_id, sha256=sha
                )

            # 只解除「该图库的这一层 <-> 这张图」的关联，其他图库/层不受影响
            conn.execute(
                "DELETE FROM keywords "
                "WHERE keyword = ? AND image_id = ? AND owner_group = ?",
                (kw, image_id, target_layer),
            )

            gallery_emptied = (
                conn.execute(
                    "SELECT 1 FROM keywords WHERE keyword = ? LIMIT 1", (kw,)
                ).fetchone()
                is None
            )

            # 这张图不再被任何图库/名片引用时，连同文件一起清理
            rel_path = self._gc_image_if_orphan(conn, image_id)

            # 图库被删空后，顺手清理没有图片的空关联
            purged = self._purge_empty_collections_with(conn)

        self._remove_image_files(rel_path, sha)

        return DeleteResult(
            status="deleted",
            keyword=kw,
            image_id=image_id,
            sha256=sha,
            orphan_removed=rel_path is not None,
            gallery_emptied=gallery_emptied,
            purged=purged,
        )

    async def delete_gallery_layer(
        self, keyword: str, owner_group: str
    ) -> DeleteResult:
        """管理员：删除某图库的指定层（owner_group='' 为公开层，否则为某群私有层）。"""
        return await asyncio.to_thread(
            self._delete_gallery_layer_sync, keyword, owner_group
        )

    def _delete_gallery_layer_sync(self, keyword: str, owner_group: str) -> DeleteResult:
        owner = self._group_of(owner_group)
        with self._connect() as conn:
            kw = self._resolve_with(conn, keyword)
            if not kw:
                return DeleteResult(status="gallery_missing", keyword=keyword)
            ids = [
                r["image_id"]
                for r in conn.execute(
                    "SELECT image_id FROM keywords "
                    "WHERE keyword = ? AND owner_group = ?",
                    (kw, owner),
                )
            ]
            if not ids:
                return DeleteResult(status="gallery_missing", keyword=kw)

            conn.execute(
                "DELETE FROM keywords WHERE keyword = ? AND owner_group = ?",
                (kw, owner),
            )
            if owner:
                conn.execute(
                    "DELETE FROM gallery_privacy "
                    "WHERE keyword = ? AND group_openid = ?",
                    (kw, owner),
                )

            orphans: list[tuple[str, str]] = []
            for iid in ids:
                r2 = conn.execute(
                    "SELECT sha256, rel_path FROM images WHERE id = ?", (iid,)
                ).fetchone()
                rel = self._gc_image_if_orphan(conn, iid)
                if rel is not None and r2 is not None:
                    orphans.append((rel, r2["sha256"]))

            purged = self._purge_empty_collections_with(conn)

        for rel, sha in orphans:
            self._remove_image_files(rel, sha)

        return DeleteResult(
            status="deleted",
            keyword=kw,
            count=len(ids),
            orphan_removed=bool(orphans),
            purged=purged,
        )

    async def all_gallery_layers(self) -> list[tuple[str, int, str]]:
        """所有图库层：[(图库名, 图片数, owner_group), ...]（owner_group='' 为公开）。"""
        return await asyncio.to_thread(self._all_layers_sync)

    def _all_layers_sync(self) -> list[tuple[str, int, str]]:
        with self._connect() as conn:
            return [
                (r["keyword"], r["c"], r["owner_group"])
                for r in conn.execute(
                    "SELECT keyword, owner_group, COUNT(*) c FROM keywords "
                    "GROUP BY keyword, owner_group ORDER BY keyword, owner_group"
                )
            ]

    # ---------------- 游戏名片 ----------------

    def _card_record(self, conn: sqlite3.Connection, row: sqlite3.Row) -> CardRecord:
        img = conn.execute(
            "SELECT sha256, rel_path, width, height FROM images WHERE id = ?",
            (row["image_id"],),
        ).fetchone()
        if img is None:
            return CardRecord(
                id=row["id"], owner=row["owner"], remark=row["remark"],
                sha256="", abs_path="", added_at=row["added_at"],
            )
        return CardRecord(
            id=row["id"],
            owner=row["owner"],
            remark=row["remark"],
            sha256=img["sha256"],
            abs_path=os.path.join(
                self.images_dir, img["rel_path"].replace("/", os.sep)
            ),
            width=img["width"],
            height=img["height"],
            added_at=row["added_at"],
        )

    async def add_card(
        self, url: str, owner: str, remark: str, content_type: str | None = None
    ) -> CardRecord:
        """添加一张游戏名片（原图入库，按 sha 去重）。同用户备注不可重复。"""
        data = await self.download(url)
        data, mime, _note = self._normalize(data, content_type)
        ext = ALLOWED_MIME[mime]
        sha = hashlib.sha256(data).hexdigest()
        card = await asyncio.to_thread(
            self._persist_card, data, sha, ext, mime, owner, remark
        )
        return card

    def _persist_card(
        self,
        data: bytes,
        sha: str,
        ext: str,
        mime: str,
        owner: str,
        remark: str,
    ) -> CardRecord:
        owner = (owner or "").strip()
        remark = (remark or "").strip()[:CARD_REMARK_MAX]
        if not owner:
            raise ValueError("无法识别用户身份")
        if not remark:
            raise ValueError("备注不能为空")
        if remark.isdigit():
            raise ValueError("备注不能用纯数字（会和编号冲突）")

        with self._connect() as conn:
            dup = conn.execute(
                "SELECT 1 FROM name_cards WHERE owner = ? AND remark = ?",
                (owner, remark),
            ).fetchone()
            if dup is not None:
                raise ValueError("该备注已存在")

            image_id, _dup_img = self._ensure_image_row(
                conn, data, sha, ext, mime, None, None
            )
            cur = conn.execute(
                "INSERT INTO name_cards (owner, remark, image_id, added_at) "
                "VALUES (?, ?, ?, ?)",
                (owner, remark, image_id, time.time()),
            )
            card_id = int(cur.lastrowid)
            row = conn.execute(
                "SELECT * FROM name_cards WHERE id = ?", (card_id,)
            ).fetchone()
            card = self._card_record(conn, row)

        self._write_thumbnail(sha, data)
        return card

    async def list_cards(self, owner: str) -> list[CardRecord]:
        return await asyncio.to_thread(self._list_cards_sync, owner)

    def _list_cards_sync(self, owner: str) -> list[CardRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM name_cards WHERE owner = ? ORDER BY added_at, id",
                ((owner or "").strip(),),
            ).fetchall()
            return [self._card_record(conn, r) for r in rows]

    async def delete_card_by_remark(
        self, owner: str, remark: str
    ) -> CardRecord | None:
        return await asyncio.to_thread(
            self._delete_card_sync,
            owner,
            "remark",
            (remark or "").strip()[:CARD_REMARK_MAX],
        )

    async def delete_card_by_index(self, owner: str, index: int) -> CardRecord | None:
        return await asyncio.to_thread(self._delete_card_sync, owner, "index", int(index))

    async def delete_card_by_sha(self, owner: str, sha: str) -> CardRecord | None:
        return await asyncio.to_thread(self._delete_card_sync, owner, "sha", sha)

    def _delete_card_sync(self, owner: str, kind: str, key) -> CardRecord | None:
        owner = (owner or "").strip()
        rel_path = None
        sha = ""
        with self._connect() as conn:
            if kind == "remark":
                row = conn.execute(
                    "SELECT * FROM name_cards WHERE owner = ? AND remark = ?",
                    (owner, key),
                ).fetchone()
            elif kind == "index":
                rows = conn.execute(
                    "SELECT * FROM name_cards WHERE owner = ? ORDER BY added_at, id",
                    (owner,),
                ).fetchall()
                row = rows[key - 1] if 1 <= key <= len(rows) else None
            else:
                row = conn.execute(
                    "SELECT c.* FROM name_cards c JOIN images i ON i.id = c.image_id "
                    "WHERE c.owner = ? AND i.sha256 = ?",
                    (owner, key),
                ).fetchone()

            if row is None:
                return None

            card = self._card_record(conn, row)
            sha = card.sha256
            image_id = int(row["image_id"])
            conn.execute("DELETE FROM name_cards WHERE id = ?", (row["id"],))
            rel_path = self._gc_image_if_orphan(conn, image_id)

        self._remove_image_files(rel_path, sha)
        return card

    def _remove_image_files(self, rel_path: str | None, sha: str) -> None:
        if rel_path:
            try:
                os.remove(
                    os.path.join(self.images_dir, rel_path.replace("/", os.sep))
                )
            except OSError:
                pass
        if sha:
            try:
                os.remove(self._thumb_path(sha))
            except OSError:
                pass

    def _image_referenced(self, conn: sqlite3.Connection, image_id: int) -> bool:
        """图片是否还被任何图库或名片引用。"""
        if conn.execute(
            "SELECT 1 FROM keywords WHERE image_id = ? LIMIT 1", (image_id,)
        ).fetchone():
            return True
        if conn.execute(
            "SELECT 1 FROM name_cards WHERE image_id = ? LIMIT 1", (image_id,)
        ).fetchone():
            return True
        return False

    def _gc_image_if_orphan(
        self, conn: sqlite3.Connection, image_id: int
    ) -> str | None:
        if self._image_referenced(conn, image_id):
            return None
        row = conn.execute(
            "SELECT rel_path FROM images WHERE id = ?", (image_id,)
        ).fetchone()
        if row is None:
            return None
        conn.execute("DELETE FROM images WHERE id = ?", (image_id,))
        return row["rel_path"]

    async def delete(self, image_id: int) -> bool:
        return await asyncio.to_thread(self._delete_sync, image_id)

    def _delete_sync(self, image_id: int) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT rel_path FROM images WHERE id = ?", (image_id,)
            ).fetchone()
            if row is None:
                return False
            conn.execute("DELETE FROM images WHERE id = ?", (image_id,))
            path = os.path.join(
                self.images_dir, row["rel_path"].replace("/", os.sep)
            )
        try:
            os.remove(path)
        except OSError:
            pass
        return True
