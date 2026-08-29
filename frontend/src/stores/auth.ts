import { defineStore } from 'pinia'
import {
  fetchMe,
  login as apiLogin,
  register as apiRegister,
} from '@/api/auth'
import type { AuthMeOut, LoginIn, RegisterIn } from '@/api/types'

interface AuthState {
  token: string
  expiresAt: number        // ms timestamp，前端按此登出
  username: string
  nickname: string
  role: 'student'          // 本项目无教师端，角色恒为 student（兼容旧视图引用）
}

const TOKEN_KEY = 'token'
const NICK_KEY = 'nickname'
const USER_KEY = 'username'
const EXP_KEY = 'token_expires_at'

/** 旧版本可能把 undefined 序列化成字符串存进 localStorage，读取时清洗掉。 */
function cleanStored(key: string): string {
  const v = localStorage.getItem(key) || ''
  return v === 'undefined' || v === 'null' ? '' : v
}

export const useAuthStore = defineStore('auth', {
  state: (): AuthState => ({
    token: localStorage.getItem(TOKEN_KEY) || '',
    expiresAt: Number(localStorage.getItem(EXP_KEY) || '0'),
    username: cleanStored(USER_KEY),
    nickname: cleanStored(NICK_KEY),
    role: 'student',
  }),
  getters: {
    isAuthed: (s) => {
      if (!s.token) return false
      if (s.expiresAt && s.expiresAt < Date.now()) return false
      return true
    },
  },
  actions: {
    persist() {
      if (this.token) {
        localStorage.setItem(TOKEN_KEY, this.token)
        localStorage.setItem(EXP_KEY, String(this.expiresAt))
      } else {
        localStorage.removeItem(TOKEN_KEY)
        localStorage.removeItem(EXP_KEY)
      }
      // 空值直接移除，避免落进 "undefined" 字符串
      if (this.nickname) localStorage.setItem(NICK_KEY, this.nickname)
      else localStorage.removeItem(NICK_KEY)
      if (this.username) localStorage.setItem(USER_KEY, this.username)
      else localStorage.removeItem(USER_KEY)
    },
    async login(payload: LoginIn) {
      const res = await apiLogin(payload)
      this.token = res.token
      this.expiresAt = Date.now() + res.expires_in * 1000
      this.nickname = res.nickname || ''
      this.username = res.username || ''
      this.persist()
    },
    async register(payload: RegisterIn) {
      // 注册后端已自动签 token，自动登录
      const res = await apiRegister(payload)
      this.token = res.token
      this.expiresAt = Date.now() + res.expires_in * 1000
      this.nickname = res.nickname || ''
      this.username = res.username || ''
      this.persist()
    },
    async refresh() {
      // 拿一下自己的 profile，确保 token 仍然有效
      const me: AuthMeOut = await fetchMe()
      this.nickname = me.nickname || ''
      this.username = me.username || ''
      this.persist()
    },
    logout() {
      this.token = ''
      this.expiresAt = 0
      this.username = ''
      this.nickname = ''
      this.persist()
    },
  },
})
