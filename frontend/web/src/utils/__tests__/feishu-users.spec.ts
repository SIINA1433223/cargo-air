/**
 * 人员候选的进程内缓存。
 *
 * 这段逻辑本身很小，但错法都很隐蔽：缓存了失败会让下拉整个会话都是空的、
 * 存结果而不是存 Promise 会让同一帧挂载的几个人员字段各发一次请求。都钉在这里。
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'

const { getUserOptions } = vi.hoisted(() => ({ getUserOptions: vi.fn() }))

vi.mock('@/api/module_feishu/workbench', () => ({ default: { getUserOptions } }))

const FAIRY = { open_id: 'ou_fairy', name: 'Fairy Li' }

function ok(users: unknown[]) {
  return { data: { data: users } }
}

async function load() {
  const { loadUserOptions } = await import('@/utils/feishu-users')
  return loadUserOptions
}

beforeEach(async () => {
  getUserOptions.mockReset()
  const { invalidateUserOptions } = await import('@/utils/feishu-users')
  invalidateUserOptions()
})

describe('loadUserOptions', () => {
  it('同一工作台只拉一次', async () => {
    getUserOptions.mockResolvedValue(ok([FAIRY]))
    const loadUserOptions = await load()

    const a = await loadUserOptions('wd_customer_service')
    const b = await loadUserOptions('wd_customer_service')

    expect(a).toEqual([FAIRY])
    expect(b).toEqual([FAIRY])
    expect(getUserOptions).toHaveBeenCalledTimes(1)
  })

  it('不同工作台各拉各的', async () => {
    getUserOptions.mockResolvedValue(ok([FAIRY]))
    const loadUserOptions = await load()

    await loadUserOptions('wd_customer_service')
    await loadUserOptions('customer_service')

    expect(getUserOptions).toHaveBeenCalledTimes(2)
  })

  it('同帧并发只发一个在途请求', async () => {
    // 缓存的是 Promise，后来者接到的是同一个在途请求，而不是各自再发一次
    getUserOptions.mockReturnValue(new Promise((resolve) => setTimeout(() => resolve(ok([FAIRY])), 0)))
    const loadUserOptions = await load()

    const [a, b, c] = await Promise.all([
      loadUserOptions('wd_customer_service'),
      loadUserOptions('wd_customer_service'),
      loadUserOptions('wd_customer_service'),
    ])

    expect([a, b, c]).toEqual([[FAIRY], [FAIRY], [FAIRY]])
    expect(getUserOptions).toHaveBeenCalledTimes(1)
  })

  it('失败不进缓存_下次照常重试', async () => {
    getUserOptions.mockRejectedValueOnce(new Error('network'))
    const loadUserOptions = await load()

    expect(await loadUserOptions('wd_customer_service')).toEqual([])
    // 一次网络抖动不该让人员下拉在整个会话里永远是空的
    getUserOptions.mockResolvedValue(ok([FAIRY]))
    expect(await loadUserOptions('wd_customer_service')).toEqual([FAIRY])
    expect(getUserOptions).toHaveBeenCalledTimes(2)
  })

  it('invalidateUserOptions_能只清一个工作台', async () => {
    getUserOptions.mockResolvedValue(ok([FAIRY]))
    const { invalidateUserOptions } = await import('@/utils/feishu-users')
    const loadUserOptions = await load()

    await loadUserOptions('wd_customer_service')
    await loadUserOptions('customer_service')
    invalidateUserOptions('wd_customer_service')
    await loadUserOptions('wd_customer_service')
    await loadUserOptions('customer_service')

    // 清掉的那个重拉，没清的接着用缓存
    expect(getUserOptions).toHaveBeenCalledTimes(3)
  })
})
