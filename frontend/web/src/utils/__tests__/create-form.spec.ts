import { describe, expect, it } from 'vitest'
import type { CreateFormField } from '@/api/module_feishu/workbench'
import { seedFormValues } from '@/utils/create-form'

function field(name: string, defaults: string[], multiple = true): CreateFormField {
  return {
    name,
    title: name,
    required: false,
    description: '',
    type: 11,
    ui_type: 'User',
    control: 'user',
    editable: true,
    options: [],
    link_table_id: '',
    multiple,
    default: defaults,
  }
}

describe('seedFormValues', () => {
  it('多选字段原样铺成数组', () => {
    expect(seedFormValues([field('负责航线', ['ou_fairy', 'ou_kevin'])])).toEqual({
      负责航线: ['ou_fairy', 'ou_kevin'],
    })
  })

  it('单选字段存裸 id，不包成单元素数组', () => {
    // 后端对字符串会自己包一层，对数组原样透传——包了就会写成「两个人」
    expect(seedFormValues([field('商机负责人', ['ou_fairy'], false)])).toEqual({
      商机负责人: 'ou_fairy',
    })
  })

  it('没有预选值的字段不进 values', () => {
    // 进 values 就会被提交逻辑当成「用户填过」，也会让「没动过」和「清空了」分不出来
    expect(seedFormValues([field('负责航线', [])])).toEqual({})
  })

  it('后端没下发 default 键（老接口）也不炸', () => {
    const legacy = { ...field('负责航线', []) }
    delete (legacy as Partial<CreateFormField>).default

    expect(seedFormValues([legacy])).toEqual({})
  })

  it('改预选值不会回头改坏后端下发的定义', () => {
    const source = field('负责航线', ['ou_fairy'])
    const seed = seedFormValues([source])

    ;(seed['负责航线'] as string[]).push('ou_evil')

    expect(source.default).toEqual(['ou_fairy'])
  })
})
