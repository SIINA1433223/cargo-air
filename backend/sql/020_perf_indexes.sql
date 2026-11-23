-- 列表排序/筛选索引（2026-11-20）
CREATE INDEX idx_opp_deleted_received_no ON biz_opportunity (is_deleted, is_goods_received, opportunity_no);
CREATE INDEX idx_opp_deleted_deadline ON biz_opportunity (is_deleted, latest_quote_deadline);
CREATE INDEX idx_order_deleted_warehouse ON biz_order (is_deleted, warehouse_in_operation_time);
CREATE INDEX idx_order_deleted_status ON biz_order (is_deleted, send_forecast, handover_domestic_operation);
CREATE INDEX idx_order_deleted_priced ON biz_order (is_deleted, route_manager_priced);
CREATE INDEX idx_pricing_deleted ON biz_pricing (is_deleted);
