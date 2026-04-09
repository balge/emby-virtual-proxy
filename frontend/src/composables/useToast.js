import { reactive } from 'vue'

const state = reactive({
  toasts: [],
  nextId: 0,
})

export function useToast() {
  const add = (message, type = 'info', duration = 3000) => {
    const id = state.nextId++
    state.toasts.push({ id, message, type })
    if (duration > 0) {
      setTimeout(() => remove(id), duration)
    }
  }

  const remove = (id) => {
    const idx = state.toasts.findIndex(t => t.id === id)
    if (idx !== -1) state.toasts.splice(idx, 1)
  }

  return {
    toasts: state.toasts,
    success: (msg) => add(msg, 'success'),
    error: (msg) => add(msg, 'error', 5000),
    warning: (msg) => add(msg, 'warning', 4000),
    /** @param {number} [durationMs] 显示毫秒数，默认 3000；RSS 等场景可传更长 */
    info: (msg, durationMs) => add(msg, 'info', durationMs ?? 3000),
    remove,
  }
}
