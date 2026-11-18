export type Entry = { id: string; title: string; status: string }
export type Evidence = { name: string; passed: number; failed: number; unchecked: number; oracle: boolean; modified: string }
export type Snapshot = { checkedAt: string; runId: string | null; tasks: Entry[]; workers: Entry[]; gates: Entry[]; evidence: Evidence[]; errors: string[] }
declare module 'orca' {
  interface PluginRequests { 'progress.read': { output: Snapshot } }
}
