import { useState } from 'react'

// 데모: 저장 안 됨. 실제로는 Supabase 의 사용자 설정 테이블에 저장
export default function Settings() {
  const [connected, setConnected] = useState(true)

  return (
    <>
      <header className="page-head"><h1>설정<span className="tag">데모</span></h1></header>
      <p className="note page-note">데모 화면이라 바꾼 값은 저장되지 않아요.</p>

      <section className="panel settings">
        <table className="form-table">
          <tbody>
            <tr>
              <th scope="row">Gmail 연동</th>
              <td><div className="cell">
                {connected ? (
                  <>
                    <span>demo@gmail.example</span>
                    <span className="tag ro">읽기 전용</span>
                    <button type="button" className="btn" onClick={() => setConnected(false)}>연동 해제</button>
                  </>
                ) : (
                  <>
                    <span className="muted">연결된 계정이 없어요.</span>
                    <button type="button" className="btn primary" onClick={() => setConnected(true)}>Gmail 연결하기</button>
                  </>
                )}
              </div></td>
            </tr>
            <tr>
              <th scope="row">자동 검사</th>
              <td><label className="sw"><input type="checkbox" role="switch" defaultChecked />새 메일이 오면 바로 검사</label></td>
            </tr>
            <tr>
              <th scope="row"><label htmlFor="set-interval">검사 주기</label></th>
              <td><select id="set-interval" defaultValue="15">
                <option value="0">실시간</option>
                <option value="15">15분마다</option>
                <option value="60">1시간마다</option>
              </select></td>
            </tr>
            <tr>
              <th scope="row">위험 메일 알림</th>
              <td><label className="sw"><input type="checkbox" role="switch" defaultChecked />위험 메일을 찾으면 팝업 알림</label></td>
            </tr>
            <tr>
              <th scope="row">의심 메일 알림</th>
              <td><label className="sw"><input type="checkbox" role="switch" />의심 메일도 팝업 알림</label></td>
            </tr>
            <tr>
              <th scope="row"><label htmlFor="set-report">주간 리포트</label></th>
              <td><select id="set-report" defaultValue="weekly">
                <option value="off">받지 않음</option>
                <option value="weekly">매주 월요일</option>
              </select></td>
            </tr>
            <tr>
              <th scope="row"><label htmlFor="set-keep">기록 보관 기간</label></th>
              <td><select id="set-keep" defaultValue="90">
                <option value="30">30일</option>
                <option value="90">90일</option>
                <option value="365">1년</option>
              </select></td>
            </tr>
          </tbody>
        </table>
      </section>
    </>
  )
}
