"""飞书多维表格（业务工作台）REST 端点。

顶层 ``APIRouter`` 由 ``app/core/discover.py`` 自动挂载到 ``/feishu`` 前缀，
本文件无需（也不应）在 ``plugin.py`` 中重复 ``add_router``。

读写全部直接透传飞书，本地不落库。
"""

from __future__ import annotations

import asyncio
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, Path, Query, Request, UploadFile
from fastapi.responses import JSONResponse, Response
from redis.asyncio.client import Redis

from app.common.response import ResponseSchema, SuccessResponse
from app.core.auth.permission import AuthPermission
from app.core.auth.schema import AuthSchema
from app.core.dependencies import redis_getter
from app.core.logger import log
from app.core.router_class import OperationLogRoute

from .schema import (
    FieldNameSchema,
    MergeRequest,
    RecordCreateSchema,
    RecordUpdateSchema,
    UnmergeRequest,
    WorkbenchConfigSchema,
    WorkbenchPageSchema,
    WorkbenchQueryParam,
)
from .service import FeishuBitableService, _content_disposition


# 注意：容器前缀 ``/feishu`` 已由 discover 按 module_feishu 自动挂载，
# 此处不再声明 prefix，避免重复（否则变成 /feishu/feishu/...）。
async def _deny_feishu_write(request: Request) -> None:
    """Fast 系统只以本地 MySQL 为准，禁止任何飞书写回。"""
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        raise HTTPException(
            status_code=403,
            detail="Fast 系统已关闭飞书写回，业务数据只保存到本地数据库",
        )


FeishuRouter = APIRouter(
    route_class=OperationLogRoute,
    tags=["飞书多维表格"],
    dependencies=[Depends(_deny_feishu_write)],
)

_QUERY = ["module_feishu:record:query"]
_DETAIL = ["module_feishu:record:detail"]
_UPDATE = ["module_feishu:record:update"]


def _service(redis: Redis, role: str = "") -> FeishuBitableService:
    """构造服务实例。

    必须把 ``role`` 传下去：数据源（app_token/table_id）跟着工作台配置走，
    同一个模块服务着富士康与威达两个 base；不传则回落到全局 settings。
    """
    return FeishuBitableService(redis, role)


@FeishuRouter.get(
    "/workbench/{role}/config",
    summary="获取工作台配置",
    description="返回工作台的列表列、待办判定字段、详情分组（含字段元数据/选项），供前端动态渲染。",
    response_model=ResponseSchema[WorkbenchConfigSchema],
)
async def get_workbench_config_controller(
    role: Annotated[str, Path(description="工作台标识（见 workbench.py 的 WORKBENCHES）")],
    redis: Annotated[Redis, Depends(redis_getter)],
    auth: Annotated[AuthSchema, Depends(AuthPermission(_QUERY))],
) -> JSONResponse:
    """获取工作台配置。"""
    result = await _service(redis, role).get_workbench_config(role)
    log.info(f"获取工作台配置成功: {role}")
    return SuccessResponse(data=result, msg="获取工作台配置成功")


@FeishuRouter.get(
    "/workbench/{role}/records",
    summary="查询工作台记录列表",
    description="服务端过滤（待办/关键字）+ 字段投影 + 游标分页，数据实时来自飞书。",
    response_model=ResponseSchema[WorkbenchPageSchema],
)
async def get_workbench_records_controller(
    role: Annotated[str, Path(description="工作台标识（见 workbench.py 的 WORKBENCHES）")],
    query: Annotated[WorkbenchQueryParam, Depends()],
    redis: Annotated[Redis, Depends(redis_getter)],
    auth: Annotated[AuthSchema, Depends(AuthPermission(_QUERY))],
) -> JSONResponse:
    """查询工作台记录列表。"""
    result = await _service(redis, role).list_workbench(
        role=role,
        pending=query.pending,
        page_token=query.page_token,
        page_size=query.page_size,
        search=query.search,
        # 行级数据权限要有「我是谁」才能算。认证槽位已经把当前用户塞进 auth.user
        # （见 core/dependencies.py），这里只是往下传一层。
        current_user=auth.user,
    )
    log.info(f"查询工作台记录成功: {role} pending={query.pending} 返回 {len(result['items'])} 条")
    return SuccessResponse(data=result, msg="查询记录列表成功")


@FeishuRouter.get(
    "/workbench/{role}/stats",
    summary="获取工作台统计",
    description="返回待办数量与记录总数，供工作台顶部卡片展示。",
    response_model=ResponseSchema[dict],
)
async def get_workbench_stats_controller(
    role: Annotated[str, Path(description="工作台标识（见 workbench.py 的 WORKBENCHES）")],
    redis: Annotated[Redis, Depends(redis_getter)],
    auth: Annotated[AuthSchema, Depends(AuthPermission(_QUERY))],
) -> JSONResponse:
    """获取工作台统计。"""
    service = _service(redis, role)
    total, breakdown = await asyncio.gather(
        service.count_total(current_user=auth.user),
        service.count_todo_breakdown(role, current_user=auth.user),
    )
    result = {"total": total, "pending": breakdown}
    log.info(f"获取工作台统计成功: {role} total={total} 待办项={len(breakdown)}")
    return SuccessResponse(data=result, msg="获取工作台统计成功")


@FeishuRouter.get(
    "/workbench/{role}/records/{record_id}",
    summary="获取记录详情",
    description="获取单条记录（按工作台角色返回所需字段）；传 invoice_no 可走更快的数据路径。",
    response_model=ResponseSchema[dict],
)
async def get_workbench_record_controller(
    role: Annotated[str, Path(description="工作台标识（见 workbench.py 的 WORKBENCHES）")],
    record_id: Annotated[str, Path(description="记录ID")],
    redis: Annotated[Redis, Depends(redis_getter)],
    auth: Annotated[AuthSchema, Depends(AuthPermission(_DETAIL))],
    invoice_no: Annotated[str | None, Query(description="发票号（有则走 search，省 2~4 秒）")] = None,
) -> JSONResponse:
    """获取记录详情。"""
    result = await _service(redis, role).get_workbench_record(record_id, role, invoice_no, current_user=auth.user)
    log.info(f"获取记录详情成功 {record_id}")
    return SuccessResponse(data=result, msg="获取记录详情成功")


@FeishuRouter.put(
    "/workbench/{role}/records/{record_id}",
    summary="更新记录",
    description="更新记录（仅写传入字段）；sync_group 为真时订舱/计划类字段连带同步到同合并组。",
    response_model=ResponseSchema[dict],
)
async def update_workbench_record_controller(
    data: RecordUpdateSchema,
    role: Annotated[str, Path(description="工作台标识（见 workbench.py 的 WORKBENCHES）")],
    record_id: Annotated[str, Path(description="记录ID")],
    redis: Annotated[Redis, Depends(redis_getter)],
    auth: Annotated[AuthSchema, Depends(AuthPermission(_UPDATE))],
) -> JSONResponse:
    """更新记录。"""
    result = await _service(redis, role).update_workbench_record(
        record_id=record_id, fields=data.fields, role=role, sync_group=data.sync_group
    )
    log.info(f"更新记录成功 {record_id}（联动 {result['group_synced']} 条）")
    return SuccessResponse(data=result, msg="更新记录成功")


@FeishuRouter.get(
    "/workbench/{role}/create-form",
    summary="新建表单定义",
    description="读飞书表单视图的定义（字段、顺序、必填、标题、说明），供新建抽屉渲染；未配置表单视图时返回空。",
    response_model=ResponseSchema[list],
)
async def get_create_form_controller(
    role: Annotated[str, Path(description="工作台标识（见 workbench.py 的 WORKBENCHES）")],
    redis: Annotated[Redis, Depends(redis_getter)],
    auth: Annotated[AuthSchema, Depends(AuthPermission(_QUERY))],
) -> JSONResponse:
    """获取新建表单定义。"""
    form = await _service(redis, role).get_create_form(role)
    return SuccessResponse(data=form, msg="获取新建表单成功")


@FeishuRouter.get(
    "/workbench/{role}/link-options",
    summary="关联字段候选记录",
    description="按字段名列出它关联的那张表的记录（取主字段作为显示文本），供新建表单的关联字段挑选。",
    response_model=ResponseSchema[list],
)
async def list_link_options_controller(
    role: Annotated[str, Path(description="工作台标识（见 workbench.py 的 WORKBENCHES）")],
    redis: Annotated[Redis, Depends(redis_getter)],
    auth: Annotated[AuthSchema, Depends(AuthPermission(_QUERY))],
    field: Annotated[str, Query(description="关联字段名，如「客户」")],
    search: Annotated[str | None, Query(description="按主字段文本过滤（不区分大小写）")] = None,
    table_id: Annotated[str, Query(description="字段长在哪张表上（父表）；留空即本工作台的表")] = "",
    filter_field: Annotated[str, Query(description="候选要按哪一列筛（关联表里的字段名）；留空即不筛")] = "",
    filter_values: Annotated[list[str] | None, Query(description="上述列的可取值的**任一个**命中即可（多选字段）")] = None,
) -> JSONResponse:
    """列出关联字段的候选记录。"""
    options = await _service(redis, role).list_link_options(
        field, search=search, role=role, table_id=table_id, filter_field=filter_field, filter_values=filter_values
    )
    return SuccessResponse(data=options, msg="获取候选记录成功")


@FeishuRouter.get(
    "/workbench/{role}/linked-records",
    summary="关联记录的完整内容",
    description=(
        "按关联字段取出那几条记录（含目标表全部字段的值与字段定义），供详情页展开关联分区"
        "（如商机的「航线报价」）。与 link-options 的区别：那个只给主字段文本，用于挑选；"
        "这个给整条记录，用于展示。\n\n"
        "`field` 也可以传**反查页**的标识（工作台在 `RelatedTable` 里配的，如国内操作的"
        "`iFreight`）：那条边是反的，此时 `record_ids` 传本表记录的 id，返回的是从表里"
        "指回它的那些记录。"
    ),
    response_model=ResponseSchema[dict],
)
async def list_linked_records_controller(
    role: Annotated[str, Path(description="工作台标识（见 workbench.py 的 WORKBENCHES）")],
    redis: Annotated[Redis, Depends(redis_getter)],
    auth: Annotated[AuthSchema, Depends(AuthPermission(_QUERY))],
    field: Annotated[str, Query(description="关联字段名，如「航线报价」")],
    record_ids: Annotated[list[str], Query(description="要取的关联记录 ID（可重复传参）")],
    table_id: Annotated[str, Query(description="关联字段长在**哪张表**上（父表）；留空 = 本工作台的表")] = "",
) -> JSONResponse:
    """取关联记录。"""
    result = await _service(redis, role).linked_records(field, record_ids, role=role, table_id=table_id)
    return SuccessResponse(data=result, msg="获取关联记录成功")


@FeishuRouter.get(
    "/workbench/{role}/related-blocks",
    summary="一次取回全部关联页",
    description=(
        "详情抽屉里**所有**反查页的数据，`{页 key: {table_id, fields, records}}`。\n\n"
        "与多次调 `linked-records` 的区别：同一张从表上的多个反查页（国内操作的 "
        "`iFreight` / `复制提单编组` / `投保编组` 都指向 iFreight 表）在这里**只扫一次**，"
        "再按各页点名的列裁开下发。逐页调会各扫一遍全表，三个 Tab 就是三次一秒多的往返。"
    ),
    response_model=ResponseSchema[dict],
)
async def list_related_blocks_controller(
    role: Annotated[str, Path(description="工作台标识（见 workbench.py 的 WORKBENCHES）")],
    redis: Annotated[Redis, Depends(redis_getter)],
    auth: Annotated[AuthSchema, Depends(AuthPermission(_QUERY))],
    record_ids: Annotated[list[str], Query(description="本表记录 ID（反查页拿它去从表里找指回来的行）")],
) -> JSONResponse:
    """取全部关联页。"""
    result = await _service(redis, role).related_blocks_records(record_ids, role=role)
    return SuccessResponse(data=result, msg="获取关联页数据成功")


@FeishuRouter.put(
    "/workbench/{role}/linked-records/{record_id}",
    summary="更新关联记录",
    description=(
        "改关联表里的某一条（详情页展开的报价/方案卡片就地改）。"
        "与 PUT /records/{record_id} 的区别：那个写本工作台的表，这个写关联字段指向的表。"
    ),
    response_model=ResponseSchema[dict],
)
async def update_linked_record_controller(
    data: RecordUpdateSchema,
    role: Annotated[str, Path(description="工作台标识（见 workbench.py 的 WORKBENCHES）")],
    record_id: Annotated[str, Path(description="关联记录的 ID")],
    redis: Annotated[Redis, Depends(redis_getter)],
    auth: Annotated[AuthSchema, Depends(AuthPermission(_UPDATE))],
    field: Annotated[str, Query(description="关联字段名，如「航线报价」——它指向的表就是要写的那张")],
    table_id: Annotated[str, Query(description="关联字段长在**哪张表**上（父表）；留空 = 本工作台的表")] = "",
) -> JSONResponse:
    """更新关联表里的记录。"""
    result = await _service(redis, role).update_linked_record(
        field_name=field, record_id=record_id, fields=data.fields, role=role, table_id=table_id
    )
    log.info(f"更新关联记录成功 {record_id}（{field}）")
    return SuccessResponse(data=result, msg="更新记录成功")


@FeishuRouter.post(
    "/workbench/{role}/linked-records",
    summary="新建关联记录",
    description=(
        "在关联表里新建一条记录（详情页「新增航线报价」）。回指字段（如「关联商机」）"
        "由调用方放进 fields，服务端不替它猜——没有回指的新记录就是个没人认领的孤儿行。"
    ),
    response_model=ResponseSchema[dict],
)
async def create_linked_record_controller(
    data: RecordCreateSchema,
    role: Annotated[str, Path(description="工作台标识（见 workbench.py 的 WORKBENCHES）")],
    redis: Annotated[Redis, Depends(redis_getter)],
    auth: Annotated[AuthSchema, Depends(AuthPermission(_UPDATE))],
    field: Annotated[str, Query(description="关联字段名，如「航线报价」——它指向的表就是要写的那张")],
    table_id: Annotated[str, Query(description="关联字段长在**哪张表**上（父表）；留空 = 本工作台的表")] = "",
) -> JSONResponse:
    """在关联表里新建记录。"""
    result = await _service(redis, role).create_linked_record(
        field_name=field, fields=data.fields, role=role, table_id=table_id
    )
    log.info(f"新建关联记录成功 {result['record_id']}（{field}）")
    return SuccessResponse(data=result, msg="新建记录成功")


@FeishuRouter.delete(
    "/workbench/{role}/linked-records/{record_id}",
    summary="删除关联记录",
    description=(
        "删掉关联表里的某一条（对客方案费用明细的「删除」）。只删记录本身，双向关联的另一侧"
        "由飞书自己维护。**不可逆**——调用方必须先二次确认。"
    ),
    response_model=ResponseSchema[dict],
)
async def delete_linked_record_controller(
    role: Annotated[str, Path(description="工作台标识（见 workbench.py 的 WORKBENCHES）")],
    record_id: Annotated[str, Path(description="关联记录的 ID")],
    redis: Annotated[Redis, Depends(redis_getter)],
    auth: Annotated[AuthSchema, Depends(AuthPermission(_UPDATE))],
    field: Annotated[str, Query(description="关联字段名，如「费用明细」——它指向的表就是要删的那张")],
    table_id: Annotated[str, Query(description="关联字段长在**哪张表**上（父表）；留空 = 本工作台的表")] = "",
) -> JSONResponse:
    """删除关联表里的记录。"""
    result = await _service(redis, role).delete_linked_record(
        field_name=field, record_id=record_id, role=role, table_id=table_id
    )
    log.info(f"删除关联记录成功 {record_id}（{field}）")
    return SuccessResponse(data=result, msg="删除记录成功")


@FeishuRouter.get(
    "/workbench/{role}/user-options",
    summary="人员候选",
    description="列出飞书通讯录成员（open_id + 姓名），供新建表单的人员字段挑选。",
    response_model=ResponseSchema[list],
)
async def list_user_options_controller(
    role: Annotated[str, Path(description="工作台标识（见 workbench.py 的 WORKBENCHES）")],
    redis: Annotated[Redis, Depends(redis_getter)],
    auth: Annotated[AuthSchema, Depends(AuthPermission(_QUERY))],
    search: Annotated[str | None, Query(description="按姓名过滤（不区分大小写）")] = None,
) -> JSONResponse:
    """列出通讯录成员。"""
    options = await _service(redis, role).list_user_options(search=search, role=role)
    return SuccessResponse(data=options, msg="获取人员候选成功")


@FeishuRouter.post(
    "/workbench/{role}/records",
    summary="新建记录",
    description="在目标表新建一条记录（索引页「新建商机」等入口）。只读字段会被忽略，由飞书自己算。",
    response_model=ResponseSchema[dict],
)
async def create_workbench_record_controller(
    data: RecordCreateSchema,
    role: Annotated[str, Path(description="工作台标识（见 workbench.py 的 WORKBENCHES）")],
    redis: Annotated[Redis, Depends(redis_getter)],
    # 复用 update 权限而不是新开一个 code：新建与改记录是同一种「写业务数据」的权限，
    # 新 code 得同步补进菜单/角色数据，否则非超管用户会被静默挡在门外。
    auth: Annotated[AuthSchema, Depends(AuthPermission(_UPDATE))],
) -> JSONResponse:
    """新建记录。"""
    result = await _service(redis, role).create_workbench_record(fields=data.fields, role=role)
    log.info(f"新建记录成功 {result['record_id']}")
    return SuccessResponse(data=result, msg="新建记录成功")


@FeishuRouter.post(
    "/workbench/{role}/fields",
    summary="新建字段",
    description=(
        "在目标表新建一个文本字段，供索引页「空列」当载体。"
        "注意：这是**改飞书生产表结构**，字段建出后所有表协作者可见，删除需人工去飞书操作。"
    ),
    response_model=ResponseSchema[dict],
)
async def create_field_controller(
    data: FieldNameSchema,
    role: Annotated[str, Path(description="工作台标识（见 workbench.py 的 WORKBENCHES）")],
    redis: Annotated[Redis, Depends(redis_getter)],
    auth: Annotated[AuthSchema, Depends(AuthPermission(_UPDATE))],
) -> JSONResponse:
    """新建文本字段。"""
    result = await _service(redis, role).create_text_field(data.name)
    log.info(f"新建字段成功 {result['name']}")
    return SuccessResponse(data=result, msg="新建列成功")


@FeishuRouter.put(
    "/workbench/{role}/fields/{field_name}",
    summary="修改字段名",
    description="改飞书字段名（索引页「空列」改列名）；只改名字，不动字段里的数据。",
    response_model=ResponseSchema[dict],
)
async def rename_field_controller(
    data: FieldNameSchema,
    role: Annotated[str, Path(description="工作台标识（见 workbench.py 的 WORKBENCHES）")],
    field_name: Annotated[str, Path(description="原字段名")],
    redis: Annotated[Redis, Depends(redis_getter)],
    auth: Annotated[AuthSchema, Depends(AuthPermission(_UPDATE))],
) -> JSONResponse:
    """修改字段名。"""
    result = await _service(redis, role).rename_field(field_name, data.name)
    log.info(f"字段改名成功 {field_name} -> {result['name']}")
    return SuccessResponse(data=result, msg="列名已修改")


@FeishuRouter.get(
    "/records/file/download",
    summary="下载飞书附件",
    description="后端代理下载（带 tenant_access_token 转发）；附件 URL 直接访问会报 Missing access token。",
    response_class=Response,
)
async def download_file_controller(
    redis: Annotated[Redis, Depends(redis_getter)],
    auth: Annotated[AuthSchema, Depends(AuthPermission(_DETAIL))],
    file_token: Annotated[str, Query(description="飞书附件的 file_token")],
    name: Annotated[str | None, Query(description="文件名（前端已知时传入，优先于飞书返回）")] = None,
    role: Annotated[str, Query(description="工作台标识；决定用哪个 base，威达附件必须传")] = "",
    table_id: Annotated[str, Query(description="附件所挂在的表；关联表的附件必须传，默认工作台主表")] = "",
) -> Response:
    """下载飞书附件。

    注意这里用 ``response_class=Response`` 而非 ``ResponseSchema``：返回的是文件字节，
    不能套统一的 JSON 包装（套了浏览器拿到的就是一段 JSON 文本）。

    ``role`` / ``table_id`` 是后加的可选参数，因为**附件不一定挂在本工作台的表上**：
    商机自己的 49 个字段里没有任何附件字段，真实附件全在「对客方案」「航线报价」这类关联表里。
    不传 role 会回落全局 settings 的富士康 base，拿威达的 file_token 去下会得到飞书
    ``[500] 无响应体``；传了 role 但 table_id 还是主表则得到 ``[403] 无响应体``——两个都实测复现过。
    富士康那套页面不传这两个参数，行为与改动前完全一致。
    """
    content, filename, media_type = await _service(redis, role).download_media(file_token, table_id)
    final_name = name or filename or "附件"
    log.info(f"下载飞书附件成功: {final_name}（{len(content)} 字节）")
    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": _content_disposition(final_name)},
    )


@FeishuRouter.post(
    "/records/file/upload",
    summary="上传附件到飞书",
    description="上传附件并返回 file_token；前端把 {file_token, name} 写进附件字段即可保存。",
    response_model=ResponseSchema[dict],
)
async def upload_file_controller(
    file: Annotated[UploadFile, File(description="待上传的文件")],
    redis: Annotated[Redis, Depends(redis_getter)],
    auth: Annotated[AuthSchema, Depends(AuthPermission(_UPDATE))],
    role: Annotated[str, Query(description="工作台标识；决定文件挂到哪个 base，威达附件必须传")] = "",
) -> JSONResponse:
    """上传附件到飞书。

    ``role`` 与下载同理（见 ``download_file_controller``）：文件必须先挂在**目标 base** 上，
    拿到的 file_token 才写得进那张表的附件字段。不传则回落到富士康 base，写进威达记录会被飞书拒掉。
    """
    content = await file.read()
    result = await _service(redis, role).upload_media(
        filename=file.filename or "",
        content=content,
        content_type=file.content_type or "",
    )
    log.info(f"上传飞书附件成功: {result['name']}")
    return SuccessResponse(data=result, msg="上传成功")


@FeishuRouter.post(
    "/records/merge",
    summary="合并记录",
    description="把若干票并入同一合并组（写「合出主单」或「合出分单」关联字段）。",
    response_model=ResponseSchema[dict],
)
async def merge_records_controller(
    data: MergeRequest,
    redis: Annotated[Redis, Depends(redis_getter)],
    auth: Annotated[AuthSchema, Depends(AuthPermission(_UPDATE))],
) -> JSONResponse:
    """合并记录。"""
    result = await _service(redis).merge_records(data.record_ids, data.merge_type, data.target_no)
    log.info(f"合并记录成功: {data.merge_type} {len(data.record_ids)} 票")
    return SuccessResponse(data=result, msg="合并成功")


@FeishuRouter.post(
    "/records/unmerge",
    summary="拆出合并组",
    description="把若干票从合并组中拆出，组内剩余票自动重算。",
    response_model=ResponseSchema[dict],
)
async def unmerge_records_controller(
    data: UnmergeRequest,
    redis: Annotated[Redis, Depends(redis_getter)],
    auth: Annotated[AuthSchema, Depends(AuthPermission(_UPDATE))],
) -> JSONResponse:
    """拆出合并组。"""
    result = await _service(redis).unmerge_records(data.record_ids, data.merge_type)
    log.info(f"拆出合并组成功: {data.merge_type} {len(data.record_ids)} 票")
    return SuccessResponse(data=result, msg="已拆出合并组")
