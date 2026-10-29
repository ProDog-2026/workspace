import { registerHooks } from 'node:module'
import { existsSync, readFileSync } from 'node:fs'
import { transformSync } from 'rolldown/experimental'
// Node 22의 동기 로더가 React CJS를 부분 초기화하지 않도록 먼저 읽는다.
import 'react'

// 기존 Vite 컴파일러로 JSX를 읽는다. 테스트용 브라우저·새 패키지는 필요 없다.
registerHooks({
  resolve(specifier, context, nextResolve) {
    if (specifier.startsWith('.') && context.parentURL) {
      for (const extension of ['.js', '.jsx', '.tsx']) {
        const url = new URL(specifier + extension, context.parentURL)
        if (existsSync(url)) return nextResolve(url.href, context)
      }
    }
    return nextResolve(specifier, context)
  },
  load(url, context, nextLoad) {
    const file = new URL(url)
    if (/\.(jsx|tsx)$/.test(file.pathname)) return {
      format: 'module', shortCircuit: true,
      source: transformSync(file.pathname, readFileSync(file, 'utf8'), { jsx: { runtime: 'automatic' } }).code,
    }
    return nextLoad(url, context)
  },
})
