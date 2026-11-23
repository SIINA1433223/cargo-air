"""威达工作台接口（本地优先版）。

在线请求只读本地 `biz_*` 表；飞书不参与业务读写。
"""

from __future__ import annotations

import html
from typing import Annotated, Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, HTMLResponse, Response
from openpyxl import load_workbook
from pydantic import BaseModel, Field
from sqlalchemy import text

from app.common.response import ResponseSchema, SuccessResponse
from app.core.auth.schema import AuthSchema
from app.core.database import async_db_session
from app.core.dependencies import get_current_user

from .agencymail import scan_agency_bills
from .billai import recognize_ifreight
from .billmail import scan_customer_bills
from .config import get_workbench
from .instructionmail import scan_instructions
from .invoicemail import scan_invoice_returns
from .overseasmail import scan_overseas_bills
from .service import (
    add_plan_fee,
    approve_pricing,
    automation_registry,
    bind_user,
    calc_ifreight_volume,
    cancel_handover,
    change_my_password,
    confirm_ai_fees,
    copy_bill_to_ifreight,
    create_approval,
    create_extrafee,
    create_feishu_approval,
    create_handover,
    create_order_ifreight,
    create_pricing_quote,
    create_record,
    create_staff,
    create_staff_account,
    dashboard_detail,
    decide_approval,
    delete_attachment,
    delete_extrafee,
    delete_option,
    delete_plan_fee,
    delete_record,
    generate_booking,
    generate_plan_for_opportunity,
    generate_quote,
    get_arrival_report,
    get_attachment,
    get_checks,
    get_config,
    get_dashboard,
    get_opportunity_orders,
    get_opportunity_plans,
    get_opportunity_pricing,
    get_planfee_options,
    get_read_state,
    get_record,
    get_related_records,
    get_staff_options,
    get_user_map,
    get_user_prefs,
    list_ai_fees,
    list_approvals,
    list_attachments,
    list_automation_logs,
    list_billlib_options,
    list_column_options,
    list_destination_ports,
    list_fleet,
    list_notifications,
    list_options_admin,
    list_order_extrafees,
    list_plan_fees,
    list_port_options,
    list_records,
    list_staff_admin,
    list_vendor_options,
    list_vendors_admin,
    mail_monitor_status,
    mark_flight_seen,
    mark_notifications_read,
    mark_read_state,
    reset_staff_password,
    save_attachment,
    save_check,
    save_destination_port,
    save_option,
    save_user_pref,
    save_vendor,
    send_action_mail,
    send_invoice_to_customer,
    set_bill_status,
    sync_approval_statuses,
    update_ai_fee,
    update_extrafee,
    update_fleet,
    update_order,
    update_order_goods,
    update_plan_fee,
    update_plan_record,
    update_pricing_quote,
    update_record,
    update_related_record,
    update_staff,
    update_todo,
)

WeidaRouter = APIRouter(
    prefix="/weida",
    tags=["威达工作台"],
    dependencies=[Depends(get_current_user)],
)


class CreateRecordBody(BaseModel):
    """新建记录。"""

    fields: dict[str, Any] = Field(default_factory=dict, description="字段值")


class UpdateRecordBody(BaseModel):
    """单条记录的部分更新。"""

    fields: dict[str, Any] = Field(default_factory=dict, description="要更新的字段")
    expected_update_time: str | None = Field(
        default=None, description="乐观锁：读取时的 update_time"
    )


def _require_workbench(role: str):
    wb = get_workbench(role)
    if wb is None:
        raise HTTPException(status_code=404, detail=f"未知工作台：{role}")
    return wb


@WeidaRouter.get(
    "/port-options",
    summary="目的港三字代码选项",
    response_model=ResponseSchema[list],
)
async def port_options(
    keyword: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=7000)] = 6000,
) -> Any:
    return SuccessResponse(data=await list_port_options(keyword, limit))


@WeidaRouter.get(
    "/options",
    summary="字段下拉候选",
    response_model=ResponseSchema[list],
)
async def column_options(
    table: Annotated[str, Query()],
    column: Annotated[str, Query()],
    keyword: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> Any:
    return SuccessResponse(
        data=await list_column_options(table, column, keyword=keyword, limit=limit)
    )


@WeidaRouter.get(
    "/vendors",
    summary="客商档案下拉",
    response_model=ResponseSchema[list],
)
async def vendors() -> Any:
    return SuccessResponse(data=await list_vendor_options())


@WeidaRouter.get(
    "/{role}/config",
    summary="工作台配置",
    response_model=ResponseSchema[dict],
)
async def workbench_config(role: str, auth: Annotated[AuthSchema, Depends(get_current_user)]) -> Any:
    wb = _require_workbench(role)
    data = await get_config(role, wb, viewer_user_id=auth.user.id)
    data["can_admin"] = bool(getattr(auth.user, "is_superuser", False))
    return SuccessResponse(data=data)


@WeidaRouter.get(
    "/{role}/records",
    summary="工作台记录（分页/筛选/排序）",
    response_model=ResponseSchema[dict],
)
async def workbench_records(
    role: str,
    request: Request,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=1000)] = 50,
    keyword: Annotated[str | None, Query(description="关键词")] = None,
    filters: Annotated[
        str | None, Query(description="JSON 数组：[{column,op,value}]")
    ] = None,
    conjunction: Annotated[str, Query(description="and / or")] = "and",
    sort_field: Annotated[str | None, Query()] = None,
    sort_desc: Annotated[bool | None, Query()] = None,
    offset: Annotated[int | None, Query(ge=0)] = None,
    tail: Annotated[bool, Query()] = False,
) -> Any:
    wb = _require_workbench(role)
    data = await list_records(
        wb,
        page=page,
        page_size=page_size,
        keyword=keyword,
        filters_raw=filters,
        conjunction="or" if conjunction == "or" else "and",
        sort_field=sort_field,
        sort_desc=sort_desc,
        viewer_user_id=request.scope.get("user_id"),
        viewer_username=request.scope.get("user_username"),
        offset=offset,
        tail=tail,
    )
    return SuccessResponse(data=data)


@WeidaRouter.get(
    "/{role}/records/{record_id}",
    summary="单条记录详情",
    response_model=ResponseSchema[dict],
)
async def workbench_record_detail(role: str, record_id: int) -> Any:
    wb = _require_workbench(role)
    data = await get_record(wb, record_id)
    if data is None:
        raise HTTPException(status_code=404, detail="记录不存在")
    return SuccessResponse(data=data)


@WeidaRouter.get(
    "/{role}/records/{record_id}/related/{key}",
    summary="记录的反查从表",
    response_model=ResponseSchema[dict],
)
async def workbench_related_records(role: str, record_id: int, key: str) -> Any:
    wb = _require_workbench(role)
    return SuccessResponse(data=await get_related_records(wb, record_id, key))


class RelatedUpdateBody(BaseModel):
    fields: dict[str, Any] = Field(default_factory=dict)


@WeidaRouter.patch(
    "/{role}/records/{record_id}/related/{key}/{row_id}",
    summary="修改反查从表记录",
    response_model=ResponseSchema[dict],
)
async def workbench_related_update(
    role: str, record_id: int, key: str, row_id: int, body: RelatedUpdateBody
) -> Any:
    wb = _require_workbench(role)
    return SuccessResponse(data=await update_related_record(wb, key, row_id, body.fields))


class BillStatusBody(BaseModel):
    step: int | None = None


@WeidaRouter.patch(
    "/domestic_ops/orders/{order_id}/bill-status",
    summary="手动设置账单状态",
    response_model=ResponseSchema[dict],
)
async def edit_bill_status(order_id: int, body: BillStatusBody) -> Any:
    return SuccessResponse(data=await set_bill_status(order_id, body.step), msg="账单状态已更新")


class MailTestBody(BaseModel):
    to: str
    profile: str = "default"
    subject: str = "威达系统邮件通道测试"
    body: str = "这是一封威达 Fast 系统邮件通道测试。"


@WeidaRouter.post(
    "/mail/scan-bills",
    summary="手动扫描客户账单确认邮件",
    response_model=ResponseSchema[dict],
)
async def scan_bills(auth: Annotated[AuthSchema, Depends(get_current_user)]) -> Any:
    _require_admin(auth)
    return SuccessResponse(data=await scan_customer_bills(), msg="扫描完成")


class PrefBody(BaseModel):
    role: str
    key: str
    value: Any


class ReadStateBody(BaseModel):
    role: str
    ids: list[int]


@WeidaRouter.get("/preferences", response_model=ResponseSchema[dict])
async def preferences(role: str, auth: Annotated[AuthSchema, Depends(get_current_user)]) -> Any:
    return SuccessResponse(data=await get_user_prefs(int(auth.user.id), role))


@WeidaRouter.put("/preferences", response_model=ResponseSchema[dict])
async def preferences_save(body: PrefBody, auth: Annotated[AuthSchema, Depends(get_current_user)]) -> Any:
    await save_user_pref(int(auth.user.id), body.role, body.key, body.value)
    return SuccessResponse(data={"ok": True})


@WeidaRouter.get("/read-state", response_model=ResponseSchema[dict])
async def read_state(role: str, auth: Annotated[AuthSchema, Depends(get_current_user)]) -> Any:
    return SuccessResponse(data={"ids": await get_read_state(int(auth.user.id), role)})


@WeidaRouter.post("/read-state", response_model=ResponseSchema[dict])
async def read_state_mark(body: ReadStateBody, auth: Annotated[AuthSchema, Depends(get_current_user)]) -> Any:
    await mark_read_state(int(auth.user.id), body.role, body.ids)
    return SuccessResponse(data={"ok": True})


@WeidaRouter.get(
    "/mail/status",
    summary="邮件监控健康状态",
    response_model=ResponseSchema[dict],
)
async def mail_status(auth: Annotated[AuthSchema, Depends(get_current_user)]) -> Any:
    _require_admin(auth)
    return SuccessResponse(data=await mail_monitor_status())


@WeidaRouter.post(
    "/mail/scan-instructions",
    summary="手动扫描托书邮件",
    response_model=ResponseSchema[dict],
)
async def scan_instruction_mail(auth: Annotated[AuthSchema, Depends(get_current_user)]) -> Any:
    _require_admin(auth)
    return SuccessResponse(data=await scan_instructions(), msg="扫描完成")


@WeidaRouter.post(
    "/mail/scan-agency",
    summary="手动扫描代理账单邮件",
    response_model=ResponseSchema[dict],
)
async def scan_agency(auth: Annotated[AuthSchema, Depends(get_current_user)]) -> Any:
    _require_admin(auth)
    return SuccessResponse(data=await scan_agency_bills(), msg="扫描完成")


@WeidaRouter.post(
    "/mail/scan-invoices",
    summary="手动扫描发票回传邮件",
    response_model=ResponseSchema[dict],
)
async def scan_invoices(auth: Annotated[AuthSchema, Depends(get_current_user)]) -> Any:
    _require_admin(auth)
    return SuccessResponse(data=await scan_invoice_returns(), msg="扫描完成")


@WeidaRouter.post(
    "/mail/scan-overseas",
    summary="手动扫描国外账单邮件",
    response_model=ResponseSchema[dict],
)
async def scan_overseas(auth: Annotated[AuthSchema, Depends(get_current_user)]) -> Any:
    _require_admin(auth)
    return SuccessResponse(data=await scan_overseas_bills(), msg="扫描完成")


@WeidaRouter.post(
    "/ifreight/{ifreight_id}/ai-recognize",
    summary="AI 识别账单费用行",
    response_model=ResponseSchema[dict],
)
async def ai_recognize(
    ifreight_id: int, kind: Annotated[str, Query()] = "foreign"
) -> Any:
    if kind not in {"foreign", "domestic", "agency"}:
        raise HTTPException(status_code=422, detail="kind 只能是 foreign / domestic / agency")
    return SuccessResponse(
        data=await recognize_ifreight(ifreight_id, kind=kind), msg="AI 识别完成"
    )


@WeidaRouter.post(
    "/mail/test",
    summary="发送测试邮件",
    response_model=ResponseSchema[dict],
)
async def mail_test(body: MailTestBody, auth: Annotated[AuthSchema, Depends(get_current_user)]) -> Any:
    _require_admin(auth)
    from .mailer import send_mail

    await run_in_threadpool(
        send_mail,
        body.to,
        body.subject,
        body.body,
        profile=body.profile,
    )
    return SuccessResponse(data={"to": body.to, "profile": body.profile}, msg="测试邮件已发送")


class ActionMailBody(BaseModel):
    kind: str


@WeidaRouter.post(
    "/domestic_ops/orders/{order_id}/send-invoice",
    summary="发送发票",
    response_model=ResponseSchema[dict],
)
async def send_invoice(order_id: int) -> Any:
    return SuccessResponse(data=await send_invoice_to_customer(order_id), msg="发票已发送")


@WeidaRouter.post(
    "/domestic_ops/orders/{order_id}/send-mail",
    summary="发送操作邮件",
    response_model=ResponseSchema[dict],
)
async def send_action_mail_route(order_id: int, body: ActionMailBody) -> Any:
    return SuccessResponse(data=await send_action_mail(order_id, body.kind), msg="邮件已发送")


class AiFeeBody(BaseModel):
    fee_category: str | None = None
    non_standard_fee_category: str | None = None
    quantity: float | None = None
    unit_price: float | None = None
    currency: str | None = None
    tax_rate: str | None = None
    tax_calc_type: str | None = None
    billing_unit: str | None = None
    separate_open: str | None = None
    fee_entry_status: str | None = None
    extra_fee_entry: bool | None = None


@WeidaRouter.patch(
    "/ai-fees/{fee_id}",
    summary="修改 AI 费用行",
    response_model=ResponseSchema[dict],
)
async def ai_fee_update(fee_id: int, body: AiFeeBody) -> Any:
    return SuccessResponse(
        data=await update_ai_fee(fee_id, body.model_dump(exclude_unset=True)), msg="费用行已更新"
    )


class ExtraFeeBody(BaseModel):
    domestic_abroad: str | None = None
    fee_category: str | None = None
    currency: str | None = None
    billing_unit: str | None = None
    app_quantity: float | None = None
    unit_price: float | None = None
    tax_point: str | None = None
    tax_calc_type: str | None = None
    fee_entry_status: str | None = None


@WeidaRouter.patch(
    "/extrafees/{fee_id}",
    summary="修改做账费用",
    response_model=ResponseSchema[dict],
)
async def extrafee_update(fee_id: int, body: ExtraFeeBody) -> Any:
    return SuccessResponse(data=await update_extrafee(fee_id, body.model_dump(exclude_unset=True)))


@WeidaRouter.delete(
    "/extrafees/{fee_id}",
    summary="删除做账费用",
    response_model=ResponseSchema[dict],
)
async def extrafee_delete(fee_id: int) -> Any:
    return SuccessResponse(data=await delete_extrafee(fee_id))


class ExtraFeeCreateBody(BaseModel):
    ifreight_id: int | None = None
    domestic_abroad: str | None = None
    fee_category: str | None = None
    currency: str | None = None
    billing_unit: str | None = None
    app_quantity: float | None = None
    unit_price: float | None = None
    fee_entry_status: str | None = None
    tax_point: str | None = None
    tax_calc_type: str | None = None


@WeidaRouter.post(
    "/domestic_ops/orders/{order_id}/extrafees",
    response_model=ResponseSchema[dict],
)
async def extrafee_create(order_id: int, body: ExtraFeeCreateBody) -> Any:
    return SuccessResponse(
        data=await create_extrafee(order_id, body.model_dump(exclude_unset=True)),
        msg="已添加费用",
    )


@WeidaRouter.get(
    "/domestic_ops/orders/{order_id}/extrafees",
    summary="订单做账费用",
    response_model=ResponseSchema[dict],
)
async def order_extrafees(order_id: int) -> Any:
    return SuccessResponse(data=await list_order_extrafees(order_id))


@WeidaRouter.post(
    "/domestic_ops/orders/{order_id}/ai-fees/confirm",
    summary="确认 AI 费用行",
    response_model=ResponseSchema[dict],
)
async def ai_fees_confirm(order_id: int) -> Any:
    return SuccessResponse(data=await confirm_ai_fees(order_id), msg="费用行已确认")


@WeidaRouter.get("/billlib/options", response_model=ResponseSchema[list])
async def billlib_options(
    keyword: str | None = None,
    limit: int = 200,
) -> Any:
    return SuccessResponse(data=await list_billlib_options(keyword, limit))


class CopyBillBody(BaseModel):
    billlib_id: int


@WeidaRouter.post("/ifreight/{ifreight_id}/copy-bl", response_model=ResponseSchema[dict])
async def ifreight_copy_bl(ifreight_id: int, body: CopyBillBody) -> Any:
    return SuccessResponse(data=await copy_bill_to_ifreight(ifreight_id, body.billlib_id))


@WeidaRouter.post("/ifreight/{ifreight_id}/calc-volume", response_model=ResponseSchema[dict])
async def ifreight_calc_volume(ifreight_id: int) -> Any:
    return SuccessResponse(data=await calc_ifreight_volume(ifreight_id))


class HandoverBody(BaseModel):
    to_name: str
    from_name: str | None = None


@WeidaRouter.post("/domestic_ops/orders/{order_id}/handover", response_model=ResponseSchema[dict])
async def ops_handover_create(order_id: int, body: HandoverBody) -> Any:
    return SuccessResponse(data=await create_handover(order_id, body.to_name, body.from_name), msg="已交接")


@WeidaRouter.delete("/domestic_ops/orders/{order_id}/handover", response_model=ResponseSchema[dict])
async def ops_handover_cancel(order_id: int) -> Any:
    await cancel_handover(order_id)
    return SuccessResponse(data={"ok": True}, msg="已撤回")


class CheckBody(BaseModel):
    kind: str = "weekend"
    user_name: str | None = None
    items: dict[str, Any] = Field(default_factory=dict)
    confirmed: bool = False


@WeidaRouter.get("/domestic_ops/orders/{order_id}/checks", response_model=ResponseSchema[dict])
async def ops_checks(order_id: int) -> Any:
    return SuccessResponse(data=await get_checks(order_id))


@WeidaRouter.post("/domestic_ops/orders/{order_id}/checks", response_model=ResponseSchema[dict])
async def ops_check_save(
    order_id: int,
    body: CheckBody,
    auth: Annotated[AuthSchema, Depends(get_current_user)],
) -> Any:
    user = auth.user
    name = body.user_name or str(getattr(user, "name", "") or getattr(user, "username", ""))
    return SuccessResponse(
        data=await save_check(order_id, body.kind, name, body.items, body.confirmed),
        msg="已保存",
    )


@WeidaRouter.post("/domestic_ops/orders/{order_id}/flight-seen", response_model=ResponseSchema[dict])
async def ops_flight_seen(order_id: int) -> Any:
    await mark_flight_seen(order_id)
    return SuccessResponse(data={"ok": True})


@WeidaRouter.post("/approvals/sync", response_model=ResponseSchema[dict])
async def approvals_sync() -> Any:
    return SuccessResponse(data={"updated": await sync_approval_statuses()})


@WeidaRouter.get("/fleet/{kind}", response_model=ResponseSchema[dict])
async def fleet_list(
    kind: str,
    auth: Annotated[AuthSchema, Depends(get_current_user)],
    keyword: str | None = None,
    page: int = 1,
    page_size: int = 100,
) -> Any:
    return SuccessResponse(data=await list_fleet(kind, keyword, page, page_size))


class FleetUpdateBody(BaseModel):
    fields: dict[str, Any] = Field(default_factory=dict)


@WeidaRouter.patch("/fleet/{kind}/{record_id}", response_model=ResponseSchema[dict])
async def fleet_update(
    kind: str,
    record_id: int,
    body: FleetUpdateBody,
    auth: Annotated[AuthSchema, Depends(get_current_user)],
) -> Any:
    return SuccessResponse(data=await update_fleet(kind, record_id, body.fields), msg="已保存")


@WeidaRouter.get("/automation/rules", response_model=ResponseSchema[dict])
async def automation_rules(
    auth: Annotated[AuthSchema, Depends(get_current_user)],
) -> Any:
    return SuccessResponse(data={"items": automation_registry()})


@WeidaRouter.get("/notifications", response_model=ResponseSchema[dict])
async def notifications(
    auth: Annotated[AuthSchema, Depends(get_current_user)],
    role: str = "customer_service",
    limit: int = 30,
) -> Any:
    user = auth.user
    return SuccessResponse(
        data=await list_notifications(int(getattr(user, "id", 0)) or None, role, limit)
    )


@WeidaRouter.post("/notifications/read", response_model=ResponseSchema[dict])
async def notifications_read(
    auth: Annotated[AuthSchema, Depends(get_current_user)],
    role: str = "customer_service",
) -> Any:
    user = auth.user
    await mark_notifications_read(int(getattr(user, "id", 0)) or None, role)
    return SuccessResponse(data={"ok": True})


@WeidaRouter.get("/automation/logs", response_model=ResponseSchema[dict])
async def automation_logs(
    auth: Annotated[AuthSchema, Depends(get_current_user)],
    limit: int = 50,
    table: str | None = None,
) -> Any:
    return SuccessResponse(data=await list_automation_logs(limit, table))


class ApprovalCreateBody(BaseModel):
    biz_type: str = "opportunity"
    biz_id: int
    snapshot: dict[str, Any] = Field(default_factory=dict)


class ApprovalDecideBody(BaseModel):
    approve: bool
    comment: str | None = None


@WeidaRouter.post(
    "/customer_service/opportunities/{opportunity_id}/feishu-approval",
    response_model=ResponseSchema[dict],
)
async def opportunity_feishu_approval(
    opportunity_id: int,
    auth: Annotated[AuthSchema, Depends(get_current_user)],
) -> Any:
    return SuccessResponse(data=await create_feishu_approval(opportunity_id), msg="已发起飞书审批")


@WeidaRouter.post("/approvals", response_model=ResponseSchema[dict])
async def approval_create(
    body: ApprovalCreateBody, auth: Annotated[AuthSchema, Depends(get_current_user)]
) -> Any:
    user = auth.user
    data = await create_approval(
        body.biz_type,
        body.biz_id,
        body.snapshot,
        int(getattr(user, "id", 0)) or None,
        str(getattr(user, "name", "") or getattr(user, "username", "") or ""),
    )
    return SuccessResponse(data=data, msg="已发起审批")


@WeidaRouter.get("/approvals", response_model=ResponseSchema[dict])
async def approvals(
    biz_type: str, biz_id: int, auth: Annotated[AuthSchema, Depends(get_current_user)]
) -> Any:
    return SuccessResponse(data=await list_approvals(biz_type, biz_id))


@WeidaRouter.post("/approvals/{approval_id}/decide", response_model=ResponseSchema[dict])
async def approval_decide(
    approval_id: int,
    body: ApprovalDecideBody,
    auth: Annotated[AuthSchema, Depends(get_current_user)],
) -> Any:
    _require_admin(auth)
    user = auth.user
    data = await decide_approval(
        approval_id,
        body.approve,
        int(getattr(user, "id", 0)) or None,
        str(getattr(user, "name", "") or getattr(user, "username", "") or ""),
        body.comment,
    )
    return SuccessResponse(data=data, msg="审批已处理")


@WeidaRouter.get(
    "/domestic_ops/orders/{order_id}/ai-fees",
    summary="AI 识别费用行",
    response_model=ResponseSchema[dict],
)
async def ai_fees(order_id: int) -> Any:
    return SuccessResponse(data=await list_ai_fees(order_id))


@WeidaRouter.get(
    "/domestic_ops/orders/{order_id}/arrival-report",
    summary="生成到货预报表",
)
async def arrival_report(order_id: int) -> Any:
    data, filename = await get_arrival_report(order_id)
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
    )


@WeidaRouter.get(
    "/domestic_ops/orders/{order_id}/doc",
    summary="生成单据（主单/分单/舱单/booking/到货预报表）",
)
async def generate_doc(
    order_id: int,
    kind: Annotated[str, Query()] = "mawb",
) -> Any:
    from .docgen import render_documents

    kinds = [k.strip() for k in kind.split(",") if k.strip()]
    try:
        data, filename, mime = await render_documents(order_id, kinds)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return Response(
        content=data,
        media_type=mime,
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
    )


@WeidaRouter.post(
    "/domestic_ops/orders/{order_id}/ifreight",
    summary="新增分单",
    response_model=ResponseSchema[dict],
)
async def create_ifreight(order_id: int) -> Any:
    return SuccessResponse(data=await create_order_ifreight(order_id), msg="已新增分单")


@WeidaRouter.post(
    "/{role}/records",
    summary="新建记录",
    response_model=ResponseSchema[dict],
)
async def workbench_record_create(
    role: str, body: CreateRecordBody, request: Request
) -> Any:
    wb = _require_workbench(role)
    data = await create_record(
        wb, body.fields, actor=request.scope.get("user_id")
    )
    return SuccessResponse(data=data, msg="已创建")


@WeidaRouter.delete(
    "/{role}/records/{record_id}",
    summary="删除记录",
    response_model=ResponseSchema[dict],
)
async def workbench_record_delete(
    role: str, record_id: int, request: Request
) -> Any:
    wb = _require_workbench(role)
    ok = await delete_record(wb, record_id, actor=request.scope.get("user_id"))
    if not ok:
        raise HTTPException(status_code=404, detail="记录不存在")
    return SuccessResponse(data={"id": record_id}, msg="已删除")


@WeidaRouter.patch(
    "/{role}/records/{record_id}",
    summary="更新单条记录",
    response_model=ResponseSchema[dict],
)
async def workbench_record_update(
    role: str, record_id: int, body: UpdateRecordBody, request: Request
) -> Any:
    wb = _require_workbench(role)
    data = await update_record(
        wb,
        record_id,
        body.fields,
        expected_update_time=body.expected_update_time,
        actor=request.scope.get("user_id"),
    )
    if data is None:
        raise HTTPException(status_code=404, detail="记录不存在")
    return SuccessResponse(data=data, msg="已保存")


@WeidaRouter.get(
    "/{role}/list",
    summary="工作台列表（兼容旧前端）",
    response_model=ResponseSchema[dict],
)
async def workbench_list(
    role: str,
    request: Request,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=500)] = 50,
) -> Any:
    wb = _require_workbench(role)
    data = await list_records(
        wb,
        page=page,
        page_size=page_size,
        viewer_user_id=request.scope.get("user_id"),
        viewer_username=request.scope.get("user_username"),
    )
    return SuccessResponse(data=data)


class GenerateFromOpportunityBody(BaseModel):
    """客服从某条航线报价生成对客方案。"""

    pricing_id: int = Field(description="biz_pricing.id")
    force: bool = Field(default=False, description="已生成过时是否再生成一条")


class PlanFeeBody(BaseModel):
    """对客方案费用明细一行。"""

    fields: dict[str, Any] = Field(default_factory=dict, description="费用字段")


class TodoBody(BaseModel):
    """操作待办勾选。"""

    field: str = Field(description="待办字段")
    value: bool = Field(description="是否完成")


@WeidaRouter.patch(
    "/{role}/records/{record_id}/todo",
    summary="勾选操作待办",
    response_model=ResponseSchema[dict],
)
async def workbench_todo(
    role: str,
    record_id: int,
    body: TodoBody,
    request: Request,
) -> Any:
    wb = _require_workbench(role)
    data = await update_todo(
        wb,
        record_id,
        body.field,
        body.value,
        actor=request.scope.get("user_id"),
    )
    if not data:
        raise HTTPException(status_code=404, detail="记录不存在")
    return SuccessResponse(data=data, msg="已保存")


class BindUserBody(BaseModel):
    """绑定本地账号；None 表示解绑。"""

    local_user_id: int | None = Field(default=None, description="sys_user.id")


class StaffBody(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    role: str = Field(..., min_length=1, max_length=32)
    roles: list[str] = Field(default_factory=list)


class StaffUpdateBody(BaseModel):
    name: str | None = None
    role: str | None = None
    roles: list[str] | None = None
    active: bool | int | None = None
    mobile: str | None = None
    email: str | None = None


class StaffAccountBody(BaseModel):
    username: str = Field(..., min_length=3, max_length=64)
    password: str = Field(..., min_length=6, max_length=64)


class StaffPasswordBody(BaseModel):
    password: str = Field(..., min_length=6, max_length=64)


class MasterItemBody(BaseModel):
    fields: dict[str, Any] = Field(default_factory=dict)


def _require_admin(auth: AuthSchema) -> None:
    if not getattr(auth.user, "is_superuser", False):
        raise HTTPException(status_code=403, detail="仅限超级管理员")


@WeidaRouter.get(
    "/customer_service/{opportunity_id}/pricing",
    summary="商机关联的航线报价",
    response_model=ResponseSchema[dict],
)
async def opportunity_pricing(opportunity_id: int) -> Any:
    return SuccessResponse(data=await get_opportunity_pricing(opportunity_id))


@WeidaRouter.get(
    "/customer_service/{opportunity_id}/orders",
    summary="商机关联的订仓单",
    response_model=ResponseSchema[dict],
)
async def opportunity_orders(opportunity_id: int) -> Any:
    return SuccessResponse(data=await get_opportunity_orders(opportunity_id))


class PricingQuoteBody(BaseModel):
    fields: dict[str, Any] = Field(default_factory=dict)


@WeidaRouter.post(
    "/pricing/{opportunity_id}/quotes",
    summary="新增航线报价",
    response_model=ResponseSchema[dict],
)
async def add_pricing_quote(opportunity_id: int, body: PricingQuoteBody) -> Any:
    return SuccessResponse(
        data=await create_pricing_quote(opportunity_id, body.fields), msg="已新增航线报价"
    )


@WeidaRouter.patch(
    "/pricing/quotes/{quote_id}",
    summary="修改航线报价",
    response_model=ResponseSchema[dict],
)
async def edit_pricing_quote(quote_id: int, body: PricingQuoteBody) -> Any:
    return SuccessResponse(
        data=await update_pricing_quote(quote_id, body.fields), msg="已保存"
    )


@WeidaRouter.post(
    "/pricing/{opportunity_id}/approve",
    summary="主管核价",
    response_model=ResponseSchema[dict],
)
async def approve_opportunity_pricing(opportunity_id: int) -> Any:
    return SuccessResponse(data=await approve_pricing(opportunity_id), msg="已核价")


class OrderBody(BaseModel):
    fields: dict[str, Any] = Field(default_factory=dict, description="订单字段")


@WeidaRouter.patch(
    "/customer_service/orders/{order_id}",
    summary="修改订仓单",
    response_model=ResponseSchema[dict],
)
async def edit_order(order_id: int, body: OrderBody) -> Any:
    return SuccessResponse(data=await update_order(order_id, body.fields))


class OrderGoodsBody(BaseModel):
    piece_count: float | None = None
    gross_weight_kg: float | None = None
    volume_m3: float | None = None
    size: str | None = None


@WeidaRouter.patch(
    "/customer_service/orders/{order_id}/goods",
    summary="修改订仓单货物数据",
    response_model=ResponseSchema[dict],
)
async def edit_order_goods(order_id: int, body: OrderGoodsBody) -> Any:
    return SuccessResponse(
        data=await update_order_goods(order_id, body.model_dump()),
        msg="货物数据已保存",
    )


@WeidaRouter.post(
    "/customer_service/orders/{order_id}/generate-booking",
    summary="生成订舱单",
    response_model=ResponseSchema[dict],
)
async def create_order_booking(order_id: int, request: Request) -> Any:
    return SuccessResponse(
        data=await generate_booking(order_id, actor=request.scope.get("user_id")),
        msg="订舱单已生成",
    )


@WeidaRouter.get(
    "/customer_service/orders/{order_id}/attachments",
    summary="订舱单附件",
    response_model=ResponseSchema[dict],
)
async def order_attachments(
    order_id: int,
    field: Annotated[str | None, Query()] = "订舱单",
) -> Any:
    return SuccessResponse(data=await list_attachments("order", order_id, field))


@WeidaRouter.post(
    "/customer_service/orders/{order_id}/attachments",
    summary="上传订舱单附件",
    response_model=ResponseSchema[dict],
)
async def upload_order_attachment(
    order_id: int,
    file: Annotated[UploadFile, File()],
    field: Annotated[str, Form()] = "订舱单",
) -> Any:
    content = await file.read()
    if len(content) > 20 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="附件不能超过 20MB")
    data = await save_attachment(
        "order", order_id, field, file.filename or "附件", content, file.content_type
    )
    return SuccessResponse(data=data)


@WeidaRouter.get(
    "/customer_service/{opportunity_id}/plans",
    summary="商机关联的对客方案",
    response_model=ResponseSchema[dict],
)
async def opportunity_plans(opportunity_id: int) -> Any:
    return SuccessResponse(data=await get_opportunity_plans(opportunity_id))


@WeidaRouter.get(
    "/customer_service/planfee-options",
    summary="费用明细下拉选项",
    response_model=ResponseSchema[dict],
)
async def planfee_options() -> Any:
    return SuccessResponse(data=await get_planfee_options())


@WeidaRouter.get(
    "/customer_service/plans/{plan_id}/attachments",
    summary="对客方案附件",
    response_model=ResponseSchema[dict],
)
async def plan_attachments(
    plan_id: int,
    field: Annotated[str | None, Query()] = "报价单",
) -> Any:
    return SuccessResponse(data=await list_attachments("plan", plan_id, field))


class PlanUpdateBody(BaseModel):
    fields: dict[str, Any]


@WeidaRouter.patch("/plans/{plan_id}", summary="修改对客方案", response_model=ResponseSchema[dict])
async def plan_update(plan_id: int, body: PlanUpdateBody) -> Any:
    return SuccessResponse(data=await update_plan_record(plan_id, body.fields), msg="已保存")


@WeidaRouter.post(
    "/customer_service/plans/{plan_id}/attachments",
    summary="上传对客方案附件",
    response_model=ResponseSchema[dict],
)
async def upload_plan_attachment(
    plan_id: int,
    file: Annotated[UploadFile, File()],
    field: Annotated[str, Form()] = "报价单",
) -> Any:
    content = await file.read()
    if len(content) > 20 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="附件不能超过 20MB")
    data = await save_attachment(
        "plan", plan_id, field, file.filename or "附件", content, file.content_type
    )
    return SuccessResponse(data=data)


@WeidaRouter.get("/attachments/{attachment_id}/download", summary="下载附件")
async def download_attachment(attachment_id: int) -> Any:
    meta, path = await get_attachment(attachment_id)
    return FileResponse(path, media_type=meta["mime"] or None, filename=meta["file_name"])


@WeidaRouter.get("/attachments/{attachment_id}/preview", summary="在线预览附件")
async def preview_attachment(attachment_id: int) -> Any:
    meta, path = await get_attachment(attachment_id)
    mime = meta["mime"] or ""
    if path.suffix.lower() in {".xlsx", ".xlsm"}:
        wb = load_workbook(path, data_only=True)
        ws = wb.active
        rows = []
        for row in ws.iter_rows():
            cells = []
            for cell in row:
                value = "" if cell.value is None else str(cell.value)
                cells.append(f"<td>{html.escape(value)}</td>")
            rows.append("<tr>" + "".join(cells) + "</tr>")
        body = (
            "<html><head><meta charset='utf-8'><style>"
            "body{font-family:Arial,sans-serif;padding:16px;background:#fff;}"
            "table{border-collapse:collapse;font-size:12px;}"
            "td{border:1px solid #ddd;padding:4px 6px;white-space:pre-wrap;}"
            "</style></head><body><table>"
            + "".join(rows)
            + "</table></body></html>"
        )
        return HTMLResponse(body)
    return FileResponse(path, media_type=mime or None, content_disposition_type="inline")


@WeidaRouter.delete(
    "/attachments/{attachment_id}",
    summary="删除附件",
    response_model=ResponseSchema[None],
)
async def remove_attachment(attachment_id: int) -> Any:
    await delete_attachment(attachment_id)
    return SuccessResponse(data=None)


@WeidaRouter.get(
    "/customer_service/plans/{plan_id}/fees",
    summary="对客方案费用明细",
    response_model=ResponseSchema[dict],
)
async def plan_fees(plan_id: int) -> Any:
    return SuccessResponse(data=await list_plan_fees(plan_id))


@WeidaRouter.post(
    "/customer_service/plans/{plan_id}/fees",
    summary="新增对客方案费用明细",
    response_model=ResponseSchema[dict],
)
async def create_plan_fee(plan_id: int, body: PlanFeeBody) -> Any:
    return SuccessResponse(data=await add_plan_fee(plan_id, body.fields))


@WeidaRouter.patch(
    "/customer_service/plans/{plan_id}/fees/{fee_id}",
    summary="修改费用明细",
    response_model=ResponseSchema[dict],
)
async def edit_plan_fee(plan_id: int, fee_id: int, body: PlanFeeBody) -> Any:
    return SuccessResponse(data=await update_plan_fee(plan_id, fee_id, body.fields))


@WeidaRouter.delete(
    "/customer_service/plans/{plan_id}/fees/{fee_id}",
    summary="删除费用明细",
    response_model=ResponseSchema[None],
)
async def remove_plan_fee(plan_id: int, fee_id: int) -> Any:
    await delete_plan_fee(plan_id, fee_id)
    return SuccessResponse(data=None)


@WeidaRouter.post(
    "/customer_service/plans/{plan_id}/generate-quote",
    summary="生成报价单",
    response_model=ResponseSchema[dict],
)
async def create_plan_quote(plan_id: int, request: Request) -> Any:
    return SuccessResponse(
        data=await generate_quote(plan_id, actor=request.scope.get("user_id"))
    )


@WeidaRouter.post(
    "/customer_service/{opportunity_id}/generate-plan",
    summary="客服生成对客方案",
    response_model=ResponseSchema[dict],
)
async def opportunity_generate_plan(
    opportunity_id: int,
    body: GenerateFromOpportunityBody,
    request: Request,
) -> Any:
    data = await generate_plan_for_opportunity(
        opportunity_id,
        body.pricing_id,
        actor=request.scope.get("user_id"),
        force=body.force,
    )
    return SuccessResponse(data=data, msg="已生成" if data["created"] else "已存在")


@WeidaRouter.get(
    "/master/vendors",
    summary="客商档案列表",
    response_model=ResponseSchema[dict],
)
async def master_vendors(
    auth: Annotated[AuthSchema, Depends(get_current_user)],
    keyword: Annotated[str | None, Query()] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=1000)] = 50,
) -> Any:
    _require_admin(auth)
    return SuccessResponse(data=await list_vendors_admin(keyword, page, page_size))


@WeidaRouter.post(
    "/master/vendors",
    summary="新增客商",
    response_model=ResponseSchema[dict],
)
async def master_vendor_create(
    body: MasterItemBody,
    auth: Annotated[AuthSchema, Depends(get_current_user)],
) -> Any:
    _require_admin(auth)
    return SuccessResponse(data=await save_vendor(None, body.fields), msg="已新增")


@WeidaRouter.patch(
    "/master/vendors/{vendor_id}",
    summary="修改客商",
    response_model=ResponseSchema[dict],
)
async def master_vendor_update(
    vendor_id: int,
    body: MasterItemBody,
    auth: Annotated[AuthSchema, Depends(get_current_user)],
) -> Any:
    _require_admin(auth)
    return SuccessResponse(data=await save_vendor(vendor_id, body.fields), msg="已保存")


@WeidaRouter.get(
    "/master/ports",
    summary="目的港列表",
    response_model=ResponseSchema[dict],
)
async def master_ports(
    auth: Annotated[AuthSchema, Depends(get_current_user)],
    keyword: Annotated[str | None, Query()] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=500)] = 50,
) -> Any:
    _require_admin(auth)
    return SuccessResponse(data=await list_destination_ports(keyword, page, page_size))


@WeidaRouter.patch(
    "/master/ports/{port_id}",
    summary="修改目的港",
    response_model=ResponseSchema[dict],
)
async def master_port_update(
    port_id: int,
    body: MasterItemBody,
    auth: Annotated[AuthSchema, Depends(get_current_user)],
) -> Any:
    _require_admin(auth)
    return SuccessResponse(data=await save_destination_port(port_id, body.fields), msg="已保存")


@WeidaRouter.get(
    "/master/options",
    summary="选项字典",
    response_model=ResponseSchema[dict],
)
async def master_options(
    auth: Annotated[AuthSchema, Depends(get_current_user)],
    category: Annotated[str | None, Query()] = None,
) -> Any:
    _require_admin(auth)
    return SuccessResponse(data=await list_options_admin(category))


@WeidaRouter.post(
    "/master/options",
    summary="新增选项",
    response_model=ResponseSchema[None],
)
async def master_option_create(
    body: MasterItemBody,
    auth: Annotated[AuthSchema, Depends(get_current_user)],
) -> Any:
    _require_admin(auth)
    await save_option(None, body.fields)
    return SuccessResponse(msg="已新增")


@WeidaRouter.patch(
    "/master/options/{option_id}",
    summary="修改选项",
    response_model=ResponseSchema[None],
)
async def master_option_update(
    option_id: int,
    body: MasterItemBody,
    auth: Annotated[AuthSchema, Depends(get_current_user)],
) -> Any:
    _require_admin(auth)
    await save_option(option_id, body.fields)
    return SuccessResponse(msg="已保存")


@WeidaRouter.delete(
    "/master/options/{option_id}",
    summary="删除选项",
    response_model=ResponseSchema[None],
)
async def master_option_delete(
    option_id: int,
    auth: Annotated[AuthSchema, Depends(get_current_user)],
) -> Any:
    _require_admin(auth)
    await delete_option(option_id)
    return SuccessResponse(msg="已删除")


@WeidaRouter.get(
    "/admin/staff",
    summary="人员与账号列表",
    response_model=ResponseSchema[dict],
)
async def staff_list(auth: Annotated[AuthSchema, Depends(get_current_user)]) -> Any:
    _require_admin(auth)
    return SuccessResponse(data=await list_staff_admin())


@WeidaRouter.post(
    "/admin/staff",
    summary="新增人员",
    response_model=ResponseSchema[dict],
)
async def staff_create(
    body: StaffBody,
    auth: Annotated[AuthSchema, Depends(get_current_user)],
) -> Any:
    _require_admin(auth)
    return SuccessResponse(
        data=await create_staff(body.name, body.role, body.roles), msg="已新增"
    )


@WeidaRouter.patch(
    "/admin/staff/{map_id}",
    summary="修改人员信息",
    response_model=ResponseSchema[None],
)
async def staff_update(
    map_id: int,
    body: StaffUpdateBody,
    auth: Annotated[AuthSchema, Depends(get_current_user)],
) -> Any:
    _require_admin(auth)
    await update_staff(map_id, body.model_dump(exclude_unset=True))
    return SuccessResponse(msg="已保存")


class MyPasswordBody(BaseModel):
    old_password: str
    new_password: str = Field(..., min_length=6, max_length=64)


@WeidaRouter.post("/me/password", summary="修改本人密码", response_model=ResponseSchema[None])
async def me_change_password(
    body: MyPasswordBody,
    auth: Annotated[AuthSchema, Depends(get_current_user)],
) -> Any:
    await change_my_password(int(auth.user.id), body.old_password, body.new_password)
    return SuccessResponse(msg="密码已修改")


@WeidaRouter.post(
    "/admin/staff/{map_id}/account",
    summary="开通账号",
    response_model=ResponseSchema[None],
)
async def staff_account(
    map_id: int,
    body: StaffAccountBody,
    auth: Annotated[AuthSchema, Depends(get_current_user)],
) -> Any:
    _require_admin(auth)
    await create_staff_account(map_id, body.username, body.password)
    return SuccessResponse(msg="账号已开通")


@WeidaRouter.post(
    "/admin/staff/{map_id}/password",
    summary="重置密码",
    response_model=ResponseSchema[None],
)
async def staff_password(
    map_id: int,
    body: StaffPasswordBody,
    auth: Annotated[AuthSchema, Depends(get_current_user)],
) -> Any:
    _require_admin(auth)
    await reset_staff_password(map_id, body.password)
    return SuccessResponse(msg="密码已重置")


@WeidaRouter.get(
    "/staff-options",
    summary="人员下拉",
    response_model=ResponseSchema[list],
)
async def staff_options(auth: Annotated[AuthSchema, Depends(get_current_user)]) -> Any:
    return SuccessResponse(data=await get_staff_options())


@WeidaRouter.get(
    "/admin/user-map",
    summary="人员映射列表",
    response_model=ResponseSchema[dict],
)
async def user_map_list(auth: Annotated[AuthSchema, Depends(get_current_user)]) -> Any:
    _require_admin(auth)
    return SuccessResponse(data=await get_user_map())


@WeidaRouter.patch(
    "/admin/user-map/{map_id}",
    summary="绑定本地账号",
    response_model=ResponseSchema[None],
)
async def user_map_bind(
    map_id: int,
    body: BindUserBody,
    auth: Annotated[AuthSchema, Depends(get_current_user)],
) -> Any:
    _require_admin(auth)
    await bind_user(map_id, body.local_user_id)
    return SuccessResponse(msg="已保存")


@WeidaRouter.get("/{role}/dashboard-detail", response_model=ResponseSchema[dict])
async def workbench_dashboard_detail(
    role: str,
    dimension: str,
    name: str,
    period: str = "week",
    from_date: str | None = None,
    to_date: str | None = None,
) -> Any:
    return SuccessResponse(
        data=await dashboard_detail(role, dimension, name, period, from_date, to_date)
    )


@WeidaRouter.get(
    "/{role}/dashboard",
    summary="工作台仪表盘（卡片 + 图表）",
    response_model=ResponseSchema[dict],
)
async def workbench_dashboard(
    role: str,
    period: str = "week",
    from_date: str | None = None,
    to_date: str | None = None,
    basis: str = "send_inquiry_time",
    group: str = "customer",
    trend_from: str | None = None,
    trend_to: str | None = None,
    owner: str | None = None,
) -> Any:
    wb = _require_workbench(role)
    return SuccessResponse(
        data=await get_dashboard(role, wb, period, from_date, to_date, basis, group, trend_from, trend_to, owner)
    )


@WeidaRouter.get(
    "/{role}/stats",
    summary="工作台仪表盘统计",
    response_model=ResponseSchema[dict],
)
async def workbench_stats(role: str) -> Any:
    """三个仪表盘要的关键数字（先给总量与关键分档）。"""
    wb = _require_workbench(role)
    if role == "customer_service":
        sql = """
        SELECT COUNT(*) AS total,
               SUM(CASE WHEN is_goods_received = '是' THEN 1 ELSE 0 END) AS done,
               SUM(CASE WHEN is_goods_received = '否' THEN 1 ELSE 0 END) AS pending
        FROM biz_opportunity WHERE is_deleted = 0
        """
        title = "商机转化"
        labels = {"total": "商机总数", "done": "已拿到货", "pending": "未拿到货"}
    elif role == "pricing":
        sql = """
        SELECT COUNT(*) AS total,
               SUM(CASE WHEN latest_quote_deadline IS NOT NULL
                         AND latest_quote_deadline >= NOW() THEN 1 ELSE 0 END) AS pending,
               SUM(CASE WHEN latest_quote_deadline IS NULL
                         OR latest_quote_deadline < NOW() THEN 1 ELSE 0 END) AS overdue
        FROM biz_opportunity WHERE is_deleted = 0
        """
        title = "待报价"
        labels = {"total": "商机总数", "pending": "未截止", "overdue": "已截止"}
    else:
        sql = """
        SELECT COUNT(*) AS total,
               SUM(CASE WHEN IFNULL(send_forecast,0)=1 THEN 1 ELSE 0 END) AS done,
               SUM(CASE WHEN IFNULL(send_forecast,0)=0
                         AND IFNULL(handover_domestic_operation,0)=1 THEN 1 ELSE 0 END) AS handed,
               SUM(CASE WHEN IFNULL(handover_domestic_operation,0)=0
                         AND IFNULL(send_forecast,0)=0 THEN 1 ELSE 0 END) AS pending
        FROM biz_order WHERE is_deleted = 0
        """
        title = "操作票量"
        labels = {"total": "订单总数", "done": "已完成", "handed": "已交接", "pending": "未交接"}

    async with async_db_session() as session:
        try:
            row = (await session.execute(text(sql))).mappings().first()
            data = {k: int(row[k] or 0) for k in row.keys()} if row else {}
        except Exception:
            data = {"total": 0}
    return SuccessResponse(
        data={"title": title, "labels": labels, "values": data, "table": wb.table}
    )
