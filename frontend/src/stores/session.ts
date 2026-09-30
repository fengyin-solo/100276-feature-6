import { defineStore } from 'pinia'

export const useSessionStore = defineStore('session', {
  state: () => ({
    operator: '李秀英',
    shiftLabel: '白班 08:00-20:00',
    scope: '港口集装箱作业管理平台',
  }),
  getters: {
    canOperate: (state) => state.operator.length > 0,
  },
  actions: {
    setOperator(name: string) {
      this.operator = name
    },
    setShift(label: string) {
      this.shiftLabel = label
    },
  },
})
