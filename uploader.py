"""
群聊富媒体分片上传。

QQ 开放平台发本地图片必须走「预上传 -> 分片 PUT -> 分片确认 -> 合并」四步，
官方 SDK（botpy 1.2.1）只实现了 URL 直传，因此这里自行实现分片流程。

参考文档：
  https://bot.q.qq.com/wiki/develop/api-v2/server-inter/message/rich-media.html
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os

import aiohttp

log = logging.getLogger("qqbot.uploader")

API_BASE = "https://api.sgroup.qq.com"
# 官方建议上传接口超时 >= 5 秒
UPLOAD_TIMEOUT = aiohttp.ClientTimeout(total=120, connect=10)
PUT_TIMEOUT = aiohttp.ClientTimeout(total=300, connect=15)

FILE_TYPE_IMAGE = 1
MD5_10M_BYTES = 10002432


class UploadError(RuntimeError):
    pass


def _md5_10m(data: bytes) -> str:
    """文件前 10002432 字节的 MD5，用于平台侧秒传判断。"""
    return hashlib.md5(data[:MD5_10M_BYTES]).hexdigest()


# 允许尝试直接发送的图片扩展名。
# gif 是否被平台接受并不稳定（文档说支持、接口可能报 850019），
# 所以这里放行 gif，由调用方在失败时降级成 PNG 首帧重试。
SENDABLE_EXT = (".png", ".jpg", ".jpeg", ".gif")


def _ensure_sendable(file_path: str, file_name: str) -> None:
    """
    发送前校验格式，给出明确报错。

    直接让平台拒绝的话只会返回「富媒体文件格式不支持（850019）」，
    定位不到根因，所以这里提前拦下明显发不出去的格式。
    """
    ext = os.path.splitext(file_name)[1].lower()
    if ext in SENDABLE_EXT:
        return
    raise UploadError(
        f"图片格式 {ext or '(无扩展名)'} 无法发送，QQ 目前只支持 png/jpg/gif。"
        "请重新用「添加」入库，入库时会自动转成 PNG。"
    )


class MediaUploader:
    """
    用法：
        uploader = MediaUploader(api)
        file_info = await uploader.upload_group_image(group_openid, "/path/a.jpg")
        await api.post_group_message(group_openid=..., msg_type=7,
                                     media=Media(file_info=file_info), msg_id=...)
    """

    def __init__(self, api):
        self._api = api

    async def upload_group_image(
        self, group_openid: str, file_path: str, file_name: str | None = None
    ) -> str:
        """上传本地图片到群聊，返回 file_info 字符串。"""
        return await self._upload(
            path_prefix=f"/v2/groups/{group_openid}",
            file_path=file_path,
            file_name=file_name,
        )

    async def upload_c2c_image(
        self, openid: str, file_path: str, file_name: str | None = None
    ) -> str:
        """上传本地图片到单聊，返回 file_info 字符串。"""
        return await self._upload(
            path_prefix=f"/v2/users/{openid}",
            file_path=file_path,
            file_name=file_name,
        )

    # ---------------- 内部实现 ----------------

    async def _upload(
        self, path_prefix: str, file_path: str, file_name: str | None
    ) -> str:
        if not os.path.isfile(file_path):
            raise UploadError(f"文件不存在: {file_path}")

        file_name = file_name or os.path.basename(file_path)
        _ensure_sendable(file_path, file_name)

        with open(file_path, "rb") as fh:
            data = fh.read()

        file_size = len(data)

        log.info("开始上传图片 %s（%d 字节）", file_name, file_size)

        # 1. 预上传
        prepare = await self._prepare(path_prefix, data, file_name, file_size)
        upload_id = prepare.get("upload_id")
        parts = prepare.get("parts") or []
        if not upload_id or not parts:
            raise UploadError(f"预上传返回异常: {prepare}")

        config = prepare.get("upload_config") or {}
        concurrency = max(1, int(config.get("concurrency", 1) or 1))
        retry_timeout = int(config.get("retry_timeout", 300) or 300)
        retry_delay = int(config.get("retry_delay", 1) or 1)

        log.info(
            "预上传成功 upload_id=%s 分片数=%d 并发=%d",
            upload_id,
            len(parts),
            concurrency,
        )

        # 2 + 3. 分片 PUT，然后逐片确认
        sem = asyncio.Semaphore(concurrency)

        async def handle_part(part: dict) -> None:
            async with sem:
                await self._put_part(
                    path_prefix, upload_id, part, data, retry_timeout, retry_delay
                )

        await asyncio.gather(*(handle_part(p) for p in parts))

        # 4. 合并，拿 file_info
        file_info = await self._finish(path_prefix, upload_id, file_name)
        log.info("上传完成 file_info=%s", str(file_info)[:24])
        return file_info

    async def _prepare(
        self, path_prefix: str, data: bytes, file_name: str, file_size: int
    ) -> dict:
        payload = {
            "file_type": FILE_TYPE_IMAGE,
            "file_size": str(file_size),
            "file_name": file_name,
            "md5": hashlib.md5(data).hexdigest(),
            "sha1": hashlib.sha1(data).hexdigest(),
            "md5_10m": _md5_10m(data),
        }
        try:
            resp = await self._api._http.request(
                _route(path_prefix + "/upload_prepare"), json=payload
            )
        except Exception as exc:  # noqa: BLE001
            raise UploadError(f"预上传请求失败: {exc}") from exc
        return _as_dict(resp)

    async def _put_part(
        self,
        path_prefix: str,
        upload_id: str,
        part: dict,
        data: bytes,
        retry_timeout: int,
        retry_delay: int,
    ) -> None:
        index = int(part.get("index", 0))
        block_size = int(part.get("block_size") or 0)
        url = part.get("presigned_url")
        if not url:
            raise UploadError(f"分片 {index} 缺少 presigned_url")

        # 注意：平台返回的分片序号是从 1 开始的（不是 0），
        # 必须按 (index - 1) 取偏移，否则会切出空分片，
        # 合并时报 850019「富媒体文件格式不支持」。
        if index >= 1:
            start = (index - 1) * block_size
        else:
            start = 0
        chunk = data[start : start + block_size]

        if not chunk:
            raise UploadError(
                f"分片 {index} 切出空数据（block_size={block_size}, "
                f"文件共 {len(data)} 字节）"
            )

        # PUT 到预签名 URL（不带平台鉴权头，签名已在 URL 里）
        last_err: Exception | None = None
        deadline = asyncio.get_event_loop().time() + retry_timeout
        while asyncio.get_event_loop().time() < deadline:
            try:
                async with aiohttp.ClientSession(timeout=PUT_TIMEOUT) as session:
                    async with session.put(url, data=chunk) as resp:
                        if resp.status not in (200, 201, 204):
                            body = await resp.text()
                            raise UploadError(
                                f"分片 {index} PUT 失败 {resp.status}: {body[:200]}"
                            )
                last_err = None
                break
            except Exception as exc:  # noqa: BLE001
                last_err = exc
                log.warning("分片 %d 上传失败，%ds 后重试: %s", index, retry_delay, exc)
                await asyncio.sleep(retry_delay)

        if last_err is not None:
            raise UploadError(f"分片 {index} 重试超时: {last_err}")

        # 确认分片
        payload = {
            "upload_id": upload_id,
            "part_index": index,
            "block_size": str(len(chunk)),
            "md5": hashlib.md5(chunk).hexdigest(),
        }
        try:
            await self._api._http.request(
                _route(path_prefix + "/upload_part_finish"), json=payload
            )
        except Exception as exc:  # noqa: BLE001
            raise UploadError(f"分片 {index} 确认失败: {exc}") from exc

    async def _finish(self, path_prefix: str, upload_id: str, file_name: str) -> str:
        payload = {
            "file_type": FILE_TYPE_IMAGE,
            "srv_send_msg": False,  # 仅换 file_info，发送走被动回复不占主动频次
            "file_name": file_name,
            "upload_id": upload_id,
        }
        try:
            resp = await self._api._http.request(
                _route(path_prefix + "/files"), json=payload
            )
        except Exception as exc:  # noqa: BLE001
            raise UploadError(f"合并文件失败: {exc}") from exc

        body = _as_dict(resp)
        file_info = body.get("file_info")
        if not file_info:
            raise UploadError(f"合并后未返回 file_info: {body}")
        return file_info


def _as_dict(resp) -> dict:
    """SDK 的 http 层可能返回 dict 或 JSON 字符串，这里统一成 dict。"""
    if isinstance(resp, dict):
        return resp
    if isinstance(resp, (str, bytes)):
        import json

        try:
            return json.loads(resp)
        except Exception:  # noqa: BLE001
            return {}
    return {}


def _route(path: str):
    """延迟导入 Route，避免循环依赖。"""
    from botpy.http import Route

    return Route("POST", path)
