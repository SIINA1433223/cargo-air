import type { CreateFormField } from '@/api/module_feishu/workbench'

/**
 * 表单定义里的预选值 → 初始 `values`。
 *
 * 后端只给配了 `user_defaults` 的字段下发 `default`（如「负责航线」默认那两位），
 * 其余一律是空数组。空数组的字段**不放进 values**：提交时 `isEmpty` 那一关要额外判断，
 * 而且「用户压根没动过」与「用户主动清空了」两件事会分不出来。
 *
 * 单选字段存**裸 id 而不是单元素数组**，得和后端 `denormalize_field_value` 认的形态对上
 * ——那一步对字符串会自己包一层，对数组则原样透传。
 */
export function seedFormValues(fields: CreateFormField[]): Record<string, unknown> {
  const seed: Record<string, unknown> = {}
  for (const field of fields) {
    if (!field.default?.length) continue
    seed[field.name] = field.multiple ? [...field.default] : field.default[0]
  }
  return seed
}
