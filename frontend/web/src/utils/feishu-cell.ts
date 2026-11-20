import { FIELD_TYPE, type FieldMeta } from '@/api/module_feishu/workbench'

/**
 * 一格能改成哪种控件。
 *
 * 抽出来是因为**有两处要拿它做决定**：索引页/报价表格点开一格时挂哪个控件（`WeidaCellEditor`），
 * 以及「这一格点下去能不能变成编辑器」（格子该显示成纯文本还是可点）。两处各写一份判断，
 * 迟早出现「看起来能点、点下去是个死控件」。
 *
 * - 只读类型（公式/查找/自动编号/按钮）本来就改不了 → `plain`；
 * - **附件与关联虽然可写也不给格子**：一个要上传控件、一个要记录选择器，格子太窄放不下，
 *   统一留到详情页/展开体。
 */
export type CellKind = 'text' | 'number' | 'single' | 'multi' | 'date' | 'checkbox' | 'user' | 'plain'

export function cellKind(meta: FieldMeta): CellKind {
  if (!meta.editable) return 'plain'
  switch (meta.type) {
    case FIELD_TYPE.TEXT:
    case FIELD_TYPE.PHONE:
      return 'text'
    case FIELD_TYPE.NUMBER:
      return 'number'
    case FIELD_TYPE.SINGLE_SELECT:
      return 'single'
    case FIELD_TYPE.MULTI_SELECT:
      return 'multi'
    case FIELD_TYPE.DATE:
      return 'date'
    case FIELD_TYPE.CHECKBOX:
      return 'checkbox'
    case FIELD_TYPE.USER:
      return 'user'
    default:
      return 'plain'
  }
}
