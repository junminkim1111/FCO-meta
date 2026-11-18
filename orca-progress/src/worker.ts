import type { OrcaWorkerApi } from 'orca/worker'
import { readFile, readdir, stat } from 'node:fs/promises'
import { execFile } from 'node:child_process'
import { promisify } from 'node:util'
import type { Snapshot, Evidence, Entry } from './protocol'

const ROOT = '/Users/junmin/fco-meta/reports'
const CLI = '/Applications/Orca.app/Contents/Resources/bin/orca'
const exec = promisify(execFile)

// No shell, mutation commands, mailbox consumption, model calls, or file writes.
async function list(command: string, runId: string): Promise<any> {
  const { stdout } = await exec(CLI, ['orchestration', command, '--run', runId, '--json'],
    { cwd: '/Users/junmin/fco-meta', timeout: 6000, maxBuffer: 1024 * 1024 })
  const response = JSON.parse(stdout)
  if (!response.ok) throw new Error(response.error?.message || command + ' 조회 실패')
  return response.result
}

export function summarize(name: string, data: any, modified: string): Evidence {
  if (typeof data.oracle !== 'boolean' || !Array.isArray(data.rows)) throw new Error(name + ': 결과 형식 불일치')
  let passed = 0, failed = 0, unchecked = 0
  for (const row of data.rows) {
    if (!Array.isArray(row.checks) || row.checks.some((c: any) => !Array.isArray(c) || typeof c[1] !== 'boolean'))
      throw new Error(name + ': checks 형식 불일치')
    if (!row.checks.length) unchecked++
    else if (row.checks.every((c: any) => c[1])) passed++
    else failed++
  }
  return { name, passed, failed, unchecked, oracle: data.oracle, modified }
}

const entry = (row: any): Entry => ({ id: String(row.id ?? row.dispatchId ?? row.taskId ?? '?'),
  title: String(row.title ?? row.displayName ?? row.agent ?? row.question ?? row.taskId ?? row.id ?? '이름 없음'),
  status: String(row.status ?? row.state ?? row.terminalState ?? '알 수 없음') })

export async function snapshot(): Promise<Snapshot> {
  const out: Snapshot = { checkedAt: new Date().toISOString(), runId: null, tasks: [], workers: [], gates: [], evidence: [], errors: [] }
  try {
    const binding = JSON.parse(await readFile(ROOT + '/orca/active.json', 'utf8'))
    if (typeof binding.runId !== 'string' || !/^run_[a-zA-Z0-9_-]+$/.test(binding.runId)) throw new Error('runId 형식 불일치')
    out.runId = binding.runId
  } catch (e: any) {
    if (e.code !== 'ENOENT') out.errors.push('실행 연결: ' + e.message)
  }
  if (out.runId) {
    await Promise.all((['task-list', 'worker-list', 'gate-list'] as const).map(async (command) => {
      const key = command === 'task-list' ? 'tasks' : command === 'worker-list' ? 'workers' : 'gates'
      try {
        const result = await list(command, out.runId!)
        if (!Array.isArray(result[key])) throw new Error('응답 형식 불일치')
        out[key] = result[key].map(entry)
        if (result.nextCursor || result.truncated) out.errors.push(key + ': 일부 결과만 표시됨')
      } catch (e: any) { out.errors.push(key + ': ' + e.message) }
    }))
  }
  // Only this bound run's evidence; never combine old runs with the current run.
  const dir = out.runId ? ROOT + '/orca/' + out.runId : ROOT
  try {
    const names = out.runId ? (await readdir(dir)).filter(n => n.endsWith('.golden.json')) : ['orca-setup-oracle.json']
    for (const name of names.sort()) {
      try {
        const path = dir + '/' + name
        const info = await stat(path)
        if (info.size > 8 * 1024 * 1024) throw new Error('결과 파일이 8MB를 초과함')
        out.evidence.push(summarize(name, JSON.parse(await readFile(path, 'utf8')), info.mtime.toISOString()))
      } catch (e: any) { out.errors.push(name + ': ' + e.message) }
    }
  } catch (e: any) { out.errors.push('검증 결과: ' + e.message) }
  return out
}

export default function activate(orca: OrcaWorkerApi): void {
  orca.panels.handle('progress.read', snapshot)
}
