import { mock } from 'node:test'
import { renderToStaticMarkup } from 'react-dom/server'
import 'react/jsx-runtime'

// Run component effects and rerenders with the existing Node module mocks.
export function hooks(t) {
  const slots = [], pending = []
  let cursor = 0, tree
  mock.module('react', { namedExports: {
    useState(initial) {
      const index = cursor++
      if (!slots[index]) slots[index] = { value: typeof initial === 'function' ? initial() : initial }
      return [slots[index].value, (next) => { slots[index].value = typeof next === 'function' ? next(slots[index].value) : next }]
    },
    useRef(initial) {
      const index = cursor++
      if (!slots[index]) slots[index] = { current: initial }
      return slots[index]
    },
    useEffect(effect, deps) {
      const index = cursor++
      const previous = slots[index]
      if (!previous || deps.some((value, i) => !Object.is(value, previous.deps[i]))) {
        pending.push(() => {
          previous?.cleanup?.()
          slots[index] = { deps, cleanup: effect() }
        })
      }
    },
  } })
  t.after(() => {
    for (const slot of slots) slot.cleanup?.()
    mock.restoreAll()
  })
  return {
    render(Component, props) {
      cursor = 0
      tree = Component(props)
      pending.splice(0).forEach((effect) => effect())
      return tree
    },
    markup: () => renderToStaticMarkup(tree),
    flush: () => new Promise((resolve) => setImmediate(resolve)),
  }
}

export function find(tree, predicate) {
  if (Array.isArray(tree)) return tree.map((child) => find(child, predicate)).find(Boolean)
  if (!tree || typeof tree !== 'object') return undefined
  return predicate(tree) ? tree : find(tree.props?.children, predicate)
}
