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
    info: (msg) => add(msg, 'info'),
    remove,
  }
}
