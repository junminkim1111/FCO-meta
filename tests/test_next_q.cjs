// web-ui.js의 NEXT_Q/splitNext를 소스에서 꺼내 검사한다 (브라우저·API 없음). 실행: node tests/test_next_q.cjs
const assert = require('node:assert/strict');
const src = require('node:fs').readFileSync(__dirname + '/../fco_meta/web/static/web-ui.js', 'utf8');
const code = src.split('\n').filter(l => /^const (NEXT_Q|splitNext) /.test(l)).join('\n');
const splitNext = new Function(code + '; return splitNext;')();
assert.deepEqual(splitNext('충분합니다.\n다음 질문: 4-2-3-1로도 짜줘'), ['충분합니다.', '4-2-3-1로도 짜줘']);
assert.deepEqual(splitNext('충분합니다.\n\n**다음 질문: 4-2-3-1로도 짜줘**'), ['충분합니다.\n', '4-2-3-1로도 짜줘']);
assert.deepEqual(splitNext('예산 안에서 충분히 구성됩니다. **다음 질문: 남은 예산까지 써서 더 비싸게 짜줘**'), ['예산 안에서 충분히 구성됩니다.', '남은 예산까지 써서 더 비싸게 짜줘']);
assert.deepEqual(splitNext('다음 질문: 앞\n뒤에 줄이 더 있음'), ['다음 질문: 앞\n뒤에 줄이 더 있음', null]);
console.log('ok');
