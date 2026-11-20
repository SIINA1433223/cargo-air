"""只读探针：真实飞书凭证下，详情分组是不是按预期切的那一刀。

对客方案那一组是**前端约定的锚点**（组名 == 关联字段名 → 铺在关联表格下方），
单元测试只能拿假字段表验算法，验不了「飞书表里这几个字段真在、名字没写错」。
名字写错的表现是分组为空 + 日志一条 warning，界面上什么都看不到，所以这里真调一次。
"""

from __future__ import annotations

import asyncio

import redis.asyncio as aioredis

from app.config.setting import settings
from app.plugin.module_feishu.service import FeishuBitableService

ROLE = "wd_customer_service"


async def main() -> None:
    redis = aioredis.from_url(settings.REDIS_URI, decode_responses=True)
    try:
        svc = FeishuBitableService(redis, ROLE)
        cfg = await svc.get_workbench_config(ROLE)

        print(f"=== {cfg.name} 详情分组（{len(cfg.detail_groups)} 组）===")
        for group in cfg.detail_groups:
            names = [m.name for m in group.fields]
            print(f"  [{group.label}] {len(names)} 个字段")
            if group.label == "对客方案":
                for meta in group.fields:
                    flag = "可编辑" if meta.editable else "只读"
                    link = f"  → {meta.link_table_id}" if meta.link_table_id else ""
                    print(f"      {meta.type:>5} {flag}  {meta.name}{link}")

        # 收尾那四个必须**不在**商机详情里了，否则同一格会出现两处
        shangji = {m.name for g in cfg.detail_groups if g.label == "商机详情" for m in g.fields}
        moved = ["最终对客方案", "确认订单", "是否拿到货", "未拿到货备注"]
        leaked = [n for n in moved if n in shangji]
        print(f"\n商机详情里残留的收尾字段: {leaked or '无 ✓'}")

        # 前端拿 link_table_id 把「最终对客方案」和下方那张对客方案表对上号
        by_name = {m.name: m for m in (cfg.detail_fields or [])}
        final = by_name.get("最终对客方案")
        duifang = by_name.get("对客方案")
        print(f"最终对客方案 → {final.link_table_id if final else '（字段不存在）'}")
        print(f"对客方案     → {duifang.link_table_id if duifang else '（字段不存在）'}")
        if final and duifang:
            print(f"两者指向同一张表（下拉候选据此配对）: {final.link_table_id == duifang.link_table_id}")
    finally:
        await redis.aclose()


if __name__ == "__main__":
    asyncio.run(main())
