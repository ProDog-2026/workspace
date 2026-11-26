import { useEffect } from 'react'
import './Guide.css'

const STEPS = [
  ['Google 계정 연결', '로그인에서 Google 계정을 선택하고 Gmail 읽기 권한을 허용해요. 연결한 계정은 화면 왼쪽 아래에서 확인할 수 있어요.', '#/login', 'Google 계정으로 시작하기'],
  ['받은 메일 불러오기', '대시보드를 열면 최근 받은 메일 20건을 불러와요. 아래의 더 불러오기로 다음 메일을 가져올 수 있어요. 판정이나 기간을 눌러 목록을 좁혀 보세요.', '#/', '대시보드 열기'],
  ['메일을 골라 결과 확인', '목록에서 메일 제목을 누르면 상세 화면이 열려요. 판정, 점수, 찾은 부분과 검사하지 못한 부분을 함께 확인하세요. 근거가 없거나 검사가 끝나지 않았다고 안전한 것은 아니에요.', '#/states', '판정 안내 보기'],
]

export default function Guide() {
  useEffect(() => {
    const previous = document.title
    document.title = '사용법 | ProDog'
    return () => { document.title = previous }
  }, [])
  return <article className="guide" aria-labelledby="guide-title">
    <header className="guide-header">
      <p className="guide-eyebrow">처음 사용하는 분께</p>
      <h1 id="guide-title">메일을 AI에게 맡기기 전에,<br />숨은 지시부터 확인해요.</h1>
      <p>메일 속에는 AI에게 원래 지시를 무시하거나 정보를 밖으로 보내라고 시키는 문장이 숨어 있을 수 있어요. ProDog는 이런 메일을 찾아 확인할 수 있도록 도와줘요.</p>
    </header>

    <section aria-labelledby="guide-start">
      <h2 id="guide-start">Gmail 연결부터 첫 결과까지</h2>
      <ol className="guide-steps">{STEPS.map(([title, body, href, action], index) => <li key={title}>
        <span className="guide-number" aria-hidden="true">{index + 1}</span>
        <h3>{title}</h3><p>{body}</p><a href={href}>{action} <span aria-hidden="true">→</span></a>
      </li>)}</ol>
    </section>

    <section className="guide-reading" aria-labelledby="guide-results">
      <h2 id="guide-results">결과는 이렇게 읽어요</h2>
      <dl>
        <div><dt>위험 · 의심</dt><dd>AI에게 요약이나 답장을 맡기기 전에 상세 화면에서 이유를 확인하세요. 삭제나 스팸 신고는 Gmail에서 직접 해야 해요.</dd></div>
        <div><dt>안전</dt><dd>검사한 범위에서 숨은 명령을 찾지 못했다는 뜻이에요. 모든 공격을 탐지하거나 메일 전체의 안전을 보장하지는 않아요.</dd></div>
        <div><dt>검사 전 · 실패 · 일부만 검사</dt><dd>아직 판단할 수 없거나 확인하지 못한 부분이 있어요. 안전으로 받아들이지 말고 상태와 빠진 부분을 확인하세요.</dd></div>
      </dl>
    </section>

    <section className="guide-reading" aria-labelledby="guide-dynamic">
      <h2 id="guide-dynamic">동적 검사는 별도 준비가 필요해요</h2>
      <p>정적 검사에서 의심·위험으로 분류된 메일은 상세 화면에서 동적 검사를 시작할 수 있어요. 동적 검사 페이지에서 Ollama 주소와 모델을 선택하고 연결·도구 호출 확인을 진행하세요. 동적 검사가 실패해도 메일 상세에서 정적 판정을 볼 수 있어요.</p>
      <a href="#/dynamic">동적 검사 설정 열기 <span aria-hidden="true">→</span></a>
    </section>

    <aside className="guide-limit" aria-labelledby="guide-limits">
      <h2 id="guide-limits">사용 전에 알아두세요</h2>
      <ul><li>Gmail 읽기 권한으로 메일을 가져와요. 메일을 자동으로 삭제하거나 차단하지 않아요.</li><li>PDF 첨부파일에서 추출한 텍스트의 검사 결과는 메일 본문 판정과 따로 표시해요. 다른 형식의 첨부파일은 검사에서 제외해요.</li><li>설정 화면의 자동 검사·알림·보관 기간 등은 데모 항목으로 저장되지 않아요. 동적 검사 설정은 동적 검사 페이지에서 관리해요.</li><li>계정 연결 해제는 설정의 연동 해제에서 할 수 있어요.</li></ul>
      <a href="#/settings">연결 관리 열기 <span aria-hidden="true">→</span></a>
    </aside>
  </article>
}
