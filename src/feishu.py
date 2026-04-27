"""
飞书多维表格录入

功能：
- 按 arXiv 链接查询去重
- 插入论文记录（10 个字段）
- 图片：从 URL 下载 → 上传到多维表格为附件
"""
from datetime import datetime
from typing import Optional, Dict, Any

import httpx
import lark_oapi as lark
from lark_oapi.api.bitable.v1 import (
    SearchAppTableRecordRequest,
    SearchAppTableRecordRequestBody,
    CreateAppTableRecordRequest,
    AppTableRecord,
    FilterInfo,
    Condition,
)
from lark_oapi.api.drive.v1 import (
    UploadAllMediaRequest,
    UploadAllMediaRequestBody,
)
from loguru import logger

from .models import Paper


class FeishuClient:
    """
    飞书多维表客户端

    field_mapping: SOP 字段名 → 飞书字段实际名称的映射
    例如 {'论文': '论文', '标题': '标题', 'arxiv': 'arxiv', ...}
    """

    # SOP 中定义的 10 个字段 key
    REQUIRED_FIELDS = [
        '论文', '标题', '机构', '日期', '作者',
        'arxiv', 'project', '概要', '简介', '图片'
    ]

    def __init__(
        self,
        app_id: str,
        app_secret: str,
        app_token: str,
        table_id: str,
        field_mapping: Dict[str, str],
        is_wiki: bool = False,
    ):
        self.client = (
            lark.Client.builder()
            .app_id(app_id)
            .app_secret(app_secret)
            .log_level(lark.LogLevel.WARNING)
            .build()
        )
        # 如果是 wiki 嵌套的多维表，先把 wiki node token 转换为真实的 app_token
        if is_wiki:
            resolved = self._resolve_wiki_token(app_token)
            logger.info(f"Wiki token 转换: {app_token} → {resolved}")
            self.app_token = resolved
        else:
            self.app_token = app_token
        self.table_id = table_id
        self.field_mapping = field_mapping

        missing = [k for k in self.REQUIRED_FIELDS if k not in field_mapping]
        if missing:
            raise ValueError(f"field_mapping 缺失字段: {missing}")

    def _resolve_wiki_token(self, wiki_node_token: str) -> str:
        """
        将 Wiki node token 转换为真实的 bitable app_token。

        调用飞书 API: GET /open-apis/wiki/v2/spaces/get_node?token={wiki_node_token}
        返回体里的 obj_token 就是我们要的 app_token。
        需要权限: wiki:wiki:readonly 或 wiki:wiki
        """
        import json as _json
        request = lark.BaseRequest.builder() \
            .http_method(lark.HttpMethod.GET) \
            .uri(f"/open-apis/wiki/v2/spaces/get_node?token={wiki_node_token}&obj_type=wiki") \
            .token_types({lark.AccessTokenType.TENANT}) \
            .build()
        response = self.client.request(request)
        if response.code != 0:
            raise RuntimeError(
                f"Wiki token 解析失败: code={response.code} msg={response.msg}. "
                f"请检查应用是否开通 wiki:wiki:readonly 权限且已发布版本。"
            )
        body = _json.loads(response.raw.content)
        obj_token = body.get('data', {}).get('node', {}).get('obj_token')
        if not obj_token:
            raise RuntimeError(f"Wiki 响应体中未找到 obj_token: {body}")
        return obj_token

    # ────────────────────────────────────────────────────
    # 查询去重
    # ────────────────────────────────────────────────────

    def exists(self, arxiv_url: str) -> bool:
        """按 arxiv 链接查询记录是否已存在"""
        field_name = self.field_mapping['arxiv']
        req = (
            SearchAppTableRecordRequest.builder()
            .app_token(self.app_token)
            .table_id(self.table_id)
            .request_body(
                SearchAppTableRecordRequestBody.builder()
                .filter(
                    FilterInfo.builder()
                    .conjunction("and")
                    .conditions([
                        Condition.builder()
                        .field_name(field_name)
                        .operator("contains")
                        .value([arxiv_url])
                        .build()
                    ])
                    .build()
                )
                .build()
            )
            .build()
        )
        resp = self.client.bitable.v1.app_table_record.search(req)
        if not resp.success():
            logger.error(f"飞书查询失败: code={resp.code} msg={resp.msg}")
            return False
        total = getattr(resp.data, 'total', 0) if resp.data else 0
        return total > 0

    # ────────────────────────────────────────────────────
    # 插入记录
    # ────────────────────────────────────────────────────

    def insert(self, paper: Paper, image_token: Optional[str] = None) -> None:
        """插入一条论文记录"""
        fields: Dict[str, Any] = {
            self.field_mapping['论文']: paper.title_zh,
            self.field_mapping['标题']: paper.title_en,
            self.field_mapping['机构']: paper.institutions,
            self.field_mapping['作者']: paper.authors,
            self.field_mapping['arxiv']: {
                'link': paper.arxiv_url,
                'text': paper.arxiv_url,
            },
            self.field_mapping['概要']: paper.core_points,
            self.field_mapping['简介']: paper.abstract_zh,
        }

        # 日期（飞书日期字段要求毫秒时间戳）
        if paper.date:
            dt = datetime.combine(paper.date, datetime.min.time())
            fields[self.field_mapping['日期']] = int(dt.timestamp() * 1000)

        # project URL（可选）
        if paper.project_url:
            fields[self.field_mapping['project']] = {
                'link': paper.project_url,
                'text': paper.project_url,
            }

        # 图片附件（可选）
        if image_token:
            fields[self.field_mapping['图片']] = [{'file_token': image_token}]

        req = (
            CreateAppTableRecordRequest.builder()
            .app_token(self.app_token)
            .table_id(self.table_id)
            .request_body(AppTableRecord.builder().fields(fields).build())
            .build()
        )
        resp = self.client.bitable.v1.app_table_record.create(req)
        if not resp.success():
            raise RuntimeError(
                f"飞书插入失败: code={resp.code} msg={resp.msg}"
            )

    # ────────────────────────────────────────────────────
    # 图片上传（SOP "复制粘贴" 的工程化实现：上传为附件）
    # ────────────────────────────────────────────────────

    async def upload_image_from_url(self, image_url: str) -> Optional[str]:
        """下载图片 → 上传到飞书云空间 → 返回 file_token"""
        try:
            # 关闭自动解压，避免 content-length 与实际字节数不一致
            async with httpx.AsyncClient(
                timeout=60,
                follow_redirects=True,
                headers={'Accept-Encoding': 'identity'},
            ) as hc:
                resp = await hc.get(image_url)
                resp.raise_for_status()
                image_bytes = resp.content

            actual_size = len(image_bytes)
            if actual_size == 0:
                logger.error(f"图片下载为空: {image_url}")
                return None

            # 通过 Content-Type / URL 后缀推断扩展名
            content_type = (resp.headers.get('content-type') or '').lower()
            ext = 'png'
            if 'jpeg' in content_type or 'jpg' in content_type:
                ext = 'jpg'
            elif 'png' in content_type:
                ext = 'png'
            elif 'webp' in content_type:
                ext = 'webp'
            else:
                low = image_url.lower()
                for candidate in ('.jpg', '.jpeg', '.png', '.webp', '.gif'):
                    if candidate in low:
                        ext = candidate.lstrip('.').replace('jpeg', 'jpg')
                        break
            file_name = f"paper_image.{ext}"

            # 飞书 SDK 要求 file 参数是 BytesIO，不是 bytes
            import io
            file_obj = io.BytesIO(image_bytes)

            req = (
                UploadAllMediaRequest.builder()
                .request_body(
                    UploadAllMediaRequestBody.builder()
                    .file_name(file_name)
                    .parent_type('bitable_image')
                    .parent_node(self.app_token)
                    .size(actual_size)
                    .file(file_obj)
                    .build()
                )
                .build()
            )
            up_resp = self.client.drive.v1.media.upload_all(req)
            if not up_resp.success():
                logger.error(
                    f"图片上传失败: code={up_resp.code} msg={up_resp.msg} "
                    f"size={actual_size} name={file_name}"
                )
                return None
            return up_resp.data.file_token
        except Exception as e:
            logger.error(f"图片处理失败 {image_url}: {e}")
            return None

    async def upload_image_bytes(self, image_bytes: bytes, ext: str = 'png') -> Optional[str]:
        """直接上传图片 bytes 到飞书云空间。用于 arxiv 兜底从 PDF 抽出来的图。"""
        try:
            actual_size = len(image_bytes)
            if actual_size == 0:
                return None
            import io
            file_obj = io.BytesIO(image_bytes)
            file_name = f"paper_image.{ext}"

            req = (
                UploadAllMediaRequest.builder()
                .request_body(
                    UploadAllMediaRequestBody.builder()
                    .file_name(file_name)
                    .parent_type('bitable_image')
                    .parent_node(self.app_token)
                    .size(actual_size)
                    .file(file_obj)
                    .build()
                )
                .build()
            )
            up_resp = self.client.drive.v1.media.upload_all(req)
            if not up_resp.success():
                logger.error(
                    f"图片(bytes)上传失败: code={up_resp.code} msg={up_resp.msg} "
                    f"size={actual_size} name={file_name}"
                )
                return None
            return up_resp.data.file_token
        except Exception as e:
            logger.error(f"图片(bytes)处理失败: {e}")
            return None

