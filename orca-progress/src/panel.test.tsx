import { assert, mockHost, mount, test } from 'orca/testing'
import * as panel from './panel'
test('read-only board renders disconnected state and refreshes', async () => {
 const host = mockHost({ requests: { 'progress.read': () => ({ checkedAt: '2026-11-16T00:00:00Z', runId: null, tasks: [], workers: [], gates: [], evidence: [], errors: [] }) } })
 const view = await mount(panel, { host })
 assert.ok(view.find({ text: '연결된 실행 없음' }))
 await view.press({ text: '새로고침' })
 assert.deepEqual(view.errors, [])
 assert.deepEqual(view.refusals, [])
 await view.unmount()
})

test('shows native task state and never treats errors as no workers', async () => {
 const host = mockHost({ requests: { 'progress.read': () => ({ checkedAt: '2026-11-16T00:00:00Z', runId: 'run_example', tasks: [{id:'task_a',title:'[2] 후보 A',status:'running'}], workers: [], gates: [], evidence: [{name:'A.oracle.golden.json',passed:2,failed:1,unchecked:1,oracle:true,modified:'2026-11-16T00:00:00Z'}], errors: ['workers: 연결 실패'] }) } })
 const view = await mount(panel, {host})
 assert.ok(view.find({text:'running'}))
 assert.ok(view.find({text:'조회 불가'}))
 assert.deepEqual(view.errors, [])
 await view.unmount()
})
