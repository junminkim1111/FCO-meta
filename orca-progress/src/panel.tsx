import { atom, Badge, Button, Heading, plugin, read, Row, set, Stack, Text, type PanelContext } from 'orca'
import type { Snapshot } from './protocol'
const data = atom<Snapshot | null>(null)
const error = atom('')
let busy = false
async function refresh() {
  if (busy) return
  busy = true
  try { set(data, await plugin.request('progress.read')); set(error, '') }
  catch (e) { set(error, String(e)) }
  finally { busy = false }
}
export function setup(ctx: PanelContext) {
  void refresh()
  const timer = setInterval(() => void refresh(), 10000)
  ctx.subscriptions.push({ dispose: () => clearInterval(timer) })
}
const stages = ['정리', '설계', '상호 반박', '설계 보고', '구현', '테스트', '결과 보고', '머지·교훈']
export default function Panel() {
  const s = read(data)
  return <Stack gap="md">
    <Row justify="between"><Heading>하네스 진행</Heading><Badge>읽기 전용</Badge></Row>
    <Row justify="between"><Text muted>fco-meta · 10초마다 조회</Text><Button variant="ghost" onPress={refresh}>새로고침</Button></Row>
    {read(error) ? <Text>조회 실패 · 아래 내용은 마지막 성공 시점의 정보입니다. {read(error)}</Text> : null}
    {!s ? <Text>ORCA 상태를 확인하고 있습니다.</Text> : <Stack gap="md">
      <Heading>{s.runId ? '연결된 실행' : '연결된 실행 없음'}</Heading>
      <Text>{s.runId || '원하는 수정사항을 오케스트레이터에게 전달하세요. 버그 수정·기능 추가·리팩터링 등 요청에 맞춰 진행합니다. 실행이 시작되면 Run ID를 연결합니다.'}</Text>
      <Text muted size="xs">마지막 조회 {new Date(s.checkedAt).toLocaleTimeString('ko-KR')}</Text>
      {s.errors.map((e) => <Text key={e}>확인 필요 · {e}</Text>)}
      <Heading>단계</Heading>
      {stages.map((label, i) => {
        const tasks = s.tasks.filter(t => t.title.startsWith(`[${i}]`))
        return <Stack key={label} gap="xs"><Row justify="between"><Text>{i}. {label}</Text><Badge>{tasks.length ? tasks.map(t => t.status).join(' / ') : s.errors.some(e => e.startsWith('tasks:')) ? '알 수 없음' : '미등록'}</Badge></Row>
          {tasks.map(t => <Text key={t.id} muted size="xs">{t.title} · {t.id}</Text>)}
        </Stack>
      })}
      <Heading>작업자 · ORCA 원본 상태</Heading>
      {s.workers.length ? s.workers.map(w => <Text key={w.id}>{w.title} · {w.status}</Text>) : <Text muted>{s.errors.some(e => e.startsWith('workers:')) ? '조회 불가' : '등록된 작업자 없음'}</Text>}
      <Heading>승인 기록</Heading>
      {s.gates.length ? s.gates.map(g => <Text key={g.id}>{g.title} · {g.status}</Text>) : <Text muted>{s.errors.some(e => e.startsWith('gates:')) ? '조회 불가' : '등록된 승인 기록 없음'}</Text>}
      <Heading>{s.runId ? '검증 결과' : '설정 검증 · 실제 후보 평가 아님'}</Heading>
      {s.evidence.map(e => <Stack key={e.name} gap="xs"><Text>{e.name}</Text><Text>통과 {e.passed} · 실패 {e.failed} · 미검증 {e.unchecked}</Text><Text muted size="xs">{e.oracle ? '도구 조건만 검사 · 모델 답변 미검증' : '실제 모델 실행 결과'} · {new Date(e.modified).toLocaleString('ko-KR')}</Text></Stack>)}
      {!s.evidence.length ? <Text muted>저장된 검증 결과 없음</Text> : null}
      <Text muted size="xs">Task 완료는 검증 통과를 뜻하지 않습니다. 이 보드는 승인·실행·머지하거나 모델을 호출하지 않습니다.</Text>
    </Stack>}
  </Stack>
}
