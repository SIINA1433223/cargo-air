#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
每小时从飞书多维表格拉取最新记录，注入 template.html 生成 index.html。
由 GitHub Actions 调用（见 .github/workflows/sync.yml）。
"""
import json
import os
import sys
import urllib.request
from datetime import datetime, timedelta, timezone

FEISHU_OPEN = "https://open.feishu.cn"
APP_TOKEN = os.environ.get("FEISHU_APP_TOKEN", "JrEmbA3QTataXcsw4mAc2svSnFd").strip()
TABLE_ID = os.environ.get("FEISHU_TABLE_ID", "tblnqJv9sVubmZoM").strip()
VIEW_ID = os.environ.get("FEISHU_VIEW_ID", "vewTrVS6cS").strip()


def http_json(url, method="GET", headers=None, body=None, timeout=30):
    req = urllib.request.Request(url, method=method)
    if headers:
        for k, v in headers.items():
            req.add_header(k, v)
    data = None
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        req.add_header("Content-Type", "application/json; charset=utf-8")
    with urllib.request.urlopen(req, data=data, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def get_token(app_id, app_secret):
    url = f"{FEISHU_OPEN}/open-apis/auth/v3/tenant_access_token/internal"
    r = http_json(url, method="POST", body={"app_id": app_id, "app_secret": app_secret})
    if r.get("code") != 0:
        raise RuntimeError(f"获取 tenant_access_token 失败: {r}")
    return r["tenant_access_token"]


def fetch_fields(token):
    url = f"{FEISHU_OPEN}/open-apis/bitable/v1/apps/{APP_TOKEN}/tables/{TABLE_ID}/fields"
    r = http_json(url, headers={"Authorization": f"Bearer {token}"})
    if r.get("code") != 0:
        raise RuntimeError(f"拉取字段失败: {r}")
    return r["data"].get("items", [])


def fetch_records(token):
    records = []
    page_token = None
    while True:
        url = f"{FEISHU_OPEN}/open-apis/bitable/v1/apps/{APP_TOKEN}/tables/{TABLE_ID}/records?page_size=500"
        if VIEW_ID:
            url += f"&view_id={VIEW_ID}"
        if page_token:
            url += f"&page_token={page_token}"
        r = http_json(url, headers={"Authorization": f"Bearer {token}"})
        if r.get("code") != 0:
            raise RuntimeError(f"拉取记录失败: {r}")
        data = r["data"]
        records.extend(data.get("items", []))
        if not data.get("has_more"):
            break
        page_token = data.get("page_token")
    return records


def build_index_html(records, fields):
    base = os.path.dirname(os.path.abspath(__file__))
    tpl_path = os.path.join(base, "..", "template.html")
    out_path = os.path.join(base, "..", "index.html")

    with open(tpl_path, "r", encoding="utf-8") as f:
        tpl = f.read()

    field_names = [f["field_name"] for f in fields]
    field_types = {f["field_name"]: f.get("type") for f in fields}
    embedded = {
        "mode": "live",
        "updated_at": datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%dT%H:%M:%S%z"),
        "fields": field_names,
        "field_types": field_types,
        "records": records,
    }

    real_js = json.dumps(embedded, ensure_ascii=False).replace("</", "<\\/")
    inject = f'<script>window.__EMBEDDED_DATA__={real_js};</script>\n'
    html = tpl.replace("</body>", inject + "</body>", 1)

    old = '  if(location.protocol.startsWith("http")){'
    new = '  if(window.__EMBEDDED_DATA__){ data = window.__EMBEDDED_DATA__; }\n  else if(location.protocol.startsWith("http")){'
    if old in html:
        html = html.replace(old, new, 1)

    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)
    return out_path


def main():
    app_id = os.environ.get("FEISHU_APP_ID", "").strip()
    app_secret = os.environ.get("FEISHU_APP_SECRET", "").strip()
    if not app_id or not app_secret:
        print("环境变量 FEISHU_APP_ID / FEISHU_APP_SECRET 未设置，跳过同步（保留现有 index.html）")
        return 0

    print(f"[{datetime.now()}] 开始同步飞书数据...")
    token = get_token(app_id, app_secret)
    fields = fetch_fields(token)
    records = fetch_records(token)
    out_path = build_index_html(records, fields)
    print(f"已生成 {out_path}：{len(records)} 条记录，{len(fields)} 个字段")
    return 0


if __name__ == "__main__":
    sys.exit(main())
