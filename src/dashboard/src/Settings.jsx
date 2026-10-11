import { useState } from 'react'

// 데모: 저장 안 됨. 실제로는 Supabase 의 사용자 설정 테이블에 저장
export default function Settings() {
  const [connected, setConnected] = useState(true)

  return (
    <>
      <header className="top"><h1>설정</h1></header>

      <section className="card form">
        <h2>Gmail 연동</h2>
        {connected ? (
          <div className="row">
            <span>✓ demo@gmail.example 연결됨 <span className="muted small">· 권한: gmail.readonly</span></span>
            <button className="btn" onClick={() => setConnected(false)}>연동 해제</button>
          </div>
        ) : (
          <div className="row">
            <span className="muted">연결된 계정 없음</span>
            <button className="btn primary" onClick={() => setConnected(true)}>Gmail 연결</button>
          </div>
        )}
      </section>

      <section className="card form">
        <h2>자동 검사</h2>
        <label><input type="checkbox" defaultChecked /> 새 메일이 오면 자동으로 검사</label>
        <label>검사 주기
          <select defaultValue="15">
            <option value="0">실시간 (푸시 알림)</option>
            <option value="15">15분마다</option>
            <option value="60">1시간마다</option>
          </select>
        </label>
      </section>

      <section className="card form">
        <h2>알림</h2>
        <label><input type="checkbox" defaultChecked /> 위험 메일 탐지 시 팝업</label>
        <label><input type="checkbox" /> 의심 메일도 팝업</label>
        <label>주간 리포트 메일
          <select defaultValue="weekly">
            <option value="off">받지 않음</option>
            <option value="weekly">매주 월요일</option>
          </select>
        </label>
      </section>

      <section className="card form">
        <h2>데이터</h2>
        <label>검사 기록 보관 기간
          <select defaultValue="90">
            <option value="30">30일</option>
            <option value="90">90일</option>
            <option value="365">1년</option>
          </select>
        </label>
      </section>
    </>
  )
}
